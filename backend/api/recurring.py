from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import config
import db
import recurring as R
from api import actor

router = APIRouter(prefix="/api/recurring")


@router.get("")
def list_recurring(status: str | None = None):
    with db.read() as conn:
        rows = R.listing(conn, status)
    confirmed = [r for r in rows if r["status"] == "confirmed" and r["active"]]
    return {"rows": rows,
            "monthly_total_cents": sum(r["monthly_equivalent_cents"] for r in confirmed),
            "monthly_business_cents": sum(r["monthly_equivalent_cents"] for r in confirmed if r["business"]),
            "suggested": sum(1 for r in rows if r["status"] == "suggested"),
            "missed": sum(1 for r in rows if r["missed"])}


@router.post("/detect")
def detect(request: Request):
    with db.write() as conn:
        res = R.detect_and_store(conn)
        audit.log(conn, actor(request), "recurring", 0, "detect", None, res)
    return res


class RecurringPatch(BaseModel):
    status: str | None = None
    name: str | None = None
    cadence: str | None = None
    expected_cents: int | None = None
    next_date: str | None = None
    is_subscription: int | None = None
    business: int | None = None
    active: int | None = None
    tolerance_pct: int | None = None


@router.patch("/{rid}")
def patch(rid: int, req: RecurringPatch, request: Request):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] not in ("suggested", "confirmed", "dismissed"):
        raise HTTPException(400, "bad status")
    if "cadence" in fields and fields["cadence"] not in ("weekly", "biweekly", "monthly", "quarterly", "yearly"):
        raise HTTPException(400, "bad cadence")
    if not fields:
        raise HTTPException(400, "nothing to change")
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM recurring WHERE id = ?", (rid,))
        if not before:
            raise HTTPException(404, "no such row")
        sets = ", ".join("%s = ?" % k for k in fields)
        conn.execute("UPDATE recurring SET %s WHERE id = ?" % sets, tuple(fields.values()) + (rid,))
        audit.log(conn, actor(request), "recurring", rid, "patch", before, fields)
        return db.row(conn, "SELECT * FROM recurring WHERE id = ?", (rid,))


class RecurringReq(BaseModel):
    name: str
    cadence: str
    expected_cents: int
    next_date: str
    payee_id: int | None = None
    is_subscription: int | None = 1
    business: int | None = 0


@router.post("")
def create(req: RecurringReq, request: Request):
    if req.cadence not in ("weekly", "biweekly", "monthly", "quarterly", "yearly"):
        raise HTTPException(400, "bad cadence")
    with db.write() as conn:
        cur = conn.execute("""INSERT INTO recurring (payee_id, name, cadence, expected_cents, next_date, is_subscription, business, active, status)
                              VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'confirmed')""",
                           (req.payee_id, req.name.strip(), req.cadence, req.expected_cents, req.next_date,
                            1 if req.is_subscription is None else req.is_subscription, req.business or 0))
        audit.log(conn, actor(request), "recurring", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM recurring WHERE id = ?", (cur.lastrowid,))


@router.get("/upcoming")
def upcoming(days: int | None = 30):
    from datetime import timedelta
    with db.read() as conn:
        rows = R.listing(conn, "confirmed")
    cutoff = (config.today() + timedelta(days=days or 30)).isoformat()
    return [r for r in rows if r["active"] and r["next_date"] and r["next_date"] <= cutoff]
