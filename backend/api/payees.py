from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import db
import payees as P
from api import actor

router = APIRouter(prefix="/api/payees")


@router.get("")
def list_payees(q: str | None = None, contractors: int | None = None, limit: int | None = 500):
    with db.read() as conn:
        where, args = ["1=1"], []
        if q:
            where.append("(p.name LIKE ? OR p.normalized LIKE ?)")
            args += ["%" + q + "%", "%" + q.lower() + "%"]
        if contractors:
            where.append("p.is_contractor = 1")
        return db.rows(conn, """
            SELECT p.*, c.name AS default_category_name,
                   (SELECT COUNT(*) FROM transactions t WHERE t.payee_id = p.id) AS txn_count,
                   (SELECT COALESCE(SUM(t.amount_cents), 0) FROM transactions t WHERE t.payee_id = p.id) AS total_cents,
                   (SELECT MAX(t.date) FROM transactions t WHERE t.payee_id = p.id) AS last_date
            FROM payees p LEFT JOIN categories c ON c.id = p.default_category_id
            WHERE %s ORDER BY txn_count DESC, p.name LIMIT ?""" % " AND ".join(where), tuple(args) + (limit or 500,))


class PayeeReq(BaseModel):
    name: str
    default_category_id: int | None = None
    default_business: int | None = None
    is_contractor: int | None = 0
    contractor_kind: str | None = None
    tin_on_file: int | None = 0
    w9_on_file: int | None = 0
    contact: str | None = ""


@router.post("")
def create(req: PayeeReq, request: Request):
    with db.write() as conn:
        if db.row(conn, "SELECT 1 FROM payees WHERE name = ?", (req.name.strip(),)):
            raise HTTPException(400, "a payee with that name exists")
        cur = conn.execute("""INSERT INTO payees (name, normalized, default_category_id, default_business, is_contractor,
                              contractor_kind, tin_on_file, w9_on_file, contact) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (req.name.strip(), P.normalize(req.name), req.default_category_id, req.default_business,
                            req.is_contractor or 0, req.contractor_kind, req.tin_on_file or 0, req.w9_on_file or 0, req.contact or ""))
        audit.log(conn, actor(request), "payee", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM payees WHERE id = ?", (cur.lastrowid,))


class PayeePatch(BaseModel):
    name: str | None = None
    default_category_id: int | None = None
    clear_default_category: bool | None = None
    default_business: int | None = None
    clear_default_business: bool | None = None
    is_contractor: int | None = None
    contractor_kind: str | None = None
    tin_on_file: int | None = None
    w9_on_file: int | None = None
    contact: str | None = None
    apply_to_existing: bool | None = None   # push the default category/business onto untouched splits


@router.patch("/{payee_id}")
def patch(payee_id: int, req: PayeePatch, request: Request):
    fields = {k: v for k, v in req.model_dump().items() if v is not None
              and k not in ("clear_default_category", "clear_default_business", "apply_to_existing")}
    if req.clear_default_category:
        fields["default_category_id"] = None
    if req.clear_default_business:
        fields["default_business"] = None
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM payees WHERE id = ?", (payee_id,))
        if not before:
            raise HTTPException(404, "no such payee")
        if fields:
            if "name" in fields:
                fields["normalized"] = P.normalize(fields["name"])
            sets = ", ".join("%s = ?" % k for k in fields)
            conn.execute("UPDATE payees SET %s WHERE id = ?" % sets, tuple(fields.values()) + (payee_id,))
        n = 0
        if req.apply_to_existing:
            p = db.row(conn, "SELECT * FROM payees WHERE id = ?", (payee_id,))
            for t in db.rows(conn, "SELECT id, amount_cents FROM transactions WHERE payee_id = ?", (payee_id,)):
                sp = db.rows(conn, "SELECT * FROM splits WHERE transaction_id = ? ORDER BY id", (t["id"],))
                if len(sp) != 1:
                    continue
                d = dict(sp[0])
                if p["default_category_id"]:
                    d["category_id"] = p["default_category_id"]
                    d["deductible_pct"] = db.scalar(conn, "SELECT default_deductible_pct FROM categories WHERE id = ?", (p["default_category_id"],)) or 100
                if p["default_business"] is not None:
                    d["business"] = p["default_business"]
                if db.set_splits(conn, t["id"], [d], "rule:payee", keep_user=True):
                    n += 1
        audit.log(conn, actor(request), "payee", payee_id, "patch", before, dict(fields, applied=n))
        out = db.row(conn, "SELECT * FROM payees WHERE id = ?", (payee_id,))
        out["applied"] = n
        return out


@router.post("/{payee_id}/merge-into/{other_id}")
def merge(payee_id: int, other_id: int, request: Request):
    if payee_id == other_id:
        raise HTTPException(400, "same payee")
    with db.write() as conn:
        if not db.row(conn, "SELECT 1 FROM payees WHERE id = ?", (other_id,)):
            raise HTTPException(404, "no such target")
        n = conn.execute("UPDATE transactions SET payee_id = ? WHERE payee_id = ?", (other_id, payee_id)).rowcount
        conn.execute("UPDATE attachments SET payee_id = ? WHERE payee_id = ?", (other_id, payee_id))
        conn.execute("UPDATE recurring SET payee_id = ? WHERE payee_id = ?", (other_id, payee_id))
        conn.execute("DELETE FROM payees WHERE id = ?", (payee_id,))
        audit.log(conn, actor(request), "payee", payee_id, "merge", {"into": other_id, "moved": n})
    return {"ok": True, "moved": n}


@router.get("/{payee_id}/transactions")
def payee_transactions(payee_id: int, limit: int | None = 200):
    from api.transactions import hydrate
    with db.read() as conn:
        rows = db.rows(conn, "SELECT * FROM v_transactions WHERE payee_id = ? ORDER BY date DESC LIMIT ?", (payee_id, limit or 200))
        return hydrate(conn, rows)
