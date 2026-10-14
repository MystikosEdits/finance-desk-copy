import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import coinbase as CB
import db
import money


def _pem():
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                             serialization.NoEncryption()).decode()


def test_jwt_shape():
    tok = CB.build_jwt("organizations/o/apiKeys/k", _pem(), "GET", "/v2/accounts")
    h, p, s = tok.split(".")
    pad = lambda x: x + "=" * (-len(x) % 4)
    header = json.loads(base64.urlsafe_b64decode(pad(h)))
    payload = json.loads(base64.urlsafe_b64decode(pad(p)))
    assert header["alg"] == "ES256" and header["kid"] == "organizations/o/apiKeys/k" and len(header["nonce"]) == 32
    assert payload["iss"] == "cdp" and payload["uri"] == "GET api.coinbase.com/v2/accounts" and payload["exp"] - payload["nbf"] == 120
    assert len(base64.urlsafe_b64decode(pad(s))) == 64      # raw r||s, not DER


def test_trade_mapping():
    buy = {"id": "b1", "type": "buy", "status": "completed", "amount": {"amount": "0.05", "currency": "BTC"},
           "native_amount": {"amount": "4050.00", "currency": "USD"}, "created_at": "2026-02-01T10:00:00Z", "details": {"title": "Bought BTC"}}
    r = CB._trade_from(buy, 1, 1)
    assert r["kind"] == "buy" and r["qty_e8"] == money.to_e8("0.05") and r["total_cents"] == -405000 and r["price_e8"] == money.to_e8("81000")
    sell = dict(buy, id="s1", type="sell", amount={"amount": "-0.01", "currency": "BTC"}, native_amount={"amount": "-900.00", "currency": "USD"})
    r = CB._trade_from(sell, 1, 1)
    assert r["kind"] == "sell" and r["total_cents"] == 90000 and r["price_e8"] == money.to_e8("90000")
    reward = dict(buy, id="r1", type="staking_reward", amount={"amount": "0.001", "currency": "ETH"}, native_amount={"amount": "2.50", "currency": "USD"})
    r = CB._trade_from(reward, 1, 1)
    assert r["kind"] == "transfer_in" and r["cost_basis_cents"] == 250
    send = dict(buy, id="x1", type="send", amount={"amount": "-0.002", "currency": "BTC"}, native_amount={"amount": "-160.00", "currency": "USD"})
    assert CB._trade_from(send, 1, 1)["kind"] == "transfer_out"
    assert CB._trade_from(dict(buy, status="failed"), 1, 1) is None


def test_run_sync_writes_trades_once_and_reconciles(conn, clock, monkeypatch):
    import secrets_store
    monkeypatch.setattr(secrets_store, "get", lambda name: json.dumps({"key_name": "organizations/o/apiKeys/k", "private_key": _pem()}))
    monkeypatch.setattr(secrets_store, "exists", lambda name: True)
    wallets = [{"id": "w-btc", "currency": {"code": "BTC"}, "balance": {"amount": "0.06"}},
               {"id": "w-usd", "currency": {"code": "USD"}, "balance": {"amount": "12.34"}},
               {"id": "w-doge", "currency": {"code": "DOGE"}, "balance": {"amount": "0"}}]
    txs = {"w-btc": [{"id": "b1", "type": "buy", "status": "completed", "amount": {"amount": "0.05", "currency": "BTC"},
                      "native_amount": {"amount": "4050.00", "currency": "USD"}, "created_at": "2026-02-01T10:00:00Z"}],
           "w-usd": [], "w-doge": []}
    monkeypatch.setattr(CB, "fetch_accounts", lambda creds: wallets)
    monkeypatch.setattr(CB, "fetch_transactions", lambda creds, aid: txs[aid])
    import prices
    monkeypatch.setattr(prices, "coins_list", lambda key=None: [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"}])
    res = CB.run_sync()
    assert res["trades_inserted"] == 1 and res["adjustments"] == 1 and res["wallets"] == 2
    acct = db.row(conn, "SELECT * FROM accounts WHERE institution = 'Coinbase'")
    assert acct["kind"] == "crypto"
    inst = db.row(conn, "SELECT * FROM instruments WHERE symbol = 'BTC'")
    assert inst["coingecko_id"] == "bitcoin"
    trades = db.rows(conn, "SELECT * FROM trades WHERE account_id = ? ORDER BY id", (acct["id"],))
    assert [t["kind"] for t in trades] == ["buy", "transfer_in"] and trades[1]["qty_e8"] == money.to_e8("0.01")
    assert db.scalar(conn, "SELECT balance_cents FROM account_balances WHERE account_id = ?", (acct["id"],)) == 1234
    # second run: nothing new, and the adjustment is rewritten not duplicated
    res = CB.run_sync()
    assert res["trades_inserted"] == 0
    assert db.scalar(conn, "SELECT COUNT(*) FROM trades WHERE account_id = ?", (acct["id"],)) == 2
    assert db.scalar(conn, "SELECT COUNT(*) FROM accounts WHERE institution = 'Coinbase'") == 1
