"""Coinbase (the consumer app) via its v2 REST API with a CDP API key.

WHAT YOU NEED FROM COINBASE
  Developer Platform -> API keys -> create key with the ECDSA (ES256)
  algorithm and only the "wallet:accounts:read" and "wallet:transactions:read"
  permissions. You get a key NAME like organizations/<org>/apiKeys/<id> and
  an EC PRIVATE KEY in PEM. Both are stored here with DPAPI, never in the DB.

HOW A CALL IS SIGNED
  Every request carries a fresh JWT (2-minute life): header alg ES256, kid =
  key name, nonce; payload iss "cdp", sub = key name, nbf, exp, and
  uri = "GET api.coinbase.com/v2/accounts". Signed with the EC key; the DER
  signature is converted to the raw r||s form JWTs use. No PyJWT needed -
  `cryptography` is already a dependency.

WHAT IS PULLED
  /v2/accounts (one per currency wallet) and each wallet's /v2/accounts/{id}/
  transactions. Transactions become trades in ONE app account ("Coinbase",
  kind crypto): buy/sell as buy/sell, send/receive as transfer_out/in,
  rewards and interest as transfer_in with the USD value as basis. The
  Coinbase transaction id is the trade's external_id, so re-syncing never
  duplicates. If the wallet balance still disagrees with the computed
  holdings (history that predates the key, or a type this code does not
  know), an adjusting transfer is written, dated today and named as such,
  so net worth is right even when cost basis is not.
"""

import base64
import json
import os
import secrets as pysecrets
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import config
import db
import log
import money
import secrets_store

logger = log.get("coinbase")
HOST = "api.coinbase.com"
BASE = "https://" + HOST
TIMEOUT = 30.0
SECRET_NAME = "coinbase"
ACCOUNT_NAME = "Coinbase"

# CoinGecko ids for the symbols that have many namesakes on the coins list.
PREFERRED_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "USDC": "usd-coin", "USDT": "tether",
                 "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "LTC": "litecoin", "AVAX": "avalanche-2",
                 "LINK": "chainlink", "DOT": "polkadot", "MATIC": "matic-network", "POL": "polygon-ecosystem-token",
                 "SHIB": "shiba-inu", "BCH": "bitcoin-cash", "UNI": "uniswap", "ATOM": "cosmos", "XLM": "stellar",
                 "ETC": "ethereum-classic", "AAVE": "aave", "SUI": "sui", "PEPE": "pepe", "TRX": "tron", "TON": "the-open-network"}


class CoinbaseError(Exception):
    pass


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def build_jwt(key_name, private_key_pem, method, path):
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as e:
        raise CoinbaseError("the private key is not a valid EC PEM: %s" % e)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise CoinbaseError("the key must be an ECDSA (ES256) key; Ed25519 keys are not accepted by the Coinbase App API")
    now = int(time.time())
    header = {"alg": "ES256", "kid": key_name, "nonce": pysecrets.token_hex(16), "typ": "JWT"}
    payload = {"iss": "cdp", "sub": key_name, "nbf": now, "exp": now + 120, "uri": "%s %s%s" % (method, HOST, path)}
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    der = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + _b64url(raw)


