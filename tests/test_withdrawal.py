from app.modules.withdrawal.rules import (
    restore, effective_balance, prove_balance, ruling_seal, snapshot_seal,
    build_scenarios, apply, qty,
)
from copy import deepcopy


def snapshot(master='M1', pieces=40, net='400', gross='440'):
    return {'id': master, 'release_ref': f'RLS-{master}',
            'released': {'pieces': pieces, 'net_kg': net, 'gross_kg': gross},
            'container': 'C1'}


def test_full_snapshot_restore_when_evidence_covers_whole_balance():
    s = snapshot()
    rel = s['released']
    amendments = [
        {'evidence_ref': 'EV-S1', 'kind': 'split', 'container': 'C1', **rel},
        {'evidence_ref': 'EV-M1', 'kind': 'merge', 'container': 'C1-N', **rel},
        {'evidence_ref': 'EV-R1', 'kind': 'restow', 'container': 'C1-X',
         'pieces': 0, 'net_kg': 0, 'gross_kg': 0},
    ]
    r = restore(deepcopy(s), amendments, {'instruction_ref': 'WDL-1'})
    assert r['status'] == '已恢复'
    assert r['mode'] == 'FULL_SNAPSHOT'
    # 恢复量必须等于原放行快照，而不是台账中间值
    assert r['restored'] == {'pieces': '40', 'net_kg': '400', 'gross_kg': '440'}
    assert r['retained_balance'] == {'pieces': '0', 'net_kg': '0', 'gross_kg': '0'}
    # 原放行记录与来源留痕
    assert r['release_ref'] == 'RLS-M1'
    assert r['evidence_refs'] == ['EV-S1', 'EV-M1', 'EV-R1']
    assert len(r['trace']) == 3


def test_partial_restore_only_evidence_backed_balance():
    s = snapshot()
    half = {'pieces': '20', 'net_kg': '200', 'gross_kg': '220'}
    amendments = [
        {'evidence_ref': 'EV-S1', 'kind': 'split', 'container': 'C1', 'pieces': 20, 'net_kg': '200', 'gross_kg': '220'},
        {'evidence_ref': 'EV-C1', 'kind': 'correction', 'container': 'C1', 'pieces': 20, 'net_kg': '200', 'gross_kg': '220'},
    ]
    r = restore(deepcopy(s), amendments, {'instruction_ref': 'WDL-2'})
    assert r['status'] == '部分恢复'
    assert r['mode'] == 'PARTIAL_EVIDENCE'
    assert r['restored'] == half
    # 未被证据覆盖的另一半保留为有效余额
    assert r['retained_balance'] == half
    assert 'SOURCE_PARTIAL' in r['reason_codes']


def test_failure_keeps_old_balance_and_original_proof_when_ledger_breaks():
    s = snapshot()
    amendments = [
        {'evidence_ref': 'EV-C9', 'kind': 'correction', 'container': 'C1',
         'pieces': -50, 'net_kg': '-500', 'gross_kg': '-540'},
    ]
    r = restore(deepcopy(s), amendments, {'instruction_ref': 'WDL-3'})
    assert r['status'] == '确认失败'
    assert r['mode'] == 'NONE'
    assert r['restored'] == {'pieces': '0', 'net_kg': '0', 'gross_kg': '0'}
    # 旧版余额保留为失衡前在账量（即原放行量），原快照封印不变
    assert r['retained_balance'] == {'pieces': '40', 'net_kg': '400', 'gross_kg': '440'}
    assert r['snapshot_seal'] == snapshot_seal(s)
    assert r['reason_codes'][0] == 'BALANCE_UNPROVEN'
    assert any(c.startswith('BROKEN_LEDGER') for c in r['reason_codes'])


def test_no_source_evidence_fails_even_when_balance_equals_snapshot():
    # 余额本身可证明，但没有任何来源证据 → 来源不能同时证明，不得整票恢复
    r = restore(snapshot(), [], {'instruction_ref': 'WDL-4'})
    assert r['status'] == '确认失败'
    assert r['mode'] == 'NONE'
    assert r['reason_codes'] == ['SOURCE_UNPROVEN']
    assert r['retained_balance'] == {'pieces': '40', 'net_kg': '400', 'gross_kg': '440'}


