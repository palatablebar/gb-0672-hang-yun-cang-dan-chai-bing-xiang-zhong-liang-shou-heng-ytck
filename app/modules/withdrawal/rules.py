from decimal import Decimal
from copy import deepcopy
import hashlib, json

QTY_FIELDS = ('pieces', 'net_kg', 'gross_kg')
ZERO = {f: Decimal('0') for f in QTY_FIELDS}


def _q(value):
    """统一把件数/重量证据值规范成 Decimal，件数为整数语义。"""
    return Decimal(str(value))


def qty(pieces, net_kg, gross_kg):
    return {'pieces': _q(pieces), 'net_kg': _q(net_kg), 'gross_kg': _q(gross_kg)}


def add(a, b):
    return {f: a[f] + b[f] for f in QTY_FIELDS}


def sub(a, b):
    return {f: a[f] - b[f] for f in QTY_FIELDS}


def as_str(q):
    out = {}
    for f in QTY_FIELDS:
        v = q[f]
        out[f] = str(int(v)) if f == 'pieces' else format(v, 'f')
    return out


def seal(payload):
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def snapshot_seal(snapshot):
    """原放行快照封印：恢复时必须能对回同一快照，防止依据被偷换。"""
    return seal({'id': snapshot['id'], 'released': snapshot['released'], 'release_ref': snapshot['release_ref']})


def validate_qty(q, where):
    for f in QTY_FIELDS:
        if not q[f].is_finite() or q[f] < 0:
            raise ValueError(f'{where} 存在负向或非法数量')
    if q['pieces'] % 1:
        raise ValueError(f'{where} 件数必须为整数')
    if q['gross_kg'] < q['net_kg']:
        raise ValueError(f'{where} 毛重不得低于净重')


# ---------------------------------------------------------------------------
# 有效证据：补正 / 拆并 / 换箱
# 台账只追加不改写。每个事件带来源证据号，按序重算当前有效余额与来源覆盖。
# ---------------------------------------------------------------------------
def apply_amendment(balance, event):
    kind = event['kind']
    delta = qty(event['pieces'], event['net_kg'], event['gross_kg'])
    if kind == 'correction':      # 舱单补正：直接增减
        nxt = add(balance, delta)
    elif kind == 'split':         # 拆箱：本主单拆出，余额减少
        nxt = sub(balance, delta)
    elif kind == 'merge':         # 并箱：并入本主单，余额增加
        nxt = add(balance, delta)
    elif kind == 'restow':        # 换箱：货量不变，只换箱号
        nxt = dict(balance)
    else:
        raise ValueError(f'不支持的台账事件类型: {kind}')
    validate_qty(nxt, f"事件 {event.get('evidence_ref')} 生效后")
    return nxt


def effective_balance(snapshot, amendments):
    """从原放行快照出发逐事件重算，返回当前有效余额、来源覆盖量、逐事件轨迹与失衡事件。

    覆盖量指当前仍在账、且有来源证据对应的货量，因此恒不超过有效余额：
    补正/并入增加在账货量则同步增加覆盖，拆出减少在账货量则同步减少覆盖，
    换箱货量不变、覆盖量不变（只留下箱号轨迹）。
    任一事件导致负向/越界余额，记入 broken_ref 并停止后续重算（账实已不能守恒）。
    """
    balance = qty(**snapshot['released'])
    covered = dict(ZERO)
    trace = []
    broken_ref = None
    for ev in amendments:
        before = dict(balance)
        try:
            balance = apply_amendment(balance, ev)
        except ValueError:
            broken_ref = ev.get('evidence_ref')
            trace.append({'evidence_ref': ev.get('evidence_ref'), 'kind': ev.get('kind'),
                          'container': ev.get('container', ''), 'before': as_str(before),
                          'after': as_str(before)})
            break
        delta = qty(ev['pieces'], ev['net_kg'], ev['gross_kg'])
        if ev['kind'] in ('correction', 'merge'):
            covered = add(covered, delta)
        elif ev['kind'] == 'split':
            covered = sub(covered, delta)
        # restow：货量与覆盖量均不变，仅在 trace 留箱号变更痕
        covered = {f: max(ZERO[f], min(covered[f], balance[f])) for f in QTY_FIELDS}
        trace.append({'evidence_ref': ev['evidence_ref'], 'kind': ev['kind'],
                      'container': ev.get('container', ''), 'before': as_str(before),
                      'after': as_str(balance)})
    return balance, covered, trace, broken_ref


