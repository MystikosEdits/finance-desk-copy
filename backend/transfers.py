"""Money moving between your own accounts is not spending and not income.

detect() is pure. Two rows pair when they are in different accounts, exactly
opposite in amount, within 3 days, and neither is already linked. The text
decides how sure we are: "PAYMENT", "TRANSFER", "ZELLE", or the other
account's institution name make it `auto`; otherwise it is only `suggested`
and the inbox asks. That is why an $80 dinner and an $80 refund elsewhere
never silently become a transfer. Rejected pairs are remembered.
"""

import re

import config
import db

WINDOW_DAYS = 3
_TRANSFER_RX = re.compile(r"transfer|xfer|\bpayment\b|\bpmt\b|\bpymt\b|zelle|autopay|online pay|"
                          r"e-?payment|mobile pay|internet pay|\bach\b|venmo cashout|cash out|deposit from|"
                          r"withdrawal to|to savings|from savings|to checking|from checking", re.I)


def _days(a, b):
    from datetime import date
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def detect(rows, rejected, institutions):
    """rows: dicts with id, account_id, date, amount_cents, text, linked (bool).
    rejected: set of (out_id, in_id). institutions: {account_id: name}.
    -> list of (out_id, in_id, status, score)."""
    outs = [r for r in rows if r["amount_cents"] < 0 and not r.get("linked")]
    ins = {}
    for r in rows:
        if r["amount_cents"] > 0 and not r.get("linked"):
            ins.setdefault(r["amount_cents"], []).append(r)
    cands = []
    for o in outs:
        for i in ins.get(-o["amount_cents"], []):
            if i["account_id"] == o["account_id"]:
                continue
            if (o["id"], i["id"]) in rejected:
                continue
            dd = _days(o["date"], i["date"])
            if dd > WINDOW_DAYS:
                continue
            text = "%s %s" % (o.get("text") or "", i.get("text") or "")
            sure = bool(_TRANSFER_RX.search(text))
            if not sure:
                other_o = (institutions.get(i["account_id"]) or "").lower()
                other_i = (institutions.get(o["account_id"]) or "").lower()
                lo = (o.get("text") or "").lower()
                li = (i.get("text") or "").lower()
                sure = bool(other_o and len(other_o) > 3 and other_o.split()[0] in lo) or \
                       bool(other_i and len(other_i) > 3 and other_i.split()[0] in li)
            score = dd + (0 if sure else 10)
            cands.append((score, o["id"], i["id"]))
    cands.sort()
    used = set()
    out = []
    for score, oid, iid in cands:
        if oid in used or iid in used:
            continue
        used.add(oid)
        used.add(iid)
        out.append((oid, iid, "auto" if score < 10 else "suggested", score))
    return out


def _candidate_rows(conn, since_date):
    return db.rows(conn, """
        SELECT t.id, t.account_id, t.date, t.amount_cents,
               (t.payee_raw || ' ' || t.description_raw || ' ' || t.memo_raw) AS text,
               EXISTS (SELECT 1 FROM transfers tr WHERE (tr.out_txn_id = t.id OR tr.in_txn_id = t.id)
                       AND tr.status <> 'rejected') AS linked
        FROM transactions t
        WHERE t.date >= ? AND t.pending = 0
          AND NOT EXISTS (SELECT 1 FROM splits s JOIN categories c ON c.id = s.category_id
                          WHERE s.transaction_id = t.id AND c.kind <> 'transfer' AND s.set_by = 'user')""",
                   (since_date,))


def link(conn, out_id, in_id, status, now_iso, actor="sync"):
    """Create the pair and, for auto/confirmed, categorise both sides as a transfer."""
    conn.execute("INSERT INTO transfers (out_txn_id, in_txn_id, status, created_at) VALUES (?, ?, ?, ?)",
                 (out_id, in_id, status, now_iso))
    tr_id = db.scalar(conn, "SELECT last_insert_rowid()")
    if status in ("auto", "confirmed"):
        categorise_as_transfer(conn, out_id, in_id, actor)
    return tr_id


def categorise_as_transfer(conn, out_id, in_id, actor="sync"):
    kinds = {r["id"]: r["kind"] for r in db.rows(
        conn, "SELECT t.id, a.kind FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id IN (?, ?)",
        (out_id, in_id))}
    cat = 501 if "credit" in kinds.values() else 500
    for tid in (out_id, in_id):
        amt = db.scalar(conn, "SELECT amount_cents FROM transactions WHERE id = ?", (tid,))
        set_by = "user" if actor != "sync" else "rule:transfer"
        db.set_splits(conn, tid, [{"amount_cents": amt, "category_id": cat}], set_by, keep_user=(actor == "sync"))
        conn.execute("UPDATE transactions SET status = 'reviewed', updated_at = ? WHERE id = ? AND status = 'new'",
                     (config.iso_now(), tid))


def detect_and_apply(conn, since_date):
    rows = _candidate_rows(conn, since_date)
    rejected = {(r["out_txn_id"], r["in_txn_id"]) for r in db.rows(
        conn, "SELECT out_txn_id, in_txn_id FROM transfers WHERE status = 'rejected'")}
    inst = {r["id"]: (r["institution"] or r["name"]) for r in db.rows(conn, "SELECT id, name, institution FROM accounts")}
    pairs = detect(rows, rejected, inst)
    now_iso = config.iso_now()
    made = []
    for oid, iid, status, _score in pairs:
        # a suggestion that already exists is left alone
        if db.scalar(conn, "SELECT 1 FROM transfers WHERE out_txn_id = ? AND in_txn_id = ? AND status <> 'rejected'", (oid, iid)):
            continue
        made.append(link(conn, oid, iid, status, now_iso))
    return made
