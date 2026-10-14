from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import config
import db
import rules
import sync
from api import actor

router = APIRouter(prefix="/api/transactions")

SORTS = {"date": "t.date DESC, t.id DESC", "date_asc": "t.date ASC, t.id ASC",
         "amount": "t.amount_cents ASC", "amount_desc": "t.amount_cents DESC", "payee": "t.payee_name, t.date DESC"}


def _splits(conn, ids):
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    out = {}
    for s in db.rows(conn, """SELECT s.*, c.name AS category_name, c.kind AS category_kind,
                                     COALESCE(s.schedule_c_line, c.default_schedule_c_line) AS effective_line
                              FROM splits s LEFT JOIN categories c ON c.id = s.category_id
                              WHERE s.transaction_id IN (%s) ORDER BY s.id""" % marks, tuple(ids)):
        out.setdefault(s["transaction_id"], []).append(s)
    return out


def _tags(conn, ids):
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    out = {}
    for r in db.rows(conn, "SELECT tt.transaction_id, g.name FROM transaction_tags tt JOIN tags g ON g.id = tt.tag_id "
                           "WHERE tt.transaction_id IN (%s)" % marks, tuple(ids)):
        out.setdefault(r["transaction_id"], []).append(r["name"])
    return out


def _suggestions(conn, ids):
    """Suggested transfers touching these rows."""
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    out = {}
    for r in db.rows(conn, """SELECT tr.id, tr.out_txn_id, tr.in_txn_id, tr.status,
                                     o.date AS out_date, o.account_id AS out_account, i.date AS in_date, i.account_id AS in_account,
                                     (SELECT name FROM accounts WHERE id = o.account_id) AS out_account_name,
                                     (SELECT name FROM accounts WHERE id = i.account_id) AS in_account_name
                              FROM transfers tr JOIN transactions o ON o.id = tr.out_txn_id JOIN transactions i ON i.id = tr.in_txn_id
                              WHERE tr.status = 'suggested' AND (tr.out_txn_id IN (%s) OR tr.in_txn_id IN (%s))""" % (marks, marks),
                     tuple(ids) + tuple(ids)):
        out.setdefault(r["out_txn_id"], []).append(r)
        out.setdefault(r["in_txn_id"], []).append(r)
    return out


def hydrate(conn, rows):
    ids = [r["id"] for r in rows]
    sp, tg, sg = _splits(conn, ids), _tags(conn, ids), _suggestions(conn, ids)
    for r in rows:
        r["splits"] = sp.get(r["id"], [])
        r["tags"] = tg.get(r["id"], [])
        r["transfer_suggestions"] = sg.get(r["id"], [])
    return rows


