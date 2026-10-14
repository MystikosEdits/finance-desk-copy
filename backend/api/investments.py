import hashlib
import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

import audit
import config
import db
import holdings as H
import money
import prices as P
import scheduler
from api import actor, require_owner
from importers import brokers

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# instruments
# --------------------------------------------------------------------------
@router.get("/instruments")
def list_instruments():
    with db.read() as conn:
        rows = db.rows(conn, "SELECT i.*, (SELECT COUNT(*) FROM trades t WHERE t.instrument_id = i.id) AS trades FROM instruments i ORDER BY i.kind, i.symbol")
        for r in rows:
            r["price"] = P.latest(conn, r["id"])
        return rows


class InstrumentReq(BaseModel):
    symbol: str
    kind: str
    name: str | None = ""
    price_source: str | None = "auto"
    coingecko_id: str | None = None


@router.post("/instruments")
def create_instrument(req: InstrumentReq, request: Request):
    if req.kind not in ("stock", "etf", "crypto", "cash", "other"):
        raise HTTPException(400, "bad kind")
    if req.kind == "crypto" and req.price_source in ("auto", "coingecko") and not req.coingecko_id:
        raise HTTPException(400, "a crypto instrument needs a coingecko_id (search to pick it)")
    with db.write() as conn:
        try:
            cur = conn.execute("INSERT INTO instruments (symbol, name, kind, price_source, coingecko_id) VALUES (?, ?, ?, ?, ?)",
                               (req.symbol.upper().strip(), req.name or "", req.kind, req.price_source or "auto", req.coingecko_id))
        except Exception as e:
            raise HTTPException(400, "could not create: %s" % e)
        audit.log(conn, actor(request), "instrument", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM instruments WHERE id = ?", (cur.lastrowid,))


@router.patch("/instruments/{iid}")
def patch_instrument(iid: int, req: InstrumentReq, request: Request):
    with db.write() as conn:
        conn.execute("UPDATE instruments SET symbol = ?, name = ?, kind = ?, price_source = ?, coingecko_id = ? WHERE id = ?",
                     (req.symbol.upper().strip(), req.name or "", req.kind, req.price_source or "auto", req.coingecko_id, iid))
        audit.log(conn, actor(request), "instrument", iid, "patch", None, req.model_dump())
        return db.row(conn, "SELECT * FROM instruments WHERE id = ?", (iid,))


@router.get("/instruments/search")
def search(q: str, kind: str | None = None):
    q = q.strip()
    out = {"equities": [], "crypto": []}
    if not q:
        return out
    with db.read() as conn:
        key = db.scalar(conn, "SELECT value FROM settings WHERE key = 'coingecko_api_key'")
    api_key = json.loads(key) if key else None
    if kind in (None, "stock", "etf"):
        try:
            out["equities"] = P.yahoo_search(q)
        except Exception as e:  # noqa: BLE001
            out["equities_error"] = str(e)
    if kind in (None, "crypto"):
        try:
            coins = P.coins_list(api_key)
            ql = q.lower()
            exact = [c for c in coins if c["symbol"].lower() == ql or c["id"] == ql]
            partial = [c for c in coins if ql in c["name"].lower() or ql in c["id"]][:15]
            seen = set()
            for c in exact + partial:
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                out["crypto"].append({"coingecko_id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"]})
            out["crypto"] = out["crypto"][:20]
        except Exception as e:  # noqa: BLE001
            out["crypto_error"] = str(e)
    return out


# --------------------------------------------------------------------------
# holdings / trades
# --------------------------------------------------------------------------
def _positions(conn, account_id=None):
    trades = db.rows(conn, "SELECT * FROM trades" + (" WHERE account_id = ?" if account_id else "") + " ORDER BY date, id",
                     (account_id,) if account_id else ())
    allocs = {}
    for r in db.rows(conn, "SELECT * FROM sell_allocations"):
        allocs.setdefault(r["sell_trade_id"], {})[r["buy_trade_id"]] = r["qty_e8"]
    return H.compute(trades, allocs)


