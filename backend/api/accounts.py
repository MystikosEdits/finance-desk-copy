from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import json

import attachments as A
import audit
import config
import db
from api import actor, require_owner

router = APIRouter(prefix="/api/accounts")

KINDS = ("checking", "savings", "credit", "cash", "brokerage", "crypto", "loan", "asset", "liability")
LIABILITY_KINDS = ("credit", "loan", "liability")


def computed_balance(conn, account_id):
    a = db.row(conn, "SELECT opening_balance_cents, opening_date FROM accounts WHERE id = ?", (account_id,))
    s = db.scalar(conn, "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions WHERE account_id = ? AND pending = 0"
                        + (" AND date >= ?" if a["opening_date"] else ""),
                  (account_id, a["opening_date"]) if a["opening_date"] else (account_id,))
    return a["opening_balance_cents"] + s


def with_balance(conn, a):
    a = dict(a)
    b = db.row(conn, "SELECT balance_cents, available_cents, date, source FROM account_balances "
                     "WHERE account_id = ? ORDER BY date DESC LIMIT 1", (a["id"],))
    if a["kind"] in ("asset", "liability") and a.get("manual_value_cents") is not None:
        a["balance_cents"], a["balance_date"], a["balance_source"] = a["manual_value_cents"], None, "manual"
    elif b:
        a["balance_cents"], a["balance_date"], a["balance_source"] = b["balance_cents"], b["date"], b["source"]
        a["available_cents"] = b["available_cents"]
    else:
        a["balance_cents"], a["balance_date"], a["balance_source"] = computed_balance(conn, a["id"]), None, "computed"
    a["computed_cents"] = computed_balance(conn, a["id"]) if a["kind"] not in ("asset", "liability", "brokerage", "crypto") else None
    a["is_liability"] = a["kind"] in LIABILITY_KINDS
    a["txn_count"] = db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (a["id"],))
    a["new_count"] = db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND status = 'new'", (a["id"],))
    a["last_txn_date"] = db.scalar(conn, "SELECT MAX(date) FROM transactions WHERE account_id = ?", (a["id"],))
    return a


@router.get("")
def list_accounts(include_closed: bool = False):
    with db.read() as conn:
        rows = db.rows(conn, "SELECT * FROM accounts" + ("" if include_closed else " WHERE is_closed = 0")
                       + " ORDER BY sort, kind, name")
        return [with_balance(conn, a) for a in rows]


class AccountReq(BaseModel):
    name: str
    kind: str
    institution: str | None = ""
    default_business: str | None = "personal"
    opening_balance_cents: int | None = 0
    opening_date: str | None = None
    apr_bp: int | None = None
    min_payment_cents: int | None = None
    due_day: int | None = None
    manual_value_cents: int | None = None
    balance_sign: int | None = 1
    sort: int | None = 0


