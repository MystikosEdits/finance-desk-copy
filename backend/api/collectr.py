import hashlib
import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

import audit
import config
import db
from api import actor, require_owner
from importers import collectr as C

router = APIRouter(prefix="/api")


import collectr_share as CS
import scheduler


@router.get("/collectr/status")
def share_status():
    return CS.status()


class ShareReq(BaseModel):
    share_url: str


@router.post("/collectr/connect")
def share_connect(req: ShareReq, request: Request):
    """Save the link (handle). A direct read is attempted; if Collectr's edge
    blocks it, the link is still saved and the bookmarklet path takes over."""
    require_owner(request)
    try:
        sid = CS.validate_id(CS.share_id(req.share_url))
    except CS.CollectrError as e:
        raise HTTPException(400, str(e))
    with db.write() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('collectr_share_url', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (json.dumps(req.share_url.strip()),))
        audit.log(conn, actor(request), "collectr", 0, "connect", None, {"id": CS.redact_id(sid)})
    out = {"ok": True, "handle": sid, "direct": None}
    try:
        norm = CS.normalize(CS.fetch(sid))
        if norm["items"] or norm["portfolio_value_cents"]:
            with db.write() as conn:
                aid, total = CS.apply(conn, norm)
            out["direct"] = {"account_id": aid, "items": len(norm["items"]), "value_cents": total}
        else:
            out["direct"] = {"empty": True, "note": "the feed is empty - portfolio sharing looks off"}
    except CS.Blocked as e:
        out["direct"] = {"blocked": True, "note": str(e)}
    except CS.CollectrError as e:
        out["direct"] = {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        out["direct"] = {"error": "could not reach Collectr: %s" % e}
    return out


@router.get("/collectr/bookmarklet")
def share_bookmarklet(origin: str):
    """The bookmark's URL for the saved handle, built for the desk origin the
    browser is on. One copy of the script, in collectr_share.bookmarklet."""
    st = CS.status()
    if not st.get("handle"):
        raise HTTPException(400, "connect a Collectr handle first")
    if not (origin.startswith("http://") or origin.startswith("https://")):
        raise HTTPException(400, "bad origin")
    return {"href": CS.bookmarklet(st["handle"], origin)}


class PasteReq(BaseModel):
    payload: str


@router.post("/collectr/paste")
def share_paste(req: PasteReq, request: Request):
    """The bookmarklet's clipboard contents."""
    require_owner(request)
    try:
        aid, total, norm = CS.ingest(req.payload)
    except CS.CollectrError as e:
        raise HTTPException(400, str(e))
    with db.write() as conn:
        audit.log(conn, actor(request), "collectr", aid, "paste", None, {"items": len(norm["items"]), "value_cents": total})
    return {"ok": True, "account_id": aid, "username": norm["username"], "value_cents": total, "items": len(norm["items"]),
            "items_value_cents": norm["items_value_cents"], "product_keys": norm["product_keys"]}


@router.post("/collectr/sync")
def share_sync(request: Request):
    require_owner(request)
    if not CS.connected():
        raise HTTPException(400, "no share link saved")
    if "collectr" not in scheduler._jobs or not scheduler.run_now("collectr"):
        raise HTTPException(409, "a Collectr sync is already running")
    return {"ok": True}


@router.delete("/collectr/share")
def share_disconnect(request: Request):
    require_owner(request)
    with db.write() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('collectr_share_url', 'collectr_last')")
        audit.log(conn, actor(request), "collectr", 0, "disconnect")
    return {"ok": True}


@router.get("/collectibles")
def list_collectibles(account: int | None = None):
    with db.read() as conn:
        where = "c.account_id = ?" if account else "1=1"
        rows = db.rows(conn, """SELECT c.*, a.name AS account_name FROM collectibles c JOIN accounts a ON a.id = c.account_id
                                WHERE %s ORDER BY c.value_cents DESC, c.name""" % where, (account,) if account else ())
        accounts = db.rows(conn, """SELECT a.id, a.name, a.manual_value_cents,
                                           (SELECT COUNT(*) FROM collectibles c WHERE c.account_id = a.id) AS items,
                                           (SELECT MAX(imported_at) FROM collectibles c WHERE c.account_id = a.id) AS imported_at
                                    FROM accounts a WHERE a.kind = 'asset' AND a.institution = 'Collectr' AND a.is_closed = 0""")
        history = {}
        for a in accounts:
            history[a["id"]] = db.rows(conn, "SELECT date, balance_cents FROM account_balances WHERE account_id = ? ORDER BY date", (a["id"],))
    return {"rows": rows, "accounts": accounts, "history": history,
            "total_value_cents": sum(r["value_cents"] for r in rows), "total_cost_cents": sum(r["cost_cents"] or 0 for r in rows),
            "items": sum(r["quantity"] for r in rows)}


class CollectrAccountReq(BaseModel):
    name: str | None = "Collectr"


@router.post("/collectr/account")
def create_account(req: CollectrAccountReq, request: Request):
    with db.write() as conn:
        cur = conn.execute("INSERT INTO accounts (name, kind, institution, default_business, manual_value_cents, created_at) VALUES (?, 'asset', 'Collectr', 'personal', 0, ?)",
                           ((req.name or "Collectr").strip(), config.iso_now()))
        audit.log(conn, actor(request), "account", cur.lastrowid, "create", None, {"institution": "Collectr"})
        return db.row(conn, "SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,))


@router.post("/imports/collectr/preview")
async def preview(file: UploadFile = File(...), profile: str | None = Form(None)):
    raw = await file.read()
    sn = C.sniff(raw)
    prof = json.loads(profile) if profile else sn["guess"]
    rows, errors = C.parse(raw, prof, sn["delimiter"])
    return {"sniff": {"headers": sn["headers"], "sample": sn["sample"][:5]}, "profile": prof, "rows": rows[:100], "total": len(rows),
            "errors": errors[:10], "total_value_cents": sum(r["value_cents"] for r in rows), "items": sum(r["quantity"] for r in rows)}


@router.post("/imports/collectr")
async def commit(request: Request, file: UploadFile = File(...), account_id: int = Form(...), profile: str = Form(...)):
    """Replace the account's collection with this export and set its value.
    Every import is a snapshot; the previous rows are dropped, the day's
    balance row keeps the value history."""
    require_owner(request)
    raw = await file.read()
    prof = json.loads(profile)
    sn = C.sniff(raw, int(prof.get("skip_rows") or 0))
    rows, errors = C.parse(raw, prof, sn["delimiter"])
    if not rows:
        raise HTTPException(400, "; ".join(errors) or "nothing to import")
    now_iso = config.iso_now()
    today = config.today().isoformat()
    with db.write() as conn:
        a = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not a or a["kind"] != "asset":
            raise HTTPException(400, "pick a Collectr (asset) account")
        before = db.scalar(conn, "SELECT COUNT(*) FROM collectibles WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM collectibles WHERE account_id = ?", (account_id,))
        for r in rows:
            conn.execute("""INSERT INTO collectibles (account_id, external_key, name, set_name, number, condition, quantity, unit_value_cents, value_cents, cost_cents, imported_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (account_id, r["external_key"], r["name"], r["set_name"], r["number"], r["condition"], r["quantity"],
                          r["unit_value_cents"], r["value_cents"], r["cost_cents"], now_iso))
        total = sum(r["value_cents"] for r in rows)
        conn.execute("UPDATE accounts SET manual_value_cents = ? WHERE id = ?", (total, account_id))
        conn.execute("""INSERT INTO account_balances (account_id, date, balance_cents, source) VALUES (?, ?, ?, 'manual')
                        ON CONFLICT(account_id, date) DO UPDATE SET balance_cents = excluded.balance_cents""", (account_id, today, total))
        conn.execute("INSERT INTO imports (kind, filename, sha256, account_id, inserted, skipped, started_at, finished_at) VALUES ('csv', ?, ?, ?, ?, ?, ?, ?)",
                     (file.filename, hashlib.sha256(raw).hexdigest() + ":collectr:" + now_iso, account_id, len(rows), 0, now_iso, now_iso))
        audit.log(conn, actor(request), "account", account_id, "collectr-import", {"items_before": before}, {"items": len(rows), "value_cents": total})
    return {"ok": True, "items": len(rows), "total_value_cents": total, "replaced": before, "errors": errors[:10]}
