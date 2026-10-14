from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import db
from api import actor

router = APIRouter(prefix="/api/categories")


@router.get("")
def list_categories(include_archived: bool = False):
    with db.read() as conn:
        rows = db.rows(conn, "SELECT c.*, (SELECT COUNT(*) FROM splits s WHERE s.category_id = c.id) AS uses FROM categories c"
                       + ("" if include_archived else " WHERE c.is_archived = 0") + " ORDER BY c.sort, c.name")
        by_id = {r["id"]: r for r in rows}
        for r in rows:
            p = by_id.get(r["parent_id"]) if r["parent_id"] else None
            r["full_name"] = ("%s › %s" % (p["name"], r["name"])) if p else r["name"]
            r["kind_effective"] = r["kind"]
        return rows


class CategoryReq(BaseModel):
    name: str
    kind: str
    parent_id: int | None = None
    default_business: int | None = 0
    default_schedule_c_line: str | None = None
    default_deductible_pct: int | None = 100
    icon: str | None = ""
    sort: int | None = 0


@router.post("")
def create(req: CategoryReq, request: Request):
    if req.kind not in ("income", "expense", "transfer"):
        raise HTTPException(400, "kind must be income, expense or transfer")
    with db.write() as conn:
        try:
            cur = conn.execute("""INSERT INTO categories (parent_id, name, kind, default_business, default_schedule_c_line,
                                  default_deductible_pct, icon, sort) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                               (req.parent_id, req.name.strip(), req.kind, req.default_business or 0,
                                req.default_schedule_c_line or None, req.default_deductible_pct or 100, req.icon or "", req.sort or 0))
        except Exception as e:
            raise HTTPException(400, "could not create: %s" % e)
        audit.log(conn, actor(request), "category", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM categories WHERE id = ?", (cur.lastrowid,))


class CategoryPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    parent_id: int | None = None
    default_business: int | None = None
    default_schedule_c_line: str | None = None
    default_deductible_pct: int | None = None
    icon: str | None = None
    sort: int | None = None
    is_archived: int | None = None
    clear_line: bool | None = None


@router.patch("/{cat_id}")
def patch(cat_id: int, req: CategoryPatch, request: Request):
    fields = {k: v for k, v in req.model_dump().items() if v is not None and k != "clear_line"}
    if req.clear_line:
        fields["default_schedule_c_line"] = None
    if not fields:
        raise HTTPException(400, "nothing to change")
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM categories WHERE id = ?", (cat_id,))
        if not before:
            raise HTTPException(404, "no such category")
        sets = ", ".join("%s = ?" % k for k in fields)
        conn.execute("UPDATE categories SET %s WHERE id = ?" % sets, tuple(fields.values()) + (cat_id,))
        audit.log(conn, actor(request), "category", cat_id, "patch", before, fields)
        return db.row(conn, "SELECT * FROM categories WHERE id = ?", (cat_id,))


@router.post("/{cat_id}/merge-into/{other_id}")
def merge(cat_id: int, other_id: int, request: Request):
    if cat_id == other_id:
        raise HTTPException(400, "same category")
    with db.write() as conn:
        if not db.row(conn, "SELECT 1 FROM categories WHERE id = ?", (other_id,)):
            raise HTTPException(404, "no such target")
        n = conn.execute("UPDATE splits SET category_id = ? WHERE category_id = ?", (other_id, cat_id)).rowcount
        conn.execute("UPDATE payees SET default_category_id = ? WHERE default_category_id = ?", (other_id, cat_id))
        conn.execute("UPDATE budgets SET category_id = ? WHERE category_id = ? AND NOT EXISTS (SELECT 1 FROM budgets b WHERE b.category_id = ? AND b.month = budgets.month)", (other_id, cat_id, other_id))
        conn.execute("DELETE FROM budgets WHERE category_id = ?", (cat_id,))
        conn.execute("UPDATE categories SET parent_id = ? WHERE parent_id = ?", (other_id, cat_id))
        conn.execute("UPDATE categories SET is_archived = 1 WHERE id = ?", (cat_id,))
        audit.log(conn, actor(request), "category", cat_id, "merge", {"into": other_id, "splits": n})
    return {"ok": True, "moved": n}
