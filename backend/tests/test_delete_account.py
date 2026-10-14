import json

import pytest
from fastapi.testclient import TestClient

import config
import db as D

H = {"X-Requested-With": "FinanceDesk"}


@pytest.fixture
def c(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", False)
    import app as appmod
    with TestClient(appmod.app) as client:
        yield client


def test_delete_account_takes_everything_with_it_and_stays_gone(c):
    chk = c.post("/api/accounts", json={"name": "Old Bank", "kind": "checking"}, headers=H).json()
    other = c.post("/api/accounts", json={"name": "Keep", "kind": "savings"}, headers=H).json()
    t = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-09-01", "amount_cents": -5000, "payee": "X", "category_id": 102, "business": 1}, headers=H).json()
    o = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-09-02", "amount_cents": -10000, "payee": "TRANSFER", "reviewed": False}, headers=H).json()
    i = c.post("/api/transactions", json={"account_id": other["id"], "date": "2026-09-02", "amount_cents": 10000, "payee": "TRANSFER", "reviewed": False}, headers=H).json()
    c.post("/api/transfers", json={"out_txn_id": o["id"], "in_txn_id": i["id"]}, headers=H)
    c.post("/api/attachments", files={"file": ("r.pdf", b"%PDF", "application/pdf")}, data={"transaction_id": str(t["id"])}, headers=H)
    fp = c.get("/api/accounts/%d/footprint" % chk["id"]).json()
    # linking the transfer marked both sides reviewed, so 2 reviewed
    assert fp == {"name": "Old Bank", "synced": False, "transactions": 2, "reviewed": 2, "attachments": 1, "trades": 0, "transfers": 1}
    assert c.delete("/api/accounts/%d?confirm=wrong" % chk["id"], headers=H).status_code == 400
    r = c.delete("/api/accounts/%d?confirm=Old%%20Bank" % chk["id"], headers=H)
    assert r.status_code == 200 and r.json()["removed"] == {"transactions": 2, "attachments": 1, "trades": 0}
    assert [a["name"] for a in c.get("/api/accounts").json()] == ["Keep"]
    # the other side of the transfer is still there, now unlinked
    got = c.get("/api/transactions/%d" % i["id"]).json()
    assert got["transfer_id"] is None
    assert c.get("/api/system/integrity").json()["ok"]

    # a synced account is remembered so the next feed does not recreate it
    with D.write() as w:
        w.execute("INSERT INTO accounts (name, kind, simplefin_id, created_at) VALUES ('Feed', 'checking', 'ACT-CHK-1', 'x')")
        aid = D.scalar(w, "SELECT id FROM accounts WHERE simplefin_id = 'ACT-CHK-1'")
    assert c.delete("/api/accounts/%d?confirm=Feed" % aid, headers=H).json()["ignored_simplefin_id"] == "ACT-CHK-1"
    with D.read() as conn:
        assert json.loads(D.scalar(conn, "SELECT value FROM settings WHERE key = 'ignored_simplefin_ids'")) == ["ACT-CHK-1"]