def test_missing_evidence_ref_blocks_source_proof():
    s = snapshot()
    amendments = [
        {'evidence_ref': '', 'kind': 'merge', 'container': 'C1',
         'pieces': 40, 'net_kg': '400', 'gross_kg': '440'},
    ]
    balance_ok, balance, covered, trace, broken = prove_balance(s, amendments)
    assert balance_ok is True  # 余额账实守恒
    r = restore(deepcopy(s), amendments, {'instruction_ref': 'WDL-5'})
    assert r['status'] == '确认失败'
    assert 'SOURCE_UNPROVEN' in r['reason_codes']


def test_restow_keeps_quantity_and_leaves_container_trace():
    s = snapshot()
    rel = s['released']
    amendments = [
        {'evidence_ref': 'EV-S1', 'kind': 'split', 'container': 'C1', **rel},
        {'evidence_ref': 'EV-M1', 'kind': 'merge', 'container': 'C1-N', **rel},
        {'evidence_ref': 'EV-R1', 'kind': 'restow', 'container': 'C1-X',
         'pieces': 0, 'net_kg': 0, 'gross_kg': 0},
    ]
    balance, covered, trace, broken = effective_balance(s, amendments)
    assert broken is None
    # 换箱不动货量
    assert trace[-1]['before'] == trace[-1]['after']
    assert trace[-1]['container'] == 'C1-X'
    assert str(balance['pieces']) == '40'


def test_ruling_is_deterministic_and_seal_stable():
    s = snapshot()
    rel = s['released']
    amendments = [
        {'evidence_ref': 'EV-S1', 'kind': 'split', 'container': 'C1', **rel},
        {'evidence_ref': 'EV-M1', 'kind': 'merge', 'container': 'C1-N', **rel},
    ]
    a = restore(deepcopy(s), deepcopy(amendments), {'instruction_ref': 'WDL-6'})
    b = restore(deepcopy(s), deepcopy(amendments), {'instruction_ref': 'WDL-6'})
    assert ruling_seal(a) == ruling_seal(b)
    # 不同指令产生不同证明封印
    c = restore(deepcopy(s), deepcopy(amendments), {'instruction_ref': 'WDL-OTHER'})
    assert ruling_seal(a) != ruling_seal(c)


def test_split_cannot_cover_more_than_effective_balance():
    s = snapshot()
    # 拆出后再补正少量，覆盖量只能对应当前在账货量
    amendments = [
        {'evidence_ref': 'EV-S1', 'kind': 'split', 'container': 'C1',
         'pieces': 30, 'net_kg': '300', 'gross_kg': '330'},
        {'evidence_ref': 'EV-C1', 'kind': 'correction', 'container': 'C1',
         'pieces': 5, 'net_kg': '50', 'gross_kg': '55'},
    ]
    balance_ok, balance, covered, trace, broken = prove_balance(s, amendments)
    assert balance_ok and broken is None
    assert all(covered[f] <= balance[f] for f in covered)
    r = restore(deepcopy(s), amendments, {'instruction_ref': 'WDL-7'})
    assert r['mode'] == 'PARTIAL_EVIDENCE'
    assert r['restored'] == {'pieces': '5', 'net_kg': '50', 'gross_kg': '55'}

def test_apply_builds_three_rulings_for_each_dataset(sample, clean):
    for state in (sample, clean):
        apply(state)
        w = state['withdrawal']
        modes = {r['master_id']: r['mode'] for r in w['rulings']}
        assert 'FULL_SNAPSHOT' in modes.values()
        assert 'PARTIAL_EVIDENCE' in modes.values()
        assert 'NONE' in modes.values()
        assert len(w['failed_masters']) >= 1
        # 视图指标与裁决一致
        assert state['views']['withdrawal']['metrics']['确认失败'] == len(w['failed_masters'])


def test_apply_does_not_mutate_original_balance_totals(sample):
    before = deepcopy(sample['balance'])
    apply(sample)
    assert sample['balance'] == before  # 撤关旁路不回写原批次对账量


def test_build_scenarios_reference_real_roots(clean):
    scenarios = build_scenarios(clean)
    roots = set(clean['graph']['roots'])
    assert {sc['master_id'] for sc in scenarios} <= roots
    for sc in scenarios:
        r = restore(deepcopy(sc['snapshot']), deepcopy(sc['amendments']),
                    {'instruction_ref': sc['instruction_ref']})
        assert r['mode'] == sc['expect']
