"""The API through starlette's TestClient. Auth stays ON here so the setup
gate and the CSRF header are what is being tested; TestClient requests arrive
from 'testclient', which is not localhost, unless we say otherwise."""

import pytest
from fastapi.testclient import TestClient

import auth
import config

H = {"X-Requested-With": "FinanceDesk"}


@pytest.fixture
def client(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", True)
    import app as appmod
    with TestClient(appmod.app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def local_client(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", True)
    import app as appmod
    with TestClient(appmod.app, base_url="http://testserver", client=("127.0.0.1", 50000)) as c:
        yield c


def test_health_is_open_and_api_is_closed(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["setup_mode"] is True
    assert client.get("/api/system/jobs").status_code == 401


def test_setup_refused_from_the_network(client):
    assert client.get("/api/setup").json()["allowed"] is False
    r = client.post("/api/setup", json={"name": "owner", "password": "longenough"}, headers=H)
    assert r.status_code == 403
    assert not auth.any_users()


def test_setup_then_login_then_csrf(local_client):
    c = local_client
    assert c.get("/api/setup").json()["allowed"] is True
    r = c.post("/api/setup", json={"name": "owner", "password": "longenough"}, headers=H)
    assert r.status_code == 200 and auth.any_users()
    # second setup is closed even from localhost
    assert c.post("/api/setup", json={"name": "x", "password": "longenough"}, headers=H).status_code == 403
    # the setup response logged us in
    assert c.get("/api/me").json()["name"] == "owner"
    # a POST without the header is refused even with a valid cookie
    assert c.post("/api/system/backup-key/acknowledge").status_code == 403
    assert c.post("/api/system/backup-key/acknowledge", headers=H).status_code == 200
    assert c.get("/api/system/backup-key/fingerprint").json()["acknowledged"] is True
    # logout, then wrong password
    c.post("/api/logout", headers=H)
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"name": "owner", "password": "wrong-one"}, headers=H).status_code == 401
    assert c.post("/api/login", json={"name": "owner", "password": "longenough"}, headers=H).status_code == 200
    assert c.get("/api/system/jobs").status_code == 200