@router.get("/holdings")
def get_holdings(account: int | None = None):
    with db.read() as conn:
        res = _positions(conn, account)
        instruments = {r["id"]: r for r in db.rows(conn, "SELECT * FROM instruments")}
        accounts = {r["id"]: r["name"] for r in db.rows(conn, "SELECT id, name FROM accounts")}
        out = []
        total_value = total_cost = 0
        for p in res["positions"]:
            if p["qty_e8"] <= 0:
                continue
            inst = instruments.get(p["instrument_id"], {})
            pr = P.latest(conn, p["instrument_id"])
            price_e8 = pr["price_e8"] if pr else 0
            value = money.mul_e8_to_cents(p["qty_e8"], price_e8) if price_e8 else 0
            total_value += value
            total_cost += p["cost_cents"]
            out.append({**p, "symbol": inst.get("symbol"), "name": inst.get("name"), "kind": inst.get("kind"),
                        "account_name": accounts.get(p["account_id"]), "price_e8": price_e8,
                        "price_date": pr["date"] if pr else None, "price_source": pr["source"] if pr else None,
                        "price_stale": (pr["stale"] if pr else True), "value_cents": value,
                        "unrealized_cents": value - p["cost_cents"] if price_e8 else None,
                        "avg_cost_e8": int(p["cost_cents"] * 1e8 / (p["qty_e8"] / 1e8) / 100) if p["qty_e8"] else 0})
        for o in out:
            o["weight"] = (o["value_cents"] / total_value) if total_value else 0
        by_kind = {}
        for o in out:
            by_kind[o["kind"]] = by_kind.get(o["kind"], 0) + o["value_cents"]
        # Collectibles (Collectr) are part of the portfolio too. They have no
        # cost basis unless Collectr says so, so they count in value and
        # allocation but stay out of unrealized gain.
        collectibles = db.scalar(conn, "SELECT COALESCE(SUM(manual_value_cents), 0) FROM accounts "
                                       "WHERE kind = 'asset' AND institution = 'Collectr' AND is_closed = 0") or 0
        if collectibles:
            by_kind["collectibles"] = collectibles
        for o in out:
            o["weight"] = (o["value_cents"] / (total_value + collectibles)) if (total_value + collectibles) else 0
        return {"positions": sorted(out, key=lambda x: -x["value_cents"]), "total_value_cents": total_value + collectibles,
                "securities_value_cents": total_value, "collectibles_cents": collectibles,
                "total_cost_cents": total_cost, "unrealized_cents": total_value - total_cost,
                "by_kind": by_kind, "warnings": res["warnings"],
                "income_ytd_cents": sum(i["cents"] for i in res["income"] if i["date"] >= "%d-01-01" % config.today().year)}


@router.get("/trades")
def list_trades(account: int | None = None, instrument: int | None = None, limit: int | None = 500):
    with db.read() as conn:
        where, args = ["1=1"], []
        if account:
            where.append("t.account_id = ?"); args.append(account)
        if instrument:
            where.append("t.instrument_id = ?"); args.append(instrument)
        return db.rows(conn, """SELECT t.*, i.symbol, i.kind AS instrument_kind, a.name AS account_name
                                FROM trades t JOIN instruments i ON i.id = t.instrument_id JOIN accounts a ON a.id = t.account_id
                                WHERE %s ORDER BY t.date DESC, t.id DESC LIMIT ?""" % " AND ".join(where), tuple(args) + (limit or 500,))


class TradeReq(BaseModel):
    account_id: int
    instrument_id: int
    date: str
    kind: str
    qty: str | None = "0"          # decimal strings; parsed to e8 here
    price: str | None = "0"
    fee_cents: int | None = 0
    total_cents: int | None = None
    cost_basis_cents: int | None = None
    cash_txn_id: int | None = None
    note: str | None = ""


def _trade_row(req):
    if req.kind not in ("buy", "sell", "transfer_in", "transfer_out", "dividend", "interest", "fee", "split"):
        raise HTTPException(400, "bad kind")
    qty = money.to_e8(req.qty or "0")
    price = money.to_e8(req.price or "0")
    fee = req.fee_cents or 0
    if req.total_cents is not None:
        total = req.total_cents
    elif req.kind == "buy":
        total = -(money.mul_e8_to_cents(qty, price) + fee)
    elif req.kind == "sell":
        total = money.mul_e8_to_cents(qty, price) - fee
    elif req.kind == "fee":
        total = -fee
    else:
        total = 0
    return qty, price, fee, total


