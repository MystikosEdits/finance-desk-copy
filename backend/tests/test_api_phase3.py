import pytest
from fastapi.testclient import TestClient

import config

H = {"X-Requested-With": "FinanceDesk"}


@pytest.fixture
def c(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", False)
    import app as appmod
    with TestClient(appmod.app) as client:
        yield client


def test_instruments_trades_holdings_networth(c):
    brk = c.post("/api/accounts", json={"name": "Fidelity", "kind": "brokerage"}, headers=H).json()
    cb = c.post("/api/accounts", json={"name": "Coinbase", "kind": "crypto"}, headers=H).json()
    assert c.post("/api/instruments", json={"symbol": "btc", "kind": "crypto"}, headers=H).status_code == 400
    vti = c.post("/api/instruments", json={"symbol": "vti", "kind": "etf", "name": "Vanguard Total"}, headers=H).json()
    btc = c.post("/api/instruments", json={"symbol": "BTC", "kind": "crypto", "coingecko_id": "bitcoin"}, headers=H).json()
    assert vti["symbol"] == "VTI"
    t1 = c.post("/api/trades", json={"account_id": brk["id"], "instrument_id": vti["id"], "date": "2025-01-10", "kind": "buy", "qty": "10", "price": "250"}, headers=H).json()
    assert t1["total_cents"] == -250000
    c.post("/api/trades", json={"account_id": brk["id"], "instrument_id": vti["id"], "date": "2026-01-10", "kind": "buy", "qty": "10", "price": "300"}, headers=H)
    c.post("/api/trades", json={"account_id": cb["id"], "instrument_id": btc["id"], "date": "2026-02-01", "kind": "buy", "qty": "0.05", "price": "80000", "fee_cents": 500}, headers=H)
    c.post("/api/prices/%d/manual" % vti["id"], json={"price": "310"}, headers=H)
    c.post("/api/prices/%d/manual" % btc["id"], json={"price": "90000"}, headers=H)
    h = c.get("/api/holdings").json()
    pos = {p["symbol"]: p for p in h["positions"]}
    assert pos["VTI"]["qty_e8"] == 20 * 10**8 and pos["VTI"]["value_cents"] == 620000 and pos["VTI"]["unrealized_cents"] == 620000 - 550000
    assert pos["BTC"]["value_cents"] == 450000 and pos["BTC"]["cost_cents"] == 400500
    assert h["total_value_cents"] == 1070000 and not pos["VTI"]["price_stale"]
    assert abs(pos["VTI"]["weight"] - 620000 / 1070000) < 1e-6
    # sell 15 VTI FIFO, then specific lot
    sell = c.post("/api/trades", json={"account_id": brk["id"], "instrument_id": vti["id"], "date": "2026-06-01", "kind": "sell", "qty": "15", "price": "320"}, headers=H).json()
    r = c.get("/api/realized?year=2026").json()
    assert r["rows"][0]["cost_cents"] == 250000 + 150000 and r["short_term_cents"] + r["long_term_cents"] == 480000 - 400000
    c.post("/api/trades/%d/allocate" % sell["id"], json={"lots": {str(t1["id"]): "5"}}, headers=H)
    r2 = c.get("/api/realized?year=2026").json()
    assert r2["rows"][0]["cost_cents"] == 125000 + 300000
    # net worth: brokerage from holdings, not from a bank balance
    c.post("/api/accounts/%d/balance" % brk["id"], json={"balance_cents": 1}, headers=H)
    now = c.get("/api/networth/now").json()
    acc = {a["name"]: a for a in now["accounts"]}
    assert acc["Fidelity"]["source"] == "holdings" and acc["Fidelity"]["value_cents"] == 5 * 31000
    snap = c.post("/api/networth/snapshot", headers=H).json()
    assert snap["net_cents"] == now["net_cents"]
    assert c.get("/api/networth").json()[0]["net_cents"] == snap["net_cents"]
    assert c.get("/api/reports/summary").json()["net_worth"]["source"] == "snapshot"
    assert c.get("/api/prices/%d" % vti["id"]).json()[0]["source"] == "manual"


def test_broker_csv_import_creates_instruments(c):
    brk = c.post("/api/accounts", json={"name": "Fidelity", "kind": "brokerage"}, headers=H).json()
    data = b"Run Date,Action,Symbol,Quantity,Price ($),Amount ($)\n09/02/2026,YOU BOUGHT,VTI,10,280.50,-2805.00\n09/04/2026,YOU SOLD,VTI,-2,300.00,600.00\n"
    pv = c.post("/api/imports/broker-csv/preview", files={"file": ("f.csv", data, "text/csv")}, data={"account_id": str(brk["id"])}, headers=H).json()
    assert pv["would_insert"] == 2 and pv["new_symbols"] == ["VTI"]
    import json
    r = c.post("/api/imports/broker-csv", files={"file": ("f.csv", data, "text/csv")},
               data={"account_id": str(brk["id"]), "profile": json.dumps(pv["profile"]), "symbol_kinds": json.dumps({"VTI": {"kind": "etf"}})}, headers=H).json()
    assert r["inserted"] == 2
    assert c.get("/api/instruments").json()[0]["kind"] == "etf"
    assert c.get("/api/holdings").json()["positions"][0]["qty_e8"] == 8 * 10**8
    r = c.post("/api/imports/broker-csv", files={"file": ("f.csv", data, "text/csv")},
               data={"account_id": str(brk["id"]), "profile": json.dumps(pv["profile"])}, headers=H)
    assert r.status_code == 409
