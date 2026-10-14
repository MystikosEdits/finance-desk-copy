"""Net worth = every account's value on a date. Snapshotted daily after prices.

The one rule that avoids double counting: a brokerage or crypto account that
has TRADES recorded is valued from its holdings x price (the bank's balance
for it is ignored); one without trades is valued from its balance like any
other account. Manual asset/liability accounts use their hand-entered value.
"""

import json

import config
import db
import holdings
import prices


def _balance_on(conn, account, d):
    if account["kind"] in ("asset", "liability") and account.get("manual_value_cents") is not None:
        return account["manual_value_cents"], "manual"
    b = db.row(conn, "SELECT balance_cents, date FROM account_balances WHERE account_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
               (account["id"], d))
    if b:
        return b["balance_cents"], "balance"
    opening = account["opening_balance_cents"] or 0
    s = db.scalar(conn, "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions WHERE account_id = ? AND pending = 0 AND date <= ?"
                        + (" AND date >= ?" if account.get("opening_date") else ""),
                  (account["id"], d, account["opening_date"]) if account.get("opening_date") else (account["id"], d))
    return opening + s, "computed"


def compute(conn, d=None):
    d = d or config.today().isoformat()
    accounts = db.rows(conn, "SELECT * FROM accounts WHERE is_closed = 0 OR (SELECT MAX(date) FROM transactions WHERE account_id = accounts.id) >= ?", (d,))
    trades = db.rows(conn, "SELECT * FROM trades WHERE date <= ?", (d,))
    allocs = {}
    for r in db.rows(conn, "SELECT * FROM sell_allocations"):
        allocs.setdefault(r["sell_trade_id"], {})[r["buy_trade_id"]] = r["qty_e8"]
    pos = holdings.compute(trades, allocs)
    by_account = {}
    for p in pos["positions"]:
        if p["qty_e8"] > 0:
            by_account.setdefault(p["account_id"], []).append(p)
    instruments = {r["id"]: r for r in db.rows(conn, "SELECT * FROM instruments")}
    detail_accounts, detail_holdings = [], []
    assets = liabilities = 0
    stale = []
    for a in accounts:
        if a["id"] in by_account:
            value = 0
            for p in by_account[a["id"]]:
                inst = instruments.get(p["instrument_id"], {})
                pr = prices.latest(conn, p["instrument_id"], d if d != config.today().isoformat() else None)
                price_e8 = pr["price_e8"] if pr else 0
                v = int(round(p["qty_e8"] * price_e8 / 1e16 * 100))
                if pr and pr.get("stale"):
                    stale.append(inst.get("symbol"))
                if not pr:
                    stale.append(inst.get("symbol"))
                value += v
                detail_holdings.append({"account_id": a["id"], "instrument_id": p["instrument_id"], "symbol": inst.get("symbol"),
                                        "qty_e8": p["qty_e8"], "price_e8": price_e8, "value_cents": v, "cost_cents": p["cost_cents"],
                                        "price_date": pr["date"] if pr else None})
            src = "holdings"
        else:
            value, src = _balance_on(conn, a, d)
        is_liab = a["kind"] in ("credit", "loan", "liability")
        if is_liab:
            liabilities += value
        else:
            assets += value
        detail_accounts.append({"account_id": a["id"], "name": a["name"], "kind": a["kind"], "value_cents": value, "source": src, "liability": is_liab})
    return {"date": d, "assets_cents": assets, "liabilities_cents": liabilities, "net_cents": assets + liabilities,
            "accounts": detail_accounts, "holdings": detail_holdings, "stale_prices": sorted(set(s for s in stale if s))}


def snapshot(conn, d=None):
    out = compute(conn, d)
    conn.execute("""INSERT INTO net_worth_snapshots (date, assets_cents, liabilities_cents, detail_json) VALUES (?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET assets_cents = excluded.assets_cents, liabilities_cents = excluded.liabilities_cents,
                    detail_json = excluded.detail_json""",
                 (out["date"], out["assets_cents"], out["liabilities_cents"],
                  json.dumps({"accounts": out["accounts"], "holdings": out["holdings"], "stale_prices": out["stale_prices"]})))
    return out


def snapshot_today():
    with db.write() as conn:
        return snapshot(conn)


def history(conn, frm=None, to=None):
    rows = db.rows(conn, "SELECT date, assets_cents, liabilities_cents FROM net_worth_snapshots WHERE date >= ? AND date <= ? ORDER BY date",
                   (frm or "2000-01-01", to or "2999-12-31"))
    for r in rows:
        r["net_cents"] = r["assets_cents"] + r["liabilities_cents"]
    return rows