def _list(conn, params, base_where="1=1", everything=False):
    where, args = [base_where], []
    if params.get("account"):
        where.append("t.account_id = ?")
        args.append(params["account"])
    if params.get("frm"):
        where.append("t.date >= ?")
        args.append(params["frm"])
    if params.get("to"):
        where.append("t.date <= ?")
        args.append(params["to"])
    if params.get("status"):
        where.append("t.status = ?")
        args.append(params["status"])
    if params.get("business") in ("0", "1", 0, 1):
        where.append("t.is_business = ?")
        args.append(int(params["business"]))
    if params.get("category"):
        where.append("EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id AND (s.category_id = ? OR s.category_id IN (SELECT id FROM categories WHERE parent_id = ?)))")
        args += [params["category"], params["category"]]
    if params.get("uncategorized"):
        where.append("EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id AND s.category_id IS NULL)")
    if params.get("payee"):
        where.append("t.payee_id = ?")
        args.append(params["payee"])
    if params.get("q"):
        where.append("(t.payee_raw LIKE ? OR t.description_raw LIKE ? OR t.memo_raw LIKE ? OR t.notes LIKE ? OR t.payee_name LIKE ?)")
        args += ["%" + params["q"] + "%"] * 5
    if params.get("min") is not None:
        where.append("ABS(t.amount_cents) >= ?")
        args.append(params["min"])
    if params.get("max") is not None:
        where.append("ABS(t.amount_cents) <= ?")
        args.append(params["max"])
    if params.get("pending") in ("0", "1", 0, 1):
        where.append("t.pending = ?")
        args.append(int(params["pending"]))
    if params.get("has_attachment") in ("0", "1", 0, 1):
        where.append("t.has_attachment = ?")
        args.append(int(params["has_attachment"]))
    if params.get("transfers") == "exclude":
        where.append("t.transfer_id IS NULL")
    sort = SORTS.get(params.get("sort") or "date", SORTS["date"])
    per = max(1, min(500, int(params.get("per") or 100)))
    page = max(1, int(params.get("page") or 1))
    w = " AND ".join(where)
    total = db.scalar(conn, "SELECT COUNT(*) FROM v_transactions t WHERE " + w, tuple(args))
    if everything:  # the inbox must show every unreviewed row, however many a backfill adds
        per, page = max(1, total), 1
    sums = db.row(conn, "SELECT COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents END), 0) AS inflow, "
                        "COALESCE(SUM(CASE WHEN amount_cents < 0 THEN amount_cents END), 0) AS outflow FROM v_transactions t WHERE " + w, tuple(args))
    rows = db.rows(conn, "SELECT t.* FROM v_transactions t WHERE %s ORDER BY %s LIMIT ? OFFSET ?" % (w, sort),
                   tuple(args) + (per, (page - 1) * per))
    return {"total": total, "page": page, "per": per, "inflow": sums["inflow"], "outflow": sums["outflow"],
            "rows": hydrate(conn, rows)}


@router.get("")
def list_transactions(account: int | None = None, frm: str | None = None, to: str | None = None,
                      status: str | None = None, business: str | None = None, category: int | None = None,
                      uncategorized: int | None = None, payee: int | None = None, q: str | None = None,
                      min: int | None = None, max: int | None = None, pending: str | None = None,
                      has_attachment: str | None = None, transfers: str | None = None,
                      sort: str | None = None, page: int | None = 1, per: int | None = 100):
    with db.read() as conn:
        return _list(conn, locals())


@router.get("/inbox")
def inbox(account: int | None = None):
    with db.read() as conn:
        out = _list(conn, {"account": account, "status": "new", "sort": "date_asc"}, everything=True)
        out["vanished"] = db.rows(conn, "SELECT id, date, amount_cents, payee_raw, account_name FROM v_transactions "
                                        "WHERE vanished_at IS NOT NULL AND pending = 1 ORDER BY date")
        return out


@router.get("/{txn_id}")
def get_one(txn_id: int):
    with db.read() as conn:
        t = db.row(conn, "SELECT * FROM v_transactions WHERE id = ?", (txn_id,))
        if not t:
            raise HTTPException(404, "no such transaction")
        hydrate(conn, [t])
        t["attachments"] = db.rows(conn, "SELECT id, kind, original_name, bytes, added_at FROM attachments WHERE transaction_id = ?", (txn_id,))
        t["transfer"] = db.row(conn, "SELECT * FROM transfers WHERE (out_txn_id = ? OR in_txn_id = ?) AND status <> 'rejected'", (txn_id, txn_id))
        return t


class ManualReq(BaseModel):
    account_id: int
    date: str
    amount_cents: int
    payee: str | None = ""
    memo: str | None = ""
    notes: str | None = ""
    category_id: int | None = None
    business: int | None = 0
    schedule_c_line: str | None = None
    deductible_pct: int | None = None
    income_source_id: int | None = None
    reviewed: bool | None = True


