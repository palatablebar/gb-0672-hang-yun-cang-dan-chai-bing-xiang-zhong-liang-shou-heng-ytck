from fastapi.testclient import TestClient
from app import main


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'test.sqlite'))
    return TestClient(main.app)


def _run(c, dataset='clean'):
    return c.post('/api/runs', json={'dataset': dataset}).json()


def test_withdrawal_page_and_entry_registered(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = _run(c)
    rid = run['manifest']['run_id']
    page = c.get(f'/withdrawal?run_id={rid}')
    assert page.status_code == 200
    assert '撤关证明' in page.text
    # 导航栏包含第 8 模块
    assert f'href="/withdrawal?run_id={rid}"' in page.text


def test_create_withdrawal_certificate(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = _run(c)['manifest']['run_id']
    resp = c.post(f'/api/withdrawal/{rid}', json={'instruction_ref': 'WDL-X-001'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['instruction_ref'] == 'WDL-X-001'
    assert len(data['rulings']) == 3
    assert data['attempts'] == 1
    seals = [r['certificate_seal'] for r in data['rulings']]
    assert all(seals)


def test_retry_same_instruction_keeps_single_result(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = _run(c)['manifest']['run_id']
    first = c.post(f'/api/withdrawal/{rid}', json={'instruction_ref': 'WDL-X-002'}).json()
    second = c.post(f'/api/withdrawal/{rid}', json={'instruction_ref': 'WDL-X-002'}).json()
    # 重试不新增第二套结果：裁决封印完全一致，仅尝试次数累加
    assert [r['certificate_seal'] for r in first['rulings']] == \
           [r['certificate_seal'] for r in second['rulings']]
    assert first['attempts'] == 1 and second['attempts'] == 2

    retry = c.post(f'/api/withdrawal/{rid}/WDL-X-002/retry').json()
    assert retry['idempotent'] is True
    assert retry['attempts'] == 3

    listing = c.get(f'/api/withdrawal/{rid}').json()['withdrawals']
    assert len(listing) == 1  # 同一指令始终只有一套结果


def test_distinct_instructions_kept_separately(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = _run(c)['manifest']['run_id']
    c.post(f'/api/withdrawal/{rid}', json={'instruction_ref': 'WDL-A'})
    c.post(f'/api/withdrawal/{rid}', json={'instruction_ref': 'WDL-B'})
    listing = c.get(f'/api/withdrawal/{rid}').json()['withdrawals']
    assert {w['instruction_ref'] for w in listing} == {'WDL-A', 'WDL-B'}


def test_failed_ruling_persists_and_retry_preserves_it(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = _run(c)['manifest']['run_id']
    data = c.post(f'/api/withdrawal/{rid}', json={}).json()
    failed = [r for r in data['rulings'] if r['status'] == '确认失败']
    assert failed
    master = failed[0]['master_id']
    retained_before = failed[0]['retained_balance']
    seal_before = failed[0]['certificate_seal']

    again = c.post(f'/api/withdrawal/{rid}', json={}).json()
    again_failed = next(r for r in again['rulings'] if r['master_id'] == master)
    # 确认失败重试：旧版余额和原证明保留，不产生第二套结果
    assert again_failed['retained_balance'] == retained_before
    assert again_failed['certificate_seal'] == seal_before
    assert again_failed['status'] == '确认失败'


def test_get_unknown_withdrawal_404(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    rid = _run(c)['manifest']['run_id']
    assert c.get(f'/api/withdrawal/{rid}/NOPE').status_code == 404


def test_withdrawal_keeps_original_run_manifest_unchanged(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    run = _run(c)
    rid = run['manifest']['run_id']
    digest_before = run['manifest']['sha256']
    c.post(f'/api/withdrawal/{rid}', json={})
    after = c.get(f'/api/runs/{rid}').json()
    # 撤关是旁路，原批次封存摘要与视图集合不变
    assert after['manifest']['sha256'] == digest_before
    assert 'withdrawal' not in after['views']
