from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import config
import db
import transfers as T
from api import actor

router = APIRouter(prefix="/api/transfers")


def _rows(conn, where, args=(), limit=200):
    return db.rows(conn, """
        SELECT tr.*, o.date AS out_date, o.amount_cents AS out_cents, o.payee_raw AS out_text, o.account_id AS out_account,
               i.date AS in_date, i.amount_cents AS in_cents, i.payee_raw AS in_text, i.account_id AS in_account,
               (SELECT name FROM accounts WHERE id = o.account_id) AS out_account_name,
               (SELECT name FROM accounts WHERE id = i.account_id) AS in_account_name
        FROM transfers tr JOIN transactions o ON o.id = tr.out_txn_id JOIN transactions i ON i.id = tr.in_txn_id
        WHERE %s ORDER BY o.date DESC LIMIT ?""" % where, tuple(args) + (limit,))


@router.get("")
def list_transfers(status: str | None = None, limit: int | None = 200):
    with db.read() as conn:
        if status:
            return _rows(conn, "tr.status = ?", (status,), limit or 200)
        return _rows(conn, "tr.status <> 'rejected'", (), limit or 200)


class LinkReq(BaseModel):
    out_txn_id: int
    in_txn_id: int


@router.post("")
def link(req: LinkReq, request: Request):
    with db.write() as conn:
        o = db.row(conn, "SELECT * FROM transactions WHERE id = ?", (req.out_txn_id,))
        i = db.row(conn, "SELECT * FROM transactions WHERE id = ?", (req.in_txn_id,))
        if not o or not i:
            raise HTTPException(404, "no such transaction")
        if o["amount_cents"] >= 0 or i["amount_cents"] <= 0:
            raise HTTPException(400, "out must be negative and in positive")
        if o["amount_cents"] != -i["amount_cents"]:
            raise HTTPException(400, "amounts are not opposite (%d vs %d)" % (o["amount_cents"], i["amount_cents"]))
        conn.execute("DELETE FROM transfers WHERE out_txn_id = ? AND in_txn_id = ? AND status = 'rejected'", (req.out_txn_id, req.in_txn_id))
        tr = T.link(conn, req.out_txn_id, req.in_txn_id, "confirmed", config.iso_now(), actor=actor(request))
        audit.log(conn, actor(request), "transfer", tr, "link", None, req.model_dump())
    return {"ok": True, "id": tr}


@router.post("/{tr_id}/confirm")
def confirm(tr_id: int, request: Request):
    with db.write() as conn:
        tr = db.row(conn, "SELECT * FROM transfers WHERE id = ?", (tr_id,))
        if not tr:
            raise HTTPException(404, "no such transfer")
        conn.execute("UPDATE transfers SET status = 'confirmed' WHERE id = ?", (tr_id,))
        T.categorise_as_transfer(conn, tr["out_txn_id"], tr["in_txn_id"], actor=actor(request))
        audit.log(conn, actor(request), "transfer", tr_id, "confirm")
    return {"ok": True}


@router.post("/{tr_id}/reject")
def reject(tr_id: int, request: Request):
    with db.write() as conn:
        tr = db.row(conn, "SELECT * FROM transfers WHERE id = ?", (tr_id,))
        if not tr:
            raise HTTPException(404, "no such transfer")
        conn.execute("UPDATE transfers SET status = 'rejected' WHERE id = ?", (tr_id,))
        if tr["status"] in ("auto", "confirmed"):
            # undo the transfer categorisation: back to uncategorised, unreviewed
            for tid in (tr["out_txn_id"], tr["in_txn_id"]):
                amt = db.scalar(conn, "SELECT amount_cents FROM transactions WHERE id = ?", (tid,))
                db.set_splits(conn, tid, [{"amount_cents": amt}], "import")
                conn.execute("UPDATE transactions SET status = 'new' WHERE id = ?", (tid,))
        audit.log(conn, actor(request), "transfer", tr_id, "reject")
    return {"ok": True}


@router.delete("/{tr_id}")
def delete(tr_id: int, request: Request):
    return reject(tr_id, request)


@router.post("/detect")
def detect(request: Request, since: str | None = None):
    with db.write() as conn:
        since = since or db.scalar(conn, "SELECT MIN(date) FROM transactions") or "2000-01-01"
        made = T.detect_and_apply(conn, since)
        audit.log(conn, actor(request), "transfer", 0, "detect", None, {"made": len(made)})
    return {"ok": True, "made": len(made)}
