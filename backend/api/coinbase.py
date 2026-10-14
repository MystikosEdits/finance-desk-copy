from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import coinbase as CB
import db
import scheduler
import secrets_store
from api import actor, require_owner

router = APIRouter(prefix="/api/coinbase")


@router.get("/status")
def status():
    return CB.status()


class ConnectReq(BaseModel):
    key_name: str
    private_key: str


@router.post("/connect")
def connect(req: ConnectReq, request: Request):
    require_owner(request)
    try:
        CB.save_credentials(req.key_name, req.private_key)
        wallets = CB.test_connection()
    except CB.CoinbaseError as e:
        secrets_store.delete(CB.SECRET_NAME)
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 - network errors get shown, not hidden
        secrets_store.delete(CB.SECRET_NAME)
        raise HTTPException(502, "could not reach Coinbase: %s" % e)
    with db.write() as conn:
        audit.log(conn, actor(request), "coinbase", 0, "connect", None, {"wallets": len(wallets)})
    started = scheduler.run_now("coinbase") if "coinbase" in scheduler._jobs else False
    return {"ok": True, "wallets": wallets, "sync_started": started}


@router.post("/test")
def test(request: Request):
    require_owner(request)
    try:
        return {"ok": True, "wallets": CB.test_connection()}
    except CB.CoinbaseError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "could not reach Coinbase: %s" % e)


@router.post("/sync")
def sync(request: Request):
    require_owner(request)
    if not CB.connected():
        raise HTTPException(400, "Coinbase is not connected")
    if not scheduler.run_now("coinbase"):
        raise HTTPException(409, "a Coinbase sync is already running")
    return {"ok": True}


@router.delete("")
def disconnect(request: Request):
    require_owner(request)
    secrets_store.delete(CB.SECRET_NAME)
    with db.write() as conn:
        audit.log(conn, actor(request), "coinbase", 0, "disconnect")
    return {"ok": True}
