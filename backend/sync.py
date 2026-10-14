"""Bank sync: SimpleFIN feed in, ledger rows out, nothing counted twice.

reconcile() is a pure function over two lists and is what the tests exercise.
run_sync() is the job: fetch over HTTP with NO lock held, then apply each
account's plan inside one short write().

WHY FOUR PASSES
---------------
A pending charge can come back next sync under a NEW id once it posts, with a
slightly different description, sometimes a different amount (a tip). If that
were treated as a new row, every restaurant meal would be counted twice and
the pending twin would linger forever. So:

  1 EXACT   same id -> update only the fields the bank is allowed to change.
  2 REKEY   a posted feed row with no local match, against a local PENDING row
            the feed no longer sends: same amount, close date, similar text ->
            it is the same charge; move the local row to the new id and keep
            everything a person did to it (splits, notes, receipt, transfer).
            Tip sub-pass: amount up to 30% higher, text very similar, and the
            row untouched by a person -> same, and rescale the one split.
  3 INSERT  what is left is genuinely new.
  4 VANISH  a local pending row the feed stopped sending and nothing rekeyed:
            mark it. An untouched one older than 7 days is deleted (a hold
            that never posted). A touched one stays and the inbox says so.

Every rekey is counted in sync_log. After two weeks that column says whether
this bank actually changes ids; if it never does, the pass costs nothing.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import config
import db
import log
import payees
import secrets_store
import simplefin

logger = log.get("sync")

OVERLAP_DAYS = 14
REKEY_DAYS = 5
VANISH_GRACE_DAYS = 7
TIP_MAX_RATIO = 1.30
SIM_REKEY = 0.5
SIM_TIP = 0.7


@dataclass
class Plan:
    updates: list = field(default_factory=list)    # (local_id, {field: value})
    rekeys: list = field(default_factory=list)     # (local_id, new_external_id, {field: value})
    inserts: list = field(default_factory=list)    # feed rows
    vanished: list = field(default_factory=list)   # local ids newly marked
    deletes: list = field(default_factory=list)    # local ids removed

    def counts(self):
        return {"inserted": len(self.inserts), "updated": len(self.updates),
                "rekeyed": len(self.rekeys), "vanished": len(self.vanished), "deleted": len(self.deletes)}


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


def _text(r):
    return "%s %s" % (r.get("payee_raw") or "", r.get("description_raw") or "")


def reconcile(feed_rows, local_rows, now):
    """feed_rows: simplefin.normalize() rows. local_rows: dicts with id,
    external_id, pending, amount_cents, date, payee_raw, description_raw,
    vanished_at, touched (bool: a person set splits / notes / receipt)."""
    plan = Plan()
    today = now.date() if isinstance(now, datetime) else now
    by_ext = {r["external_id"]: r for r in local_rows if r.get("external_id")}
    feed_ids = {f["external_id"] for f in feed_rows}

    # 1 EXACT
    unmatched = []
    for f in feed_rows:
        loc = by_ext.get(f["external_id"])
        if not loc:
            unmatched.append(f)
            continue
        upd = {}
        if bool(loc["pending"]) != bool(f["pending"]):
            upd["pending"] = f["pending"]
        if f["posted_date"] and f["posted_date"] != loc.get("posted_date"):
            upd["posted_date"] = f["posted_date"]
        if loc["pending"] and f["date"] != loc["date"]:
            upd["date"] = f["date"]
        if loc["pending"] and f["amount_cents"] != loc["amount_cents"]:
            upd["amount_cents"] = f["amount_cents"]
        if not loc.get("payee_raw") and f["payee_raw"]:
            upd["payee_raw"] = f["payee_raw"]
        if not loc.get("description_raw") and f["description_raw"]:
            upd["description_raw"] = f["description_raw"]
        if loc.get("vanished_at"):
            upd["vanished_at"] = None
        if upd:
            plan.updates.append((loc["id"], upd))

    # 2 REKEY
    orphans = [r for r in local_rows if r["pending"] and r.get("external_id") not in feed_ids]
    used = set()
    for strict in (True, False):
        still = []
        for f in unmatched:
            if f["pending"]:
                still.append(f)
                continue
            best, best_key = None, None
            for loc in orphans:
                if loc["id"] in used:
                    continue
                dd = abs((_d(f["date"]) - _d(loc["date"])).days)
                if dd > REKEY_DAYS:
                    continue
                sim = payees.similarity(_text(f), _text(loc))
                if strict:
                    if f["amount_cents"] != loc["amount_cents"] or sim < SIM_REKEY:
                        continue
                else:
                    a, b = f["amount_cents"], loc["amount_cents"]
                    same_sign = (a < 0) == (b < 0) and b != 0
                    ratio = abs(a) / abs(b) if b else 0
                    if not (same_sign and 1.0 < ratio <= TIP_MAX_RATIO and sim >= SIM_TIP and not loc.get("touched")):
                        continue
                key = (dd, -sim)
                if best is None or key < best_key:
                    best, best_key = loc, key
            if best is None:
                still.append(f)
                continue
            used.add(best["id"])
            upd = {"pending": 0, "posted_date": f["posted_date"], "date": f["date"], "vanished_at": None}
            if f["amount_cents"] != best["amount_cents"]:
                upd["amount_cents"] = f["amount_cents"]
            if f["payee_raw"] and f["payee_raw"] != best.get("payee_raw"):
                upd["payee_raw"] = f["payee_raw"]
            if f["description_raw"] and f["description_raw"] != best.get("description_raw"):
                upd["description_raw"] = f["description_raw"]
            plan.rekeys.append((best["id"], f["external_id"], upd))
        unmatched = still

    # 3 INSERT
    plan.inserts = unmatched

    # 4 VANISH
    for loc in orphans:
        if loc["id"] in used:
            continue
        if not loc.get("vanished_at"):
            plan.vanished.append(loc["id"])
            continue
        age = (today - _d(loc["vanished_at"][:10])).days
        if age >= VANISH_GRACE_DAYS and not loc.get("touched"):
            plan.deletes.append(loc["id"])
    return plan


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def _local_rows(conn, account_id, since):
    return db.rows(conn, """
        SELECT t.id, t.external_id, t.pending, t.amount_cents, t.date, t.posted_date,
               t.payee_raw, t.description_raw, t.vanished_at,
               (t.notes <> '' OR t.status = 'reviewed'
                OR EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id AND s.set_by = 'user')
                OR EXISTS (SELECT 1 FROM attachments a WHERE a.transaction_id = t.id)) AS touched
        FROM transactions t
        WHERE t.account_id = ? AND t.source = 'simplefin' AND (t.date >= ? OR t.pending = 1)""",
                   (account_id, (since - timedelta(days=7)).date().isoformat()))


def insert_transaction(conn, account, row, source, import_id=None, now_iso=None):
    """Insert a row with its first split. Shared by sync, CSV import and manual entry. -> id."""
    now_iso = now_iso or config.iso_now()
    pid = payees.resolve(conn, row.get("payee_raw", ""), row.get("description_raw", ""))
    cur = conn.execute("""INSERT INTO transactions (account_id, date, posted_date, amount_cents, payee_raw,
                            description_raw, memo_raw, payee_id, source, external_id, import_id, pending,
                            status, notes, created_at, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)""",
                       (account["id"], row["date"], row.get("posted_date"), row["amount_cents"],
                        row.get("payee_raw", ""), row.get("description_raw", ""), row.get("memo_raw", ""),
                        pid, source, row.get("external_id"), import_id, row.get("pending", 0), now_iso, now_iso))
    tid = cur.lastrowid
    payee = db.row(conn, "SELECT default_category_id, default_business FROM payees WHERE id = ?", (pid,)) if pid else None
    business = 0
    if account.get("default_business") == "business":
        business = 1
    if payee and payee["default_business"] is not None:
        business = int(payee["default_business"])
    cat = payee["default_category_id"] if payee else None
    ded = 100
    if cat:
        c = db.row(conn, "SELECT default_deductible_pct FROM categories WHERE id = ?", (cat,))
        ded = c["default_deductible_pct"] if c else 100
    conn.execute("""INSERT INTO splits (transaction_id, amount_cents, category_id, business, deductible_pct, set_by)
                    VALUES (?, ?, ?, ?, ?, 'import')""", (tid, row["amount_cents"], cat, business, ded))
    return tid


def apply(conn, account, plan, now_iso):
    for tid, upd in plan.updates:
        _update(conn, tid, upd, now_iso)
    for tid, new_ext, upd in plan.rekeys:
        upd = dict(upd)
        upd["external_id"] = new_ext
        _update(conn, tid, upd, now_iso)
    new_ids = [insert_transaction(conn, account, f, "simplefin", now_iso=now_iso) for f in plan.inserts]
    for tid in plan.vanished:
        conn.execute("UPDATE transactions SET vanished_at = ?, updated_at = ? WHERE id = ?", (now_iso, now_iso, tid))
    for tid in plan.deletes:
        conn.execute("DELETE FROM transactions WHERE id = ?", (tid,))
    return new_ids


def _update(conn, tid, upd, now_iso):
    if "amount_cents" in upd:
        # The amount moved (a tip posted). Rescale: one split takes the whole
        # amount, keeping the first split's category/business. Never reached
        # for user-touched rows - reconcile() refuses those.
        first = db.row(conn, "SELECT * FROM splits WHERE transaction_id = ? ORDER BY id LIMIT 1", (tid,))
        conn.execute("UPDATE transactions SET amount_cents = ? WHERE id = ?", (upd["amount_cents"], tid))
        conn.execute("DELETE FROM splits WHERE transaction_id = ?", (tid,))
        conn.execute("""INSERT INTO splits (transaction_id, amount_cents, category_id, business, deductible_pct,
                          schedule_c_line, income_source_id, note, set_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (tid, upd["amount_cents"], first["category_id"] if first else None,
                      first["business"] if first else 0, first["deductible_pct"] if first else 100,
                      first["schedule_c_line"] if first else None, first["income_source_id"] if first else None,
                      first["note"] if first else "", first["set_by"] if first else "import"))
    cols = {k: v for k, v in upd.items() if k != "amount_cents"}
    cols["updated_at"] = now_iso
    sets = ", ".join("%s = ?" % k for k in cols)
    conn.execute("UPDATE transactions SET %s WHERE id = ?" % sets, tuple(cols.values()) + (tid,))


