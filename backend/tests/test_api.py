import pytest
from fastapi.testclient import TestClient

from simulator.samples import SAMPLES

SRC = SAMPLES['Hazard showcase']


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'DB_PATH', tmp_path / 'test.db')
    return TestClient(app_module.app)


def test_health_and_samples(client):
    assert client.get('/api/health').json()['status'] == 'ok'
    assert 'Hazard showcase' in client.get('/api/samples').json()
    assert client.get('/api/config/default').json()['width'] == 2


def test_ui_is_served(client):
    r = client.get('/')
    assert r.status_code == 200 and 'Superscalar' in r.text


def test_assemble(client):
    r = client.post('/api/assemble', json={'source': SRC}).json()
    assert len(r['prog']) == 8
    assert {e['type'] for e in r['graph']['edges']} == {'RAW', 'WAR', 'WAW'}


def test_simulate(client):
    r = client.post('/api/simulate', json={'source': SRC, 'config': {'width': 4, 'rename': True}})
    assert r.status_code == 200
    d = r.json()
    assert d['cfg']['width'] == 4 and d['sim']['committed'] == 8
    assert len(d['sim']['snaps']) == d['sim']['cycles'] + 1
    assert [x['w'] for x in d['compare']['widths']] == [1, 2, 4, 8]


def test_simulate_summary_only(client):
    d = client.post('/api/simulate', json={'source': SRC, 'compare': False, 'snapshots': False}).json()
    assert 'snaps' not in d['sim'] and 'compare' not in d and d['sim']['cycles'] > 0


def test_errors(client):
    r = client.post('/api/simulate', json={'source': 'ADD R1, R2\nFOO'})
    assert r.status_code == 422 and len(r.json()['detail']['errors']) == 2
    assert client.post('/api/simulate', json={'source': SRC, 'config': {'width': 99}}).status_code == 422
    assert client.post('/api/simulate', json={'source': SRC, 'config': {'bp': 'magic'}}).status_code == 422


def test_compare(client):
    d = client.post('/api/compare', json={'source': SRC}).json()
    assert d['current']['cycles'] > 0 and d['widths'][0]['speedup'] == 1


def test_report_csv(client):
    r = client.post('/api/report', json={'source': SRC})
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/csv')
    assert 'Issue width' in r.text and 'LW R1, 0(R0)' in r.text


def test_experiments_crud(client):
    a = client.post('/api/experiments', json={'name': 'baseline', 'source': SRC, 'config': {'width': 1}})
    assert a.status_code == 201
    b = client.post('/api/experiments', json={'name': '4-wide', 'source': SRC, 'config': {'width': 4}}).json()
    assert [x['name'] for x in client.get('/api/experiments').json()] == ['4-wide', 'baseline']
    full = client.get(f"/api/experiments/{b['id']}").json()
    assert full['source'] == SRC and full['result']['summary']['cycles'] == b['cycles']
    assert client.delete(f"/api/experiments/{b['id']}").status_code == 204
    assert client.get(f"/api/experiments/{b['id']}").status_code == 404