def prove_balance(snapshot, amendments):
    """余额证明：重算不出现负向/越界即账实可守恒；同时核对来源证据齐备且覆盖不越界。"""
    balance, covered, trace, broken_ref = effective_balance(snapshot, amendments)
    balance_ok = broken_ref is None and all(balance[f] >= 0 for f in QTY_FIELDS)
    source_ok = bool(amendments) and all(ev.get('evidence_ref') for ev in amendments)
    if source_ok and balance_ok:
        source_ok = all(covered[f] <= balance[f] for f in QTY_FIELDS)
    return balance_ok, balance, covered, trace, broken_ref


# ---------------------------------------------------------------------------
# 撤关裁决
# ---------------------------------------------------------------------------
def restore(snapshot, amendments, instruction):
    """
    返回撤关裁决。是否恢复、恢复多少，取决于余额与来源能否同时证明：
      - 整票可证明（证据链覆盖到当前全部有效余额）→ 按原放行快照恢复整票
      - 仅部分可证明 → 只恢复有证据对应的余额
      - 两者不能同时证明 → 确认失败，保留旧版余额与原证明
    """
    result = {
        'instruction_ref': instruction['instruction_ref'],
        'master_id': snapshot['id'],
        'release_ref': snapshot['release_ref'],
        'snapshot_seal': snapshot_seal(snapshot),
        'mode': None, 'status': None,
        'released_snapshot': as_str(qty(**snapshot['released'])),
        'effective_balance': None, 'restored': as_str(dict(ZERO)),
        'retained_balance': None, 'evidence_refs': [],
        'trace': [], 'reason_codes': [],
    }
    balance_ok, balance, covered, trace, broken_ref = prove_balance(snapshot, amendments)
    result['trace'] = trace
    result['evidence_refs'] = [ev['evidence_ref'] for ev in amendments]
    # 旧版余额：失衡时取最后一条合法轨迹的在账余额，否则取重算余额
    last_good = trace[-1]['before'] if broken_ref and trace else None

    if not balance_ok:
        # 余额无法守恒证明：不能依据快照恢复，旧版余额与原证明原样保留
        retained = last_good if last_good is not None else as_str(qty(**snapshot['released']))
        result['mode'] = 'NONE'
        result['status'] = '确认失败'
        result['effective_balance'] = retained
        result['retained_balance'] = retained
        result['reason_codes'] = ['BALANCE_UNPROVEN'] + (['BROKEN_LEDGER:' + str(broken_ref)] if broken_ref else [])
        return result

    result['effective_balance'] = as_str(balance)
    if not amendments or not all(str(ev.get('evidence_ref', '')).strip() for ev in amendments):
        # 无来源证据或证据号缺失：来源不能证明，即便余额可守恒也不得恢复
        result['mode'] = 'NONE'
        result['status'] = '确认失败'
        result['retained_balance'] = as_str(balance)
        result['reason_codes'] = ['SOURCE_UNPROVEN']
        return result

    full = all(covered[f] >= balance[f] for f in QTY_FIELDS)
    if full:
        # 余额与来源同时证明且覆盖整票：依据原放行快照恢复整票
        result['mode'] = 'FULL_SNAPSHOT'
        result['status'] = '已恢复'
        result['restored'] = as_str(qty(**snapshot['released']))
        result['retained_balance'] = as_str(dict(ZERO))
        result['reason_codes'] = ['FULL_RESTORE']
        return result

    if any(covered[f] > 0 for f in QTY_FIELDS):
        # 仅部分有证据：只恢复证据对应的余额，未证明部分保留
        restored = {f: min(covered[f], balance[f]) for f in QTY_FIELDS}
        result['mode'] = 'PARTIAL_EVIDENCE'
        result['status'] = '部分恢复'
        result['restored'] = as_str(restored)
        result['retained_balance'] = as_str(sub(balance, restored))
        result['reason_codes'] = ['PARTIAL_RESTORE', 'SOURCE_PARTIAL']
        return result

    result['mode'] = 'NONE'
    result['status'] = '确认失败'
    result['retained_balance'] = as_str(balance)
    result['reason_codes'] = ['SOURCE_UNPROVEN']
    return result


def ruling_seal(ruling):
    """撤关证明封印：同一指令+快照+证据链必产生同一封印，支撑幂等留痕。"""
    return seal({k: ruling[k] for k in (
        'instruction_ref', 'master_id', 'snapshot_seal', 'mode', 'status',
        'restored', 'retained_balance', 'evidence_refs')})