@router.post("")
def create_manual(req: ManualReq, request: Request):
    with db.write() as conn:
        account = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (req.account_id,))
        if not account:
            raise HTTPException(404, "no such account")
        tid = sync.insert_transaction(conn, account, {"date": req.date, "posted_date": req.date, "amount_cents": req.amount_cents,
                                                       "payee_raw": req.payee or "", "description_raw": req.payee or "",
                                                       "memo_raw": req.memo or "", "external_id": None, "pending": 0}, "manual")
        # A manual row with a category (or marked reviewed) is the person's decision.
        # One typed in with neither is just a row - rules and transfer detection may still shape it.
        decided = bool(req.category_id) or bool(req.reviewed)
        db.set_splits(conn, tid, [{"amount_cents": req.amount_cents, "category_id": req.category_id, "business": req.business,
                                   "schedule_c_line": req.schedule_c_line, "deductible_pct": req.deductible_pct,
                                   "income_source_id": req.income_source_id}], "user" if decided else "import")
        conn.execute("UPDATE transactions SET notes = ?, status = ? WHERE id = ?",
                     (req.notes or "", "reviewed" if req.reviewed else "new", tid))
        audit.log(conn, actor(request), "transaction", tid, "create", None, req.model_dump())
        t = db.row(conn, "SELECT * FROM v_transactions WHERE id = ?", (tid,))
        return hydrate(conn, [t])[0]


class TxnPatch(BaseModel):
    payee_id: int | None = None
    payee_name: str | None = None      # create-or-find by name
    notes: str | None = None
    date: str | None = None
    status: str | None = None
    tags: list[str] | None = None


@router.patch("/{txn_id}")
def patch(txn_id: int, req: TxnPatch, request: Request):
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM transactions WHERE id = ?", (txn_id,))
        if not before:
            raise HTTPException(404, "no such transaction")
        fields = {}
        if req.payee_name is not None and req.payee_name.strip():
            import payees as P
            name = req.payee_name.strip()
            pid = db.scalar(conn, "SELECT id FROM payees WHERE name = ?", (name,))
            if not pid:
                pid = conn.execute("INSERT INTO payees (name, normalized) VALUES (?, ?)", (name, P.normalize(name))).lastrowid
            fields["payee_id"] = pid
        elif req.payee_id is not None:
            fields["payee_id"] = req.payee_id
        if req.notes is not None:
            fields["notes"] = req.notes
        if req.date is not None:
            fields["date"] = req.date
        if req.status in ("new", "reviewed"):
            fields["status"] = req.status
        if fields:
            fields["updated_at"] = config.iso_now()
            sets = ", ".join("%s = ?" % k for k in fields)
            conn.execute("UPDATE transactions SET %s WHERE id = ?" % sets, tuple(fields.values()) + (txn_id,))
        if req.tags is not None:
            conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ?", (txn_id,))
            for name in req.tags:
                name = name.strip()
                if not name:
                    continue
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                conn.execute("INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, (SELECT id FROM tags WHERE name = ?))", (txn_id, name))
        audit.log(conn, actor(request), "transaction", txn_id, "patch", before, req.model_dump(exclude_none=True))
        t = db.row(conn, "SELECT * FROM v_transactions WHERE id = ?", (txn_id,))
        return hydrate(conn, [t])[0]


class SplitIn(BaseModel):
    amount_cents: int
    category_id: int | None = None
    business: int | None = 0
    deductible_pct: int | None = None
    schedule_c_line: str | None = None
    income_source_id: int | None = None
    note: str | None = ""


class SplitsReq(BaseModel):
    splits: list[SplitIn]
    review: bool | None = False


@router.put("/{txn_id}/splits")
def put_splits(txn_id: int, req: SplitsReq, request: Request):
    with db.write() as conn:
        before = db.rows(conn, "SELECT * FROM splits WHERE transaction_id = ?", (txn_id,))
        parts = []
        for s in req.splits:
            d = s.model_dump()
            if d["deductible_pct"] is None and d["category_id"]:
                d["deductible_pct"] = db.scalar(conn, "SELECT default_deductible_pct FROM categories WHERE id = ?", (d["category_id"],))
            parts.append(d)
        try:
            db.set_splits(conn, txn_id, parts, "user")
        except ValueError as e:
            raise HTTPException(400, str(e))
        if req.review:
            conn.execute("UPDATE transactions SET status = 'reviewed' WHERE id = ?", (txn_id,))
        audit.log(conn, actor(request), "transaction", txn_id, "splits", before, parts)
        t = db.row(conn, "SELECT * FROM v_transactions WHERE id = ?", (txn_id,))
        return hydrate(conn, [t])[0]


