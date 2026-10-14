"""Jobs, backups, integrity, settings."""

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import backup
import config
import db
import scheduler
from api import require_owner

router = APIRouter(prefix="/api/system")


@router.get("/jobs")
def jobs():
    return scheduler.status()


@router.post("/jobs/{name}/run")
def run_job(name: str, request: Request):
    require_owner(request)
    try:
        started = scheduler.run_now(name)
    except KeyError:
        raise HTTPException(404, "no job called %s" % name)
    if not started:
        raise HTTPException(409, "%s is already running" % name)
    return {"ok": True}


@router.get("/backups")
def backups():
    return backup.list_backups()


@router.post("/backup")
def backup_now(request: Request):
    require_owner(request)
    return backup.run()


@router.post("/backup/verify")
def backup_verify(request: Request):
    require_owner(request)
    return backup.verify()


@router.get("/backup-key/fingerprint")
def key_fingerprint():
    with db.read() as conn:
        ack = db.scalar(conn, "SELECT value FROM settings WHERE key = 'backup_key_acknowledged'")
    return {"fingerprint": backup.fingerprint(), "acknowledged": ack == "true"}


@router.post("/backup-key/acknowledge")
def key_ack(request: Request):
    require_owner(request)
    with db.write() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('backup_key_acknowledged', 'true') "
                     "ON CONFLICT(key) DO UPDATE SET value = 'true'")
    return {"ok": True}


@router.get("/integrity")
def integrity():
    return db.integrity_check()


@router.get("/settings")
def get_settings():
    out = {}
    with db.read() as conn:
        for r in db.rows(conn, "SELECT key, value FROM settings"):
            try:
                out[r["key"]] = json.loads(r["value"])
            except ValueError:
                out[r["key"]] = r["value"]
    out["_config"] = {k: v for k, v in config.CFG.items() if not k.startswith("_")}
    return out


class SettingReq(BaseModel):
    key: str
    value: object


@router.put("/settings")
def put_setting(req: SettingReq, request: Request):
    require_owner(request)
    if req.key.startswith("_"):
        raise HTTPException(400, "config.json values are edited on disk, not here")
    with db.write() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (req.key, json.dumps(req.value)))
    return {"ok": True}
