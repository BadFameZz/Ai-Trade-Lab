import pytest

from aitra.config import Config
from aitra.web import create_app

TOKEN = "t" * 32


@pytest.fixture
def client(tmp_path):
    app = create_app(Config(100, 10, 2, 50, tmp_path, TOKEN))
    return app.test_client()


def test_status_and_health(client):
    s = client.get("/api/status").get_json()
    assert s["mode"] == "PAPER" and s["live_locked"] and s["equity"] == 100
    h = client.get("/api/health")
    assert h.status_code == 200 and h.get_json()["database"] == "ok"


def test_initial_decision_persisted(client):
    d = client.get("/api/decisions").get_json()
    assert d[0]["symbol"] == "SYSTEM" and d[0]["action"] == "WAIT"


def test_risk_check_requires_token(client):
    r = client.post("/api/risk/check", json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 5})
    assert r.status_code == 401


def test_risk_check_journals(client):
    h = {"X-Admin-Token": TOKEN}
    ok = client.post("/api/risk/check", headers=h, json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 8, "confidence": 71}).get_json()
    bad = client.post("/api/risk/check", headers=h, json={"symbol": "BTCUSDC", "action": "BUY", "position_pct": 15}).get_json()
    assert ok["approved"] and not bad["approved"] and bad["code"] == "MAX_POSITION"
    assert len(client.get("/api/decisions").get_json()) == 3


def test_kill_switch_flow(client):
    assert client.post("/api/kill-switch/engage").get_json()["kill_switch"]
    assert client.get("/api/status").get_json()["risk"] == "BLOCKED"
    assert client.post("/api/kill-switch/release").status_code == 401
    assert not client.post("/api/kill-switch/release", headers={"X-Admin-Token": TOKEN}).get_json()["kill_switch"]


def test_persistence_across_restart(tmp_path):
    cfg = Config(100, 10, 2, 50, tmp_path, TOKEN)
    create_app(cfg).test_client().post("/api/kill-switch/engage")
    assert create_app(cfg).test_client().get("/api/status").get_json()["kill_switch"] is True