@router.post("/{txn_id}/review")
def review(txn_id: int, request: Request):
    with db.write() as conn:
        conn.execute("UPDATE transactions SET status = 'reviewed', updated_at = ? WHERE id = ?", (config.iso_now(), txn_id))
        # A reviewed row's splits count as the person's decision from now on.
        conn.execute("UPDATE splits SET set_by = 'user' WHERE transaction_id = ?", (txn_id,))
        audit.log(conn, actor(request), "transaction", txn_id, "review")
    return {"ok": True}


@router.post("/{txn_id}/unreview")
def unreview(txn_id: int, request: Request):
    with db.write() as conn:
        conn.execute("UPDATE transactions SET status = 'new', updated_at = ? WHERE id = ?", (config.iso_now(), txn_id))
    return {"ok": True}


class BulkReq(BaseModel):
    ids: list[int]
    action: str                         # categorize | business | personal | review | unreview | tag | untag | rules
    category_id: int | None = None
    tag: str | None = None


@router.post("/bulk")
def bulk(req: BulkReq, request: Request):
    n = 0
    with db.write() as conn:
        for tid in req.ids:
            t = db.row(conn, "SELECT * FROM transactions WHERE id = ?", (tid,))
            if not t:
                continue
            if req.action in ("categorize", "business", "personal"):
                sp = db.rows(conn, "SELECT * FROM splits WHERE transaction_id = ? ORDER BY id", (tid,))
                if not sp:
                    sp = [{"amount_cents": t["amount_cents"], "category_id": None, "business": 0, "deductible_pct": 100,
                           "schedule_c_line": None, "income_source_id": None, "note": ""}]
                parts = []
                for s in sp:
                    d = dict(s)
                    if req.action == "categorize":
                        d["category_id"] = req.category_id
                        cat = db.row(conn, "SELECT default_deductible_pct, default_business FROM categories WHERE id = ?", (req.category_id,))
                        d["deductible_pct"] = cat["default_deductible_pct"] if cat else 100
                        # Quick-categorise takes the category's business default too,
                        # so picking "Software & subscriptions" is one keystroke, not two.
                        d["business"] = cat["default_business"] if cat else d.get("business", 0)
                    elif req.action == "business":
                        d["business"] = 1
                    else:
                        d["business"] = 0
                    parts.append(d)
                db.set_splits(conn, tid, parts, "user")
                n += 1
            elif req.action == "review":
                conn.execute("UPDATE transactions SET status = 'reviewed' WHERE id = ?", (tid,))
                conn.execute("UPDATE splits SET set_by = 'user' WHERE transaction_id = ?", (tid,))
                n += 1
            elif req.action == "unreview":
                conn.execute("UPDATE transactions SET status = 'new' WHERE id = ?", (tid,))
                n += 1
            elif req.action == "tag" and req.tag:
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (req.tag,))
                conn.execute("INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, (SELECT id FROM tags WHERE name = ?))", (tid, req.tag))
                n += 1
            elif req.action == "untag" and req.tag:
                conn.execute("DELETE FROM transaction_tags WHERE transaction_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)", (tid, req.tag))
                n += 1
        if req.action == "rules":
            n = rules.apply_rules(conn, req.ids)
        audit.log(conn, actor(request), "transaction", 0, "bulk:" + req.action, None, {"ids": req.ids, "n": n})
    return {"ok": True, "changed": n}


@router.delete("/{txn_id}")
def delete(txn_id: int, request: Request):
    with db.write() as conn:
        t = db.row(conn, "SELECT * FROM transactions WHERE id = ?", (txn_id,))
        if not t:
            raise HTTPException(404, "no such transaction")
        if t["source"] == "simplefin":
            raise HTTPException(400, "a synced row cannot be deleted - it would come back on the next sync. Categorise it as a transfer or ignore it instead.")
        conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
        audit.log(conn, actor(request), "transaction", txn_id, "delete", t, None)
    return {"ok": True}


@router.get("/{txn_id}/audit")
def audit_trail(txn_id: int):
    with db.read() as conn:
        return db.rows(conn, "SELECT * FROM audit_log WHERE entity = 'transaction' AND entity_id = ? ORDER BY id DESC", (txn_id,))