# --------------------------------------------------------------------------
# the job
# --------------------------------------------------------------------------
def _since(conn, now):
    last = db.scalar(conn, "SELECT MAX(started_at) FROM sync_log WHERE ok = 1")
    if last:
        return datetime.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)
    days = int(db.scalar(conn, "SELECT value FROM settings WHERE key = 'first_sync_floor_days'") or 365)
    return now - timedelta(days=days)


def find_or_create_account(conn, fa, now_iso):
    a = db.row(conn, "SELECT * FROM accounts WHERE simplefin_id = ?", (fa["simplefin_id"],))
    if a:
        return a, False
    name = fa["name"].lower()
    kind = "checking"
    if "credit" in name or "card" in name or "visa" in name or "mastercard" in name or "amex" in name:
        kind = "credit"
    elif "saving" in name:
        kind = "savings"
    elif "brokerage" in name or "invest" in name or "ira" in name or "401" in name:
        kind = "brokerage"
    elif "loan" in name or "mortgage" in name:
        kind = "loan"
    cur = conn.execute("""INSERT INTO accounts (name, kind, institution, simplefin_id, default_business, created_at)
                          VALUES (?, ?, ?, ?, 'ask', ?)""",
                       (fa["name"], kind, fa["institution"], fa["simplefin_id"], now_iso))
    return db.row(conn, "SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,)), True


def run_sync():
    import rules
    import transfers
    access = secrets_store.get("simplefin")
    if not access:
        # Not an error: before the Bridge is connected the job simply has
        # nothing to do, and a red "failed" badge every 8 hours would be noise.
        logger.info("sync: SimpleFIN not connected, nothing to do")
        return {"skipped": "not connected"}
    now = config.now()
    now_iso = now.isoformat(timespec="seconds")
    with db.write() as conn:
        since = _since(conn, now)
        cur = conn.execute("INSERT INTO sync_log (started_at) VALUES (?)", (now_iso,))
        log_id = cur.lastrowid
    logger.info("sync: fetching %s since %s", simplefin.redact(access), since.date())
    try:
        payload = simplefin.fetch(access, since, now, pending=True)
        accounts, errs = simplefin.normalize(payload)
    except Exception as e:
        with db.write() as conn:
            conn.execute("UPDATE sync_log SET finished_at = ?, ok = 0, error = ? WHERE id = ?",
                         (config.iso_now(), str(e)[:500], log_id))
        raise
    totals = {"inserted": 0, "updated": 0, "rekeyed": 0, "vanished": 0, "deleted": 0}
    new_ids = []
    with db.read() as conn:
        raw = db.scalar(conn, "SELECT value FROM settings WHERE key = 'ignored_simplefin_ids'")
    ignored = set(json.loads(raw)) if raw else set()
    for fa in accounts:
        if fa["simplefin_id"] in ignored:
            # deleted in the app on purpose; do not recreate it
            continue
        with db.write() as conn:
            account, created = find_or_create_account(conn, fa, now_iso)
            if created:
                logger.info("sync: new account %r (%s)", fa["name"], fa["institution"])
            local = _local_rows(conn, account["id"], since)
            plan = reconcile(fa["transactions"], local, now)
            new_ids += apply(conn, account, plan, now_iso)
            bal = fa["balance_cents"] * account["balance_sign"]
            conn.execute("""INSERT INTO account_balances (account_id, date, balance_cents, available_cents, source)
                            VALUES (?, ?, ?, ?, 'simplefin')
                            ON CONFLICT(account_id, date) DO UPDATE SET balance_cents = excluded.balance_cents,
                              available_cents = excluded.available_cents, source = 'simplefin'""",
                         (account["id"], fa["balance_date"], bal, fa["available_cents"]))
            for k, v in plan.counts().items():
                totals[k] += v
            if plan.rekeys:
                logger.info("sync: account %s rekeyed %s", account["id"],
                            [(t, e) for t, e, _ in plan.rekeys])
    with db.write() as conn:
        if new_ids:
            rules.apply_rules(conn, new_ids)
        transfers.detect_and_apply(conn, since.date().isoformat())
        conn.execute("""UPDATE sync_log SET finished_at = ?, ok = 1, accounts = ?, inserted = ?, updated = ?,
                        rekeyed = ?, vanished = ?, errlist_json = ? WHERE id = ?""",
                     (config.iso_now(), len(accounts), totals["inserted"], totals["updated"],
                      totals["rekeyed"], totals["vanished"], json.dumps(errs), log_id))
    logger.info("sync: %s accounts, %s; errlist=%s", len(accounts), totals, errs)
    return totals