@router.post("/trades")
def create_trade(req: TradeReq, request: Request):
    qty, price, fee, total = _trade_row(req)
    with db.write() as conn:
        cur = conn.execute("""INSERT INTO trades (account_id, instrument_id, date, kind, qty_e8, price_e8, fee_cents, total_cents,
                              cost_basis_cents, cash_txn_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (req.account_id, req.instrument_id, req.date, req.kind, qty, price, fee, total, req.cost_basis_cents,
                            req.cash_txn_id, req.note or ""))
        if req.cash_txn_id:
            amt = db.scalar(conn, "SELECT amount_cents FROM transactions WHERE id = ?", (req.cash_txn_id,))
            if amt is not None:
                db.set_splits(conn, req.cash_txn_id, [{"amount_cents": amt, "category_id": 504}], "user")
                conn.execute("UPDATE transactions SET status = 'reviewed' WHERE id = ?", (req.cash_txn_id,))
        audit.log(conn, actor(request), "trade", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM trades WHERE id = ?", (cur.lastrowid,))


@router.patch("/trades/{tid}")
def patch_trade(tid: int, req: TradeReq, request: Request):
    qty, price, fee, total = _trade_row(req)
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM trades WHERE id = ?", (tid,))
        if not before:
            raise HTTPException(404, "no such trade")
        conn.execute("""UPDATE trades SET account_id = ?, instrument_id = ?, date = ?, kind = ?, qty_e8 = ?, price_e8 = ?, fee_cents = ?,
                        total_cents = ?, cost_basis_cents = ?, cash_txn_id = ?, note = ? WHERE id = ?""",
                     (req.account_id, req.instrument_id, req.date, req.kind, qty, price, fee, total, req.cost_basis_cents,
                      req.cash_txn_id, req.note or "", tid))
        audit.log(conn, actor(request), "trade", tid, "patch", before, req.model_dump())
        return db.row(conn, "SELECT * FROM trades WHERE id = ?", (tid,))


@router.delete("/trades/{tid}")
def delete_trade(tid: int, request: Request):
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM trades WHERE id = ?", (tid,))
        conn.execute("DELETE FROM trades WHERE id = ?", (tid,))
        audit.log(conn, actor(request), "trade", tid, "delete", before)
    return {"ok": True}


class AllocReq(BaseModel):
    lots: dict[int, str]       # buy_trade_id -> qty (decimal string)


@router.post("/trades/{tid}/allocate")
def allocate(tid: int, req: AllocReq, request: Request):
    """Specific-lot identification for a sell."""
    with db.write() as conn:
        t = db.row(conn, "SELECT * FROM trades WHERE id = ?", (tid,))
        if not t or t["kind"] != "sell":
            raise HTTPException(400, "not a sell")
        conn.execute("DELETE FROM sell_allocations WHERE sell_trade_id = ?", (tid,))
        for buy_id, q in req.lots.items():
            qty = money.to_e8(q)
            buy = db.row(conn, "SELECT * FROM trades WHERE id = ? AND instrument_id = ? AND account_id = ?", (int(buy_id), t["instrument_id"], t["account_id"]))
            if not buy:
                raise HTTPException(400, "lot %s is not a buy of the same instrument in the same account" % buy_id)
            cost = int(round((money.mul_e8_to_cents(buy["qty_e8"], buy["price_e8"]) + buy["fee_cents"]) * qty / buy["qty_e8"])) if buy["qty_e8"] else 0
            conn.execute("INSERT INTO sell_allocations (sell_trade_id, buy_trade_id, qty_e8, cost_cents) VALUES (?, ?, ?, ?)", (tid, int(buy_id), qty, cost))
        audit.log(conn, actor(request), "trade", tid, "allocate", None, req.model_dump())
    return {"ok": True}


@router.get("/realized")
def realized(year: int | None = None):
    year = year or config.today().year
    with db.read() as conn:
        res = _positions(conn)
        instruments = {r["id"]: r["symbol"] for r in db.rows(conn, "SELECT id, symbol FROM instruments")}
        rows = [dict(r, symbol=instruments.get(r["instrument_id"])) for r in res["realized"] if r["date"].startswith(str(year))]
        inc = [dict(i, symbol=instruments.get(i["instrument_id"])) for i in res["income"] if i["date"].startswith(str(year))]
    return {"year": year, "rows": rows, "short_term_cents": sum(r["gain_cents"] for r in rows if not r["long_term"]),
            "long_term_cents": sum(r["gain_cents"] for r in rows if r["long_term"]),
            "income": inc, "income_cents": sum(i["cents"] for i in inc)}


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------
@router.get("/prices/{iid}")
def price_history(iid: int, frm: str | None = None, to: str | None = None):
    with db.read() as conn:
        return P.history(conn, iid, frm, to)


@router.post("/prices/refresh")
def refresh(request: Request):
    require_owner(request)
    if "prices" not in scheduler._jobs:
        raise HTTPException(400, "prices job not registered")
    if not scheduler.run_now("prices"):
        raise HTTPException(409, "already refreshing")
    return {"ok": True}


class ManualPrice(BaseModel):
    price: str
    date: str | None = None


@router.post("/prices/{iid}/manual")
def manual_price(iid: int, req: ManualPrice, request: Request):
    d = req.date or config.today().isoformat()
    with db.write() as conn:
        conn.execute("""INSERT INTO prices (instrument_id, date, price_e8, source, fetched_at) VALUES (?, ?, ?, 'manual', ?)
                        ON CONFLICT(instrument_id, date) DO UPDATE SET price_e8 = excluded.price_e8, source = 'manual', fetched_at = excluded.fetched_at""",
                     (iid, d, money.to_e8(req.price), config.iso_now()))
        audit.log(conn, actor(request), "instrument", iid, "manual-price", None, {"date": d, "price": req.price})
    return {"ok": True}


# --------------------------------------------------------------------------
# broker CSV
# --------------------------------------------------------------------------
@router.post("/imports/broker-csv/preview")
async def broker_preview(file: UploadFile = File(...), account_id: int = Form(...), profile: str | None = Form(None)):
    raw = await file.read()
    sn = brokers.sniff(raw)
    prof = json.loads(profile) if profile else sn["guess"]
    rows, errors = brokers.parse(raw, prof, account_id, sn["delimiter"]) if prof.get("column_map", {}).get("date") else ([], ["no date column"])
    with db.read() as conn:
        ext = {r["external_id"] for r in db.rows(conn, "SELECT external_id FROM trades WHERE account_id = ? AND external_id IS NOT NULL", (account_id,))}
        known = {r["symbol"] for r in db.rows(conn, "SELECT symbol FROM instruments")}
    for r in rows:
        r["duplicate"] = r["external_id"] in ext
        r["new_symbol"] = r["symbol"] not in known
    return {"sniff": sn, "profile": prof, "rows": rows[:100], "total": len(rows), "errors": errors[:20],
            "would_insert": sum(1 for r in rows if not r["duplicate"]), "would_skip": sum(1 for r in rows if r["duplicate"]),
            "new_symbols": sorted({r["symbol"] for r in rows if r["new_symbol"]})}


@router.post("/imports/broker-csv")
async def broker_commit(request: Request, file: UploadFile = File(...), account_id: int = Form(...), profile: str = Form(...),
                        symbol_kinds: str | None = Form(None)):
    """symbol_kinds: JSON {symbol: {"kind": "stock"|"etf"|"crypto", "coingecko_id": ...}} for symbols not yet known."""
    require_owner(request)
    raw = await file.read()
    sha = hashlib.sha256(raw).hexdigest()
    prof = json.loads(profile)
    kinds = json.loads(symbol_kinds) if symbol_kinds else {}
    sn = brokers.sniff(raw, int(prof.get("skip_rows") or 0))
    rows, errors = brokers.parse(raw, prof, account_id, sn["delimiter"])
    now_iso = config.iso_now()
    with db.write() as conn:
        if db.row(conn, "SELECT id FROM imports WHERE sha256 = ? AND undone_at IS NULL", (sha,)):
            raise HTTPException(409, "this exact file was already imported")
        cur = conn.execute("INSERT INTO imports (kind, filename, sha256, account_id, started_at) VALUES ('broker_csv', ?, ?, ?, ?)",
                           (file.filename, sha, account_id, now_iso))
        import_id = cur.lastrowid
        ext = {r["external_id"] for r in db.rows(conn, "SELECT external_id FROM trades WHERE account_id = ?", (account_id,))}
        inserted = skipped = 0
        for r in rows:
            if r["external_id"] in ext:
                skipped += 1
                continue
            inst = db.row(conn, "SELECT id FROM instruments WHERE symbol = ?", (r["symbol"],))
            if not inst:
                k = kinds.get(r["symbol"], {})
                conn.execute("INSERT INTO instruments (symbol, kind, coingecko_id) VALUES (?, ?, ?)",
                             (r["symbol"], k.get("kind", "stock"), k.get("coingecko_id")))
                inst = db.row(conn, "SELECT id FROM instruments WHERE symbol = ?", (r["symbol"],))
            conn.execute("""INSERT INTO trades (account_id, instrument_id, date, kind, qty_e8, price_e8, fee_cents, total_cents, import_id, external_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (account_id, inst["id"], r["date"], r["kind"], r["qty_e8"], r["price_e8"], r["fee_cents"], r["total_cents"], import_id, r["external_id"]))
            ext.add(r["external_id"])
            inserted += 1
        conn.execute("UPDATE imports SET inserted = ?, skipped = ?, finished_at = ?, error = ? WHERE id = ?",
                     (inserted, skipped, config.iso_now(), "; ".join(errors[:10]) or None, import_id))
        audit.log(conn, actor(request), "import", import_id, "broker_csv", None, {"inserted": inserted, "skipped": skipped})
    return {"ok": True, "import_id": import_id, "inserted": inserted, "skipped": skipped, "errors": errors[:20]}
