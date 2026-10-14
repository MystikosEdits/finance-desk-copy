"""SimpleFIN connection + CSV import."""

import hashlib
import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

import audit
import config
import db
import rules
import scheduler
import secrets_store
import simplefin
import sync
import transfers
from api import actor, require_owner
from importers import csvmap

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# SimpleFIN
# --------------------------------------------------------------------------
@router.get("/simplefin/status")
def sf_status():
    with db.read() as conn:
        last = db.row(conn, "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1")
        last_ok = db.row(conn, "SELECT * FROM sync_log WHERE ok = 1 ORDER BY id DESC LIMIT 1")
        job = db.row(conn, "SELECT * FROM jobs WHERE name = 'sync'")
    access = secrets_store.get("simplefin")
    return {"connected": bool(access), "host": simplefin.redact(access) if access else None,
            "last": last, "last_ok": last_ok, "job": job}


class ClaimReq(BaseModel):
    setup_token: str


@router.post("/simplefin/claim")
def sf_claim(req: ClaimReq, request: Request):
    require_owner(request)
    try:
        access = simplefin.claim(req.setup_token)
    except simplefin.SimpleFinError as e:
        raise HTTPException(400, str(e))
    # Persist BEFORE anything else can fail: the token is spent now.
    secrets_store.set("simplefin", access)
    with db.write() as conn:
        audit.log(conn, actor(request), "simplefin", 0, "claim", None, {"host": simplefin.redact(access)})
    started = scheduler.run_now("sync") if "sync" in scheduler._jobs else False
    return {"ok": True, "host": simplefin.redact(access), "sync_started": started}


@router.post("/simplefin/sync")
def sf_sync(request: Request):
    require_owner(request)
    if not secrets_store.exists("simplefin"):
        raise HTTPException(400, "SimpleFIN is not connected")
    if not scheduler.run_now("sync"):
        raise HTTPException(409, "a sync is already running")
    return {"ok": True}