@router.post("")
def create(req: AccountReq, request: Request):
    if req.kind not in KINDS:
        raise HTTPException(400, "kind must be one of %s" % ", ".join(KINDS))
    with db.write() as conn:
        cur = conn.execute("""INSERT INTO accounts (name, kind, institution, default_business, opening_balance_cents,
                              opening_date, apr_bp, min_payment_cents, due_day, manual_value_cents, balance_sign, sort, created_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (req.name.strip(), req.kind, req.institution or "", req.default_business or "personal",
                            req.opening_balance_cents or 0, req.opening_date, req.apr_bp, req.min_payment_cents,
                            req.due_day, req.manual_value_cents, req.balance_sign or 1, req.sort or 0, config.iso_now()))
        audit.log(conn, actor(request), "account", cur.lastrowid, "create", None, req.model_dump())
        return with_balance(conn, db.row(conn, "SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,)))


class AccountPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    institution: str | None = None
    default_business: str | None = None
    opening_balance_cents: int | None = None
    opening_date: str | None = None
    apr_bp: int | None = None
    min_payment_cents: int | None = None
    due_day: int | None = None
    manual_value_cents: int | None = None
    balance_sign: int | None = None
    sort: int | None = None
    is_closed: int | None = None


@router.patch("/{account_id}")
def patch(account_id: int, req: AccountPatch, request: Request):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "kind" in fields and fields["kind"] not in KINDS:
        raise HTTPException(400, "bad kind")
    if not fields:
        raise HTTPException(400, "nothing to change")
    with db.write() as conn:
        before = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not before:
            raise HTTPException(404, "no such account")
        sets = ", ".join("%s = ?" % k for k in fields)
        conn.execute("UPDATE accounts SET %s WHERE id = ?" % sets, tuple(fields.values()) + (account_id,))
        audit.log(conn, actor(request), "account", account_id, "patch", before, fields)
        return with_balance(conn, db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,)))


class BalanceReq(BaseModel):
    balance_cents: int
    date: str | None = None


@router.post("/{account_id}/balance")
def set_balance(account_id: int, req: BalanceReq, request: Request):
    d = req.date or config.today().isoformat()
    with db.write() as conn:
        if not db.row(conn, "SELECT 1 FROM accounts WHERE id = ?", (account_id,)):
            raise HTTPException(404, "no such account")
        conn.execute("""INSERT INTO account_balances (account_id, date, balance_cents, source) VALUES (?, ?, ?, 'manual')
                        ON CONFLICT(account_id, date) DO UPDATE SET balance_cents = excluded.balance_cents, source = 'manual'""",
                     (account_id, d, req.balance_cents))
        kind = db.scalar(conn, "SELECT kind FROM accounts WHERE id = ?", (account_id,))
        if kind in ("asset", "liability"):
            conn.execute("UPDATE accounts SET manual_value_cents = ? WHERE id = ?", (req.balance_cents, account_id))
        audit.log(conn, actor(request), "account", account_id, "balance", None, {"date": d, "balance_cents": req.balance_cents})
        return with_balance(conn, db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,)))


@router.get("/{account_id}/balances")
def balances(account_id: int, frm: str | None = None, to: str | None = None):
    with db.read() as conn:
        sql = "SELECT date, balance_cents, available_cents, source FROM account_balances WHERE account_id = ?"
        params = [account_id]
        if frm:
            sql += " AND date >= ?"
            params.append(frm)
        if to:
            sql += " AND date <= ?"
            params.append(to)
        return db.rows(conn, sql + " ORDER BY date", tuple(params))


@router.post("/{account_id}/close")
def close(account_id: int, request: Request):
    with db.write() as conn:
        conn.execute("UPDATE accounts SET is_closed = 1 WHERE id = ?", (account_id,))
        audit.log(conn, actor(request), "account", account_id, "close")
    return {"ok": True}


@router.get("/{account_id}/footprint")
def footprint(account_id: int):
    """What deleting this account would take with it."""
    with db.read() as conn:
        a = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not a:
            raise HTTPException(404, "no such account")
        return {
            "name": a["name"], "synced": bool(a["simplefin_id"]),
            "transactions": db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)),
            "reviewed": db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND status = 'reviewed'", (account_id,)),
            "attachments": db.scalar(conn, "SELECT COUNT(*) FROM attachments WHERE transaction_id IN (SELECT id FROM transactions WHERE account_id = ?)", (account_id,)),
            "trades": db.scalar(conn, "SELECT COUNT(*) FROM trades WHERE account_id = ?", (account_id,)),
            "transfers": db.scalar(conn, """SELECT COUNT(*) FROM transfers WHERE out_txn_id IN (SELECT id FROM transactions WHERE account_id = ?)
                                            OR in_txn_id IN (SELECT id FROM transactions WHERE account_id = ?)""", (account_id, account_id)),
        }


@router.delete("/{account_id}")
def delete_account(account_id: int, request: Request, confirm: str = ""):
    """Remove the account and everything that hangs off it: transactions
    (splits, tags, transfers), receipts (files too), balances, trades.
    `confirm` must equal the account's name - this is the one action in the
    app that throws data away, so it is not one click.

    A synced account's SimpleFIN id is remembered in settings so the next
    sync does not simply recreate it; to bring it back later remove the id
    from settings['ignored_simplefin_ids'].
    """
    require_owner(request)
    with db.write() as conn:
        a = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not a:
            raise HTTPException(404, "no such account")
        if confirm.strip() != a["name"]:
            raise HTTPException(400, "type the account name (%s) to confirm" % a["name"])
        txn_ids = [r["id"] for r in db.rows(conn, "SELECT id FROM transactions WHERE account_id = ?", (account_id,))]
        removed = {"transactions": len(txn_ids), "attachments": 0, "trades": 0}
        if txn_ids:
            marks = ",".join("?" for _ in txn_ids)
            for att in db.rows(conn, "SELECT id FROM attachments WHERE transaction_id IN (%s)" % marks, tuple(txn_ids)):
                A.delete(conn, att["id"])
                removed["attachments"] += 1
            conn.execute("UPDATE tax_payments SET txn_id = NULL WHERE txn_id IN (%s)" % marks, tuple(txn_ids))
            conn.execute("UPDATE trades SET cash_txn_id = NULL WHERE cash_txn_id IN (%s)" % marks, tuple(txn_ids))
            conn.execute("UPDATE recurring SET last_seen_txn_id = NULL WHERE last_seen_txn_id IN (%s)" % marks, tuple(txn_ids))
            conn.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))   # splits, tags, transfers cascade
        removed["trades"] = conn.execute("DELETE FROM trades WHERE account_id = ?", (account_id,)).rowcount
        conn.execute("DELETE FROM account_balances WHERE account_id = ?", (account_id,))
        conn.execute("UPDATE csv_profiles SET account_id = NULL WHERE account_id = ?", (account_id,))
        conn.execute("UPDATE imports SET account_id = NULL WHERE account_id = ?", (account_id,))
        if a["simplefin_id"]:
            cur = db.scalar(conn, "SELECT value FROM settings WHERE key = 'ignored_simplefin_ids'")
            ids = json.loads(cur) if cur else []
            if a["simplefin_id"] not in ids:
                ids.append(a["simplefin_id"])
            conn.execute("INSERT INTO settings (key, value) VALUES ('ignored_simplefin_ids', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (json.dumps(ids),))
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        audit.log(conn, actor(request), "account", account_id, "delete", a, removed)
    return {"ok": True, "removed": removed, "ignored_simplefin_id": a["simplefin_id"]}
