from fastapi import APIRouter, Request

import db
import networth as N
from api import require_owner

router = APIRouter(prefix="/api/networth")


@router.get("")
def history(frm: str | None = None, to: str | None = None):
    with db.read() as conn:
        return N.history(conn, frm, to)


@router.get("/now")
def now():
    with db.read() as conn:
        return N.compute(conn)


@router.post("/snapshot")
def snapshot(request: Request):
    require_owner(request)
    return N.snapshot_today()