# ---------------------------------------------------------------------------
# 工作台装配
# ---------------------------------------------------------------------------
def build_scenarios(state):
    """从当前批次主单派生三个可对照的撤关现场（全量/部分/失败）。

    覆盖量只由带来源证据的台账事件证明，原放行快照本身不计来源覆盖。
    """
    roots = state['graph']['roots']
    rows = {r['id']: r for r in state['records']}
    scenarios = []

    def snap(master):
        r = rows[master]
        return {'id': master, 'release_ref': f'RLS-{master}',
                'released': {'pieces': r['pieces'], 'net_kg': r['net_kg'], 'gross_kg': r['gross_kg']},
                'container': r['container']}

    # 场景一：拆出全部原货再并回（补正+拆并+换箱齐活），证据链覆盖当前全部有效余额 → 按快照整票恢复
    if roots:
        m = roots[0]; s = snap(m); rel = s['released']
        amendments = [
            {'evidence_ref': f'EV-{m}-S1', 'kind': 'split', 'container': s['container'], **rel},
            {'evidence_ref': f'EV-{m}-M1', 'kind': 'merge', 'container': s['container'] + '-N', **rel},
            {'evidence_ref': f'EV-{m}-R1', 'kind': 'restow', 'container': s['container'] + '-X',
             'pieces': 0, 'net_kg': 0, 'gross_kg': 0},
        ]
        scenarios.append({'master_id': m, 'snapshot': s, 'amendments': amendments,
                          'instruction_ref': f'WDL-{m}-001', 'expect': 'FULL_SNAPSHOT'})

    # 场景二：先拆出一半，再只对留在主单的一半补来源证据 → 覆盖部分有效余额，部分恢复
    if len(roots) > 1:
        m = roots[1]; s = snap(m)
        half = {'pieces': int(Decimal(s['released']['pieces']) / 2),
                'net_kg': format(Decimal(s['released']['net_kg']) / 2, 'f'),
                'gross_kg': format(Decimal(s['released']['gross_kg']) / 2, 'f')}
        amendments = [
            {'evidence_ref': f'EV-{m}-S1', 'kind': 'split', 'container': s['container'], **half},
            {'evidence_ref': f'EV-{m}-C1', 'kind': 'correction', 'container': s['container'], **half},
        ]
        scenarios.append({'master_id': m, 'snapshot': s, 'amendments': amendments,
                          'instruction_ref': f'WDL-{m}-001', 'expect': 'PARTIAL_EVIDENCE'})

    # 场景三：补正扣减超过在账货量，重算出现负余额（账实不能守恒）→ 确认失败，保留旧版余额与原证明
    if len(roots) > 2:
        m = roots[2]; s = snap(m)
        over = {'pieces': s['released']['pieces'] + 10,
                'net_kg': format(Decimal(s['released']['net_kg']) + 100, 'f'),
                'gross_kg': format(Decimal(s['released']['gross_kg']) + 100, 'f')}
        amendments = [
            {'evidence_ref': f'EV-{m}-C9', 'kind': 'correction', 'container': s['container'],
             **{f: ('-' + str(over[f])) for f in QTY_FIELDS}},
        ]
        scenarios.append({'master_id': m, 'snapshot': s, 'amendments': amendments,
                          'instruction_ref': f'WDL-{m}-001', 'expect': 'NONE'})
    return scenarios


def apply(state, payload=None):
    payload = payload or {}
    instruction_ref = str(payload.get('instruction_ref') or 'WDL-BATCH-001')
    scenarios = build_scenarios(state)
    rulings = []
    for sc in scenarios:
        ruling = restore(deepcopy(sc['snapshot']), deepcopy(sc['amendments']),
                         {'instruction_ref': instruction_ref})
        ruling['expect'] = sc['expect']
        ruling['certificate_seal'] = ruling_seal(ruling)
        rulings.append(ruling)

    state['withdrawal'] = {
        'instruction_ref': instruction_ref,
        'rulings': rulings,
        'restored_masters': [r['master_id'] for r in rulings if r['status'] == '已恢复'],
        'partial_masters': [r['master_id'] for r in rulings if r['status'] == '部分恢复'],
        'failed_masters': [r['master_id'] for r in rulings if r['status'] == '确认失败'],
    }
    state['views']['withdrawal'] = {
        'title': '撤关证明',
        'rows': [{k: v for k, v in r.items() if k not in ('trace',)} for r in rulings],
        'metrics': {
            '撤关指令': instruction_ref,
            '整票恢复': len(state['withdrawal']['restored_masters']),
            '部分恢复': len(state['withdrawal']['partial_masters']),
            '确认失败': len(state['withdrawal']['failed_masters']),
        },
    }
    return state