def _get(creds, path):
    """GET a v2 path (with query). -> parsed JSON. Raises CoinbaseError with the API's message."""
    bare = urlsplit(path).path
    token = build_jwt(creds["key_name"], creds["private_key"], "GET", bare)
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.get(BASE + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    if r.status_code == 401:
        raise CoinbaseError("Coinbase rejected the key (401). Check the key name, the PEM, and that the key has wallet:accounts:read and wallet:transactions:read.")
    if r.status_code != 200:
        msg = r.text[:200]
        try:
            msg = "; ".join(e.get("message", "") for e in r.json().get("errors", [])) or msg
        except ValueError:
            pass
        raise CoinbaseError("Coinbase HTTP %d: %s" % (r.status_code, msg))
    try:
        return r.json()
    except ValueError:
        raise CoinbaseError("Coinbase returned something that is not JSON")


def _paged(creds, path):
    out = []
    while path:
        j = _get(creds, path)
        out.extend(j.get("data", []) or [])
        path = (j.get("pagination") or {}).get("next_uri")
    return out


def fetch_accounts(creds):
    return _paged(creds, "/v2/accounts?limit=100")


def fetch_transactions(creds, account_id):
    return _paged(creds, "/v2/accounts/%s/transactions?limit=100" % account_id)


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------
def save_credentials(key_name, private_key_pem):
    key_name = key_name.strip()
    pem = private_key_pem.strip().replace("\\n", "\n")
    if not key_name.startswith("organizations/"):
        raise CoinbaseError("the key name looks like organizations/<org>/apiKeys/<id>")
    build_jwt(key_name, pem, "GET", "/v2/accounts")   # validates the PEM
    secrets_store.set(SECRET_NAME, json.dumps({"key_name": key_name, "private_key": pem}))


def load_credentials():
    raw = secrets_store.get(SECRET_NAME)
    return json.loads(raw) if raw else None


def connected():
    return secrets_store.exists(SECRET_NAME)


def test_connection():
    creds = load_credentials()
    if not creds:
        raise CoinbaseError("not connected")
    accts = fetch_accounts(creds)
    return [{"currency": (a.get("currency") or {}).get("code") if isinstance(a.get("currency"), dict) else a.get("currency"),
             "balance": (a.get("balance") or {}).get("amount"), "name": a.get("name")} for a in accts]


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------
def _currency_code(a):
    c = a.get("currency")
    return (c.get("code") if isinstance(c, dict) else c) or ""


def _dec(x):
    try:
        return money.to_e8(x)
    except ValueError:
        return 0


def _trade_from(tx, app_account_id, instrument_id):
    """A v2 transaction -> a trade row, or None for types with no position effect."""
    kind = tx.get("type") or ""
    status = tx.get("status") or "completed"
    if status not in ("completed", "pending"):
        return None
    amt = tx.get("amount") or {}
    nat = tx.get("native_amount") or {}
    qty = _dec(amt.get("amount"))
    usd = money.parse(nat.get("amount")) if nat.get("amount") is not None else 0
    date = (tx.get("created_at") or "")[:10] or config.today().isoformat()
    note = (tx.get("details") or {}).get("title") or tx.get("description") or kind
    if qty == 0:
        return None
    absq, absusd = abs(qty), abs(usd)
    # dollars per unit, 1e8-scaled: (usd cents / 100) / (qty / 1e8) * 1e8
    price = int(round(absusd / 100 * money.E8 / (absq / money.E8))) if absq else 0
    row = {"date": date, "qty_e8": absq, "price_e8": price, "fee_cents": 0, "external_id": tx.get("id"), "note": note[:120]}
    if kind in ("buy", "advanced_trade_fill", "trade", "fiat_deposit") and qty > 0:
        row.update(kind="buy", total_cents=-absusd)
    elif kind in ("sell", "advanced_trade_fill", "trade", "fiat_withdrawal") and qty < 0:
        row.update(kind="sell", total_cents=absusd)
    elif qty > 0:
        # receive, staking_reward, interest, inflation_reward, earn, etc: the coins arrive with today's value as basis
        row.update(kind="transfer_in", total_cents=0, cost_basis_cents=absusd)
    else:
        row.update(kind="transfer_out", total_cents=0)
    return row


def _instrument_for(conn, code, coins):
    r = db.row(conn, "SELECT id FROM instruments WHERE symbol = ? AND kind = 'crypto'", (code,))
    if r:
        return r["id"]
    cg = PREFERRED_IDS.get(code)
    if not cg and coins:
        match = [c for c in coins if c.get("symbol", "").upper() == code]
        if match:
            exact_name = [c for c in match if c.get("id") == code.lower() or c.get("name", "").lower() == code.lower()]
            cg = (exact_name or match)[0]["id"]
    cur = conn.execute("INSERT INTO instruments (symbol, name, kind, price_source, coingecko_id) VALUES (?, ?, 'crypto', ?, ?)",
                       (code, code, "auto" if cg else "manual", cg))
    return cur.lastrowid


def _app_account(conn):
    raw = db.scalar(conn, "SELECT value FROM settings WHERE key = 'coinbase_account_id'")
    if raw:
        a = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (json.loads(raw),))
        if a:
            return a
    cur = conn.execute("INSERT INTO accounts (name, kind, institution, default_business, created_at) VALUES (?, 'crypto', 'Coinbase', 'personal', ?)",
                       (ACCOUNT_NAME, config.iso_now()))
    conn.execute("INSERT INTO settings (key, value) VALUES ('coinbase_account_id', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (json.dumps(cur.lastrowid),))
    return db.row(conn, "SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,))


def run_sync():
    """The job. Pull wallets and transactions, write trades, reconcile balances."""
    creds = load_credentials()
    if not creds:
        logger.info("coinbase: not connected, nothing to do")
        return {"skipped": "not connected"}
    wallets = fetch_accounts(creds)
    try:
        import prices
        coins = prices.coins_list(None)
    except Exception:  # noqa: BLE001 - a missing coins list only costs price lookups
        coins = []
    result = {"wallets": 0, "trades_inserted": 0, "adjustments": 0, "skipped_types": {}}
    now_iso = config.iso_now()
    txs_by_wallet = []
    for w in wallets:
        code = _currency_code(w)
        bal = _dec((w.get("balance") or {}).get("amount"))
        if not code or (code == "USD" and bal == 0):
            continue
        txs = fetch_transactions(creds, w["id"])
        if bal == 0 and not txs:
            continue
        txs_by_wallet.append((code, bal, txs))
    with db.write() as conn:
        account = _app_account(conn)
        import holdings as H
        for code, bal, txs in txs_by_wallet:
            result["wallets"] += 1
            if code == "USD":
                # cash sitting in Coinbase: record as the account's balance, not a holding
                conn.execute("""INSERT INTO account_balances (account_id, date, balance_cents, source) VALUES (?, ?, ?, 'manual')
                                ON CONFLICT(account_id, date) DO UPDATE SET balance_cents = excluded.balance_cents""",
                             (account["id"], config.today().isoformat(), money.parse("%.2f" % (bal / money.E8))))
                continue
            iid = _instrument_for(conn, code, coins)
            existing = {r["external_id"] for r in db.rows(conn, "SELECT external_id FROM trades WHERE account_id = ? AND instrument_id = ?", (account["id"], iid))}
            for tx in txs:
                if tx.get("id") in existing:
                    continue
                row = _trade_from(tx, account["id"], iid)
                if not row:
                    t = tx.get("type") or "?"
                    result["skipped_types"][t] = result["skipped_types"].get(t, 0) + 1
                    continue
                conn.execute("""INSERT INTO trades (account_id, instrument_id, date, kind, qty_e8, price_e8, fee_cents, total_cents,
                                cost_basis_cents, external_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (account["id"], iid, row["date"], row["kind"], row["qty_e8"], row["price_e8"], row["fee_cents"],
                              row["total_cents"], row.get("cost_basis_cents"), row["external_id"], row["note"]))
                existing.add(tx.get("id"))
                result["trades_inserted"] += 1
            # reconcile: what the wallet says vs what the trades add up to
            trades = db.rows(conn, "SELECT * FROM trades WHERE account_id = ? AND instrument_id = ? ORDER BY date, id", (account["id"], iid))
            held = sum(p["qty_e8"] for p in H.compute(trades)["positions"])
            diff = bal - held
            if abs(diff) > 100:   # 1e-6 of a unit
                today = config.today().isoformat()
                ext = "adjust:%s:%s" % (code, today)
                conn.execute("DELETE FROM trades WHERE account_id = ? AND external_id = ?", (account["id"], ext))
                conn.execute("""INSERT INTO trades (account_id, instrument_id, date, kind, qty_e8, price_e8, fee_cents, total_cents, cost_basis_cents, external_id, note)
                                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?)""",
                             (account["id"], iid, today, "transfer_in" if diff > 0 else "transfer_out", abs(diff), ext,
                              "balance adjustment: wallet says %s, trades add to %s" % (money.e8_str(bal), money.e8_str(held))))
                result["adjustments"] += 1
        conn.execute("INSERT INTO settings (key, value) VALUES ('coinbase_last', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (json.dumps({"at": now_iso, **result}),))
    logger.info("coinbase: %s", result)
    try:
        import scheduler
        if "prices" in scheduler._jobs:
            scheduler.run_now("prices")
    except Exception:  # noqa: BLE001
        pass
    return result


def status():
    with db.read() as conn:
        last = db.scalar(conn, "SELECT value FROM settings WHERE key = 'coinbase_last'")
        acct = db.scalar(conn, "SELECT value FROM settings WHERE key = 'coinbase_account_id'")
        job = db.row(conn, "SELECT * FROM jobs WHERE name = 'coinbase'")
    return {"connected": connected(), "last": json.loads(last) if last else None,
            "account_id": json.loads(acct) if acct else None, "job": job}
