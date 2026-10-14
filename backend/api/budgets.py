from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import config
import db
from api import actor

router = APIRouter(prefix="/api/budgets")


def _month_range(month):
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m // 12), m % 12 + 1, 1)
    return start.isoformat(), (end.fromordinal(end.toordinal() - 1)).isoformat()


@router.get("")
def get_budgets(month: str | None = None):
    month = month or config.today().strftime("%Y-%m")
    frm, to = _month_range(month)
    with db.read() as conn:
        cats = db.rows(conn, """SELECT c.id, c.name, c.parent_id, c.kind, c.default_business,
                                       (SELECT name FROM categories p WHERE p.id = c.parent_id) AS parent_name
                                FROM categories c WHERE c.kind = 'expense' AND c.is_archived = 0 AND c.default_business = 0
                                  AND c.parent_id IS NOT NULL ORDER BY c.sort, c.name""")
        budgets = {r["category_id"]: r["amount_cents"] for r in db.rows(conn, "SELECT * FROM budgets WHERE month = ?", (month,))}
        actual = {r["category_id"]: r["cents"] for r in db.rows(conn, """
            SELECT s.category_id, SUM(-s.amount_cents) AS cents FROM splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE t.pending = 0 AND t.date BETWEEN ? AND ? AND s.amount_cents < 0 AND s.business = 0 GROUP BY s.category_id""", (frm, to))}
        prev_month = (date.fromisoformat(frm).replace(day=1) - date.resolution).strftime("%Y-%m")
        pf, pt = _month_range(prev_month)
        prev = {r["category_id"]: r["cents"] for r in db.rows(conn, """
            SELECT s.category_id, SUM(-s.amount_cents) AS cents FROM splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE t.pending = 0 AND t.date BETWEEN ? AND ? AND s.amount_cents < 0 AND s.business = 0 GROUP BY s.category_id""", (pf, pt))}
        uncategorized = db.scalar(conn, """SELECT COALESCE(SUM(-s.amount_cents), 0) FROM splits s JOIN transactions t ON t.id = s.transaction_id
            WHERE t.pending = 0 AND t.date BETWEEN ? AND ? AND s.amount_cents < 0 AND s.business = 0 AND s.category_id IS NULL""", (frm, to))
    rows = []
    for c in cats:
        b = budgets.get(c["id"])
        a = actual.get(c["id"], 0)
        rows.append({**c, "budget_cents": b, "actual_cents": a, "prev_cents": prev.get(c["id"], 0),
                     "remaining_cents": (b - a) if b is not None else None})
    return {"month": month, "from": frm, "to": to, "rows": rows,
            "budget_total_cents": sum(r["budget_cents"] or 0 for r in rows),
            "actual_total_cents": sum(r["actual_cents"] for r in rows) + uncategorized,
            "uncategorized_cents": uncategorized}


class BudgetReq(BaseModel):
    amount_cents: int | None = None


@router.put("/{category_id}/{month}")
def put_budget(category_id: int, month: str, req: BudgetReq, request: Request):
    with db.write() as conn:
        if req.amount_cents is None:
            conn.execute("DELETE FROM budgets WHERE category_id = ? AND month = ?", (category_id, month))
        else:
            conn.execute("""INSERT INTO budgets (category_id, month, amount_cents) VALUES (?, ?, ?)
                            ON CONFLICT(category_id, month) DO UPDATE SET amount_cents = excluded.amount_cents""",
                         (category_id, month, req.amount_cents))
        audit.log(conn, actor(request), "budget", category_id, "put", None, {"month": month, "amount_cents": req.amount_cents})
    return {"ok": True}


@router.post("/copy")
def copy_month(request: Request, frm: str, to: str):
    if not (len(frm) == 7 and len(to) == 7):
        raise HTTPException(400, "months look like 2026-09")
    with db.write() as conn:
        n = conn.execute("""INSERT INTO budgets (category_id, month, amount_cents)
                            SELECT category_id, ?, amount_cents FROM budgets WHERE month = ?
                            ON CONFLICT(category_id, month) DO UPDATE SET amount_cents = excluded.amount_cents""", (to, frm)).rowcount
        audit.log(conn, actor(request), "budget", 0, "copy", None, {"from": frm, "to": to, "n": n})
    return {"ok": True, "copied": n}