@router.get("/simplefin/log")
def sf_log(limit: int | None = 30):
    with db.read() as conn:
        return db.rows(conn, "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit or 30,))


@router.delete("/simplefin")
def sf_disconnect(request: Request):
    require_owner(request)
    secrets_store.delete("simplefin")
    with db.write() as conn:
        audit.log(conn, actor(request), "simplefin", 0, "disconnect")
    return {"ok": True}


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
@router.get("/imports/csv/profiles")
def profiles():
    with db.read() as conn:
        out = db.rows(conn, "SELECT p.*, a.name AS account_name FROM csv_profiles p LEFT JOIN accounts a ON a.id = p.account_id ORDER BY p.name")
        for p in out:
            p["column_map"] = json.loads(p["column_map"])
        return out


class ProfileReq(BaseModel):
    name: str
    account_id: int | None = None
    column_map: dict
    date_format: str | None = "%m/%d/%Y"
    negate: int | None = 0
    skip_rows: int | None = 0


@router.post("/imports/csv/profiles")
def create_profile(req: ProfileReq, request: Request):
    with db.write() as conn:
        cur = conn.execute("""INSERT INTO csv_profiles (name, account_id, column_map, date_format, negate, skip_rows)
                              VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET account_id = excluded.account_id,
                              column_map = excluded.column_map, date_format = excluded.date_format, negate = excluded.negate,
                              skip_rows = excluded.skip_rows""",
                           (req.name.strip(), req.account_id, json.dumps(req.column_map), req.date_format or "%m/%d/%Y",
                            req.negate or 0, req.skip_rows or 0))
        pid = cur.lastrowid or db.scalar(conn, "SELECT id FROM csv_profiles WHERE name = ?", (req.name.strip(),))
        audit.log(conn, actor(request), "csv_profile", pid, "save", None, req.model_dump())
        p = db.row(conn, "SELECT * FROM csv_profiles WHERE id = ?", (pid,))
        p["column_map"] = json.loads(p["column_map"])
        return p


@router.delete("/imports/csv/profiles/{pid}")
def delete_profile(pid: int):
    with db.write() as conn:
        conn.execute("DELETE FROM csv_profiles WHERE id = ?", (pid,))
    return {"ok": True}


@router.post("/imports/csv/preview")
async def csv_preview(file: UploadFile = File(...), account_id: int = Form(...),
                      profile: str | None = Form(None), skip_rows: int | None = Form(0)):
    """Sniff the file (or use the given profile JSON) and parse the first rows.
    Reports how many rows would be inserted vs skipped as duplicates."""
    raw = await file.read()
    sn = csvmap.sniff(raw, int(skip_rows or 0))
    prof = json.loads(profile) if profile else sn["guess"]
    if not prof.get("column_map") or "date" not in prof["column_map"]:
        return {"sniff": sn, "profile": prof, "rows": [], "errors": ["could not find a date column - map it by hand"],
                "would_insert": 0, "would_skip": 0}
    rows, errors = csvmap.parse(raw, prof, account_id, sn["delimiter"])
    with db.read() as conn:
        existing = db.rows(conn, "SELECT date, amount_cents, payee_raw, description_raw, external_id FROM transactions WHERE account_id = ?", (account_id,))
        ext = {e["external_id"] for e in existing if e["external_id"]}
    ins, skip = 0, 0
    for r in rows:
        if r["external_id"] in ext or csvmap.find_fuzzy_duplicate(existing, r):
            r["duplicate"] = True
            skip += 1
        else:
            r["duplicate"] = False
            ins += 1
    return {"sniff": sn, "profile": prof, "rows": rows[:50], "total": len(rows), "errors": errors[:20],
            "would_insert": ins, "would_skip": skip, "sha256": hashlib.sha256(raw).hexdigest()}


@router.post("/imports/csv")
async def csv_commit(request: Request, file: UploadFile = File(...), account_id: int = Form(...),
                     profile: str = Form(...), save_profile_as: str | None = Form(None)):
    require_owner(request)
    raw = await file.read()
    sha = hashlib.sha256(raw).hexdigest()
    prof = json.loads(profile)
    sn = csvmap.sniff(raw, int(prof.get("skip_rows") or 0))
    rows, errors = csvmap.parse(raw, prof, account_id, sn["delimiter"])
    now_iso = config.iso_now()
    with db.write() as conn:
        account = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not account:
            raise HTTPException(404, "no such account")
        if db.row(conn, "SELECT id FROM imports WHERE sha256 = ? AND undone_at IS NULL", (sha,)):
            raise HTTPException(409, "this exact file was already imported")
        pid = None
        if save_profile_as:
            conn.execute("""INSERT INTO csv_profiles (name, account_id, column_map, date_format, negate, skip_rows)
                            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET account_id = excluded.account_id,
                            column_map = excluded.column_map, date_format = excluded.date_format, negate = excluded.negate,
                            skip_rows = excluded.skip_rows""",
                         (save_profile_as.strip(), account_id, json.dumps(prof["column_map"]), prof.get("date_format") or "%m/%d/%Y",
                          int(prof.get("negate") or 0), int(prof.get("skip_rows") or 0)))
            pid = db.scalar(conn, "SELECT id FROM csv_profiles WHERE name = ?", (save_profile_as.strip(),))
        cur = conn.execute("INSERT INTO imports (kind, filename, sha256, account_id, profile_id, started_at) VALUES ('csv', ?, ?, ?, ?, ?)",
                           (file.filename, sha, account_id, pid, now_iso))
        import_id = cur.lastrowid
        existing = db.rows(conn, "SELECT date, amount_cents, payee_raw, description_raw, external_id FROM transactions WHERE account_id = ?", (account_id,))
        ext = {e["external_id"] for e in existing if e["external_id"]}
        new_ids, skipped = [], 0
        for r in rows:
            if r["external_id"] in ext or csvmap.find_fuzzy_duplicate(existing, r):
                skipped += 1
                continue
            new_ids.append(sync.insert_transaction(conn, account, r, "csv", import_id=import_id, now_iso=now_iso))
            ext.add(r["external_id"])
        if new_ids:
            rules.apply_rules(conn, new_ids)
            transfers.detect_and_apply(conn, min(r["date"] for r in rows))
        conn.execute("UPDATE imports SET inserted = ?, skipped = ?, finished_at = ?, error = ? WHERE id = ?",
                     (len(new_ids), skipped, config.iso_now(), "; ".join(errors[:10]) or None, import_id))
        audit.log(conn, actor(request), "import", import_id, "csv", None, {"file": file.filename, "inserted": len(new_ids), "skipped": skipped})
    return {"ok": True, "import_id": import_id, "inserted": len(new_ids), "skipped": skipped, "errors": errors[:20]}


@router.get("/imports")
def list_imports():
    with db.read() as conn:
        return db.rows(conn, "SELECT i.*, a.name AS account_name FROM imports i LEFT JOIN accounts a ON a.id = i.account_id ORDER BY i.id DESC LIMIT 100")


@router.post("/imports/{import_id}/undo")
def undo_import(import_id: int, request: Request):
    require_owner(request)
    with db.write() as conn:
        imp = db.row(conn, "SELECT * FROM imports WHERE id = ?", (import_id,))
        if not imp:
            raise HTTPException(404, "no such import")
        n = conn.execute("DELETE FROM transactions WHERE import_id = ? AND status = 'new' AND notes = '' "
                         "AND NOT EXISTS (SELECT 1 FROM attachments a WHERE a.transaction_id = transactions.id)", (import_id,)).rowcount
        left = db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE import_id = ?", (import_id,))
        conn.execute("UPDATE imports SET undone_at = ? WHERE id = ?", (config.iso_now(), import_id))
        audit.log(conn, actor(request), "import", import_id, "undo", None, {"deleted": n, "kept": left})
    return {"ok": True, "deleted": n, "kept_reviewed": left}
