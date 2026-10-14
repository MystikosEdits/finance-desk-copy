"""Subscriptions and other recurring charges, found from the ledger.

A payee with three or more charges whose gaps cluster around a week, two
weeks, a month, a quarter or a year, with amounts mostly within tolerance
of the median, becomes a SUGGESTED recurring row. You confirm or
dismisses; dismissed payees are never suggested again. Confirmed rows get
their next date and last-seen updated on every run and go "missed" when the
next date is more than a few days past.
"""

from datetime import date, timedelta
from statistics import median

import config
import db
import log

logger = log.get("recurring")

CADENCES = [("weekly", 7, 2), ("biweekly", 14, 3), ("monthly", 30, 5), ("quarterly", 91, 8), ("yearly", 365, 20)]
MIN_OCCURRENCES = 3
AMOUNT_TOLERANCE = 0.15
LOOKBACK_DAYS = 460
MISSED_GRACE_DAYS = 5


def cadence_for(intervals):
    if not intervals:
        return None
    m = median(intervals)
    for name, days, tol in CADENCES:
        if abs(m - days) <= tol:
            # most gaps must agree, not just the median
            agree = sum(1 for i in intervals if abs(i - days) <= tol * 2)
            if agree >= max(2, int(len(intervals) * 0.6)):
                return name, days
    return None


def next_after(d, cadence):
    days = dict((c[0], c[1]) for c in CADENCES)[cadence]
    if cadence == "monthly":
        y, m = d.year + (d.month // 12), d.month % 12 + 1
        try:
            return d.replace(year=y, month=m)
        except ValueError:
            return d.replace(year=y, month=m, day=28)
    if cadence == "yearly":
        try:
            return d.replace(year=d.year + 1)
        except ValueError:
            return d.replace(year=d.year + 1, day=28)
    return d + timedelta(days=days)


def detect(rows, today):
    """rows: dicts with payee_id, payee_name, date, amount_cents, business (0/1), txn_id; charges only (amount < 0).
    -> list of suggestion dicts, one per payee."""
    by_payee = {}
    for r in rows:
        if r["payee_id"] is None or r["amount_cents"] >= 0:
            continue
        by_payee.setdefault(r["payee_id"], []).append(r)
    out = []
    for pid, rs in by_payee.items():
        rs.sort(key=lambda r: r["date"])
        if len(rs) < MIN_OCCURRENCES:
            continue
        dates = [date.fromisoformat(r["date"]) for r in rs]
        intervals = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        cad = cadence_for(intervals)
        if not cad:
            continue
        amounts = [abs(r["amount_cents"]) for r in rs]
        med = int(median(amounts))
        close = sum(1 for a in amounts if abs(a - med) <= med * AMOUNT_TOLERANCE)
        if close < max(2, int(len(amounts) * 0.6)):
            continue
        last = dates[-1]
        # A series that stopped more than two periods ago is not a subscription
        # to suggest; a confirmed one that stops shows up as "missed" instead.
        if (today - last).days > 2 * cad[1] + 10:
            continue
        nxt = next_after(last, cad[0])
        biz = sum(1 for r in rs if r.get("business")) * 2 > len(rs)
        out.append({"payee_id": pid, "name": rs[-1]["payee_name"] or "?", "cadence": cad[0], "expected_cents": med,
                    "next_date": nxt.isoformat(), "business": 1 if biz else 0, "last_seen_txn_id": rs[-1]["txn_id"],
                    "last_seen_date": last.isoformat(), "occurrences": len(rs)})
    return out


def _rows(conn, since):
    return db.rows(conn, """
        SELECT t.id AS txn_id, t.payee_id, p.name AS payee_name, t.date, t.amount_cents,
               EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id AND s.business = 1) AS business
        FROM transactions t LEFT JOIN payees p ON p.id = t.payee_id
        WHERE t.pending = 0 AND t.date >= ? AND t.amount_cents < 0
          AND NOT EXISTS (SELECT 1 FROM transfers tr WHERE (tr.out_txn_id = t.id OR tr.in_txn_id = t.id) AND tr.status <> 'rejected')
          AND NOT EXISTS (SELECT 1 FROM splits s JOIN categories c ON c.id = s.category_id WHERE s.transaction_id = t.id AND c.kind = 'transfer')
        ORDER BY t.date""", (since,))


def detect_and_store(conn=None):
    own = conn is None
    if own:
        ctx = db.write()
        conn = ctx.__enter__()
    try:
        today = config.today()
        rows = _rows(conn, (today - timedelta(days=LOOKBACK_DAYS)).isoformat())
        found = detect(rows, today)
        existing = {r["payee_id"]: r for r in db.rows(conn, "SELECT * FROM recurring WHERE payee_id IS NOT NULL")}
        new = updated = 0
        for f in found:
            ex = existing.get(f["payee_id"])
            if ex is None:
                conn.execute("""INSERT INTO recurring (payee_id, name, cadence, expected_cents, next_date, is_subscription, business, active,
                                status, last_seen_txn_id, last_seen_date) VALUES (?, ?, ?, ?, ?, 1, ?, 1, 'suggested', ?, ?)""",
                             (f["payee_id"], f["name"], f["cadence"], f["expected_cents"], f["next_date"], f["business"],
                              f["last_seen_txn_id"], f["last_seen_date"]))
                new += 1
            elif ex["status"] == "dismissed":
                continue
            else:
                # keep the person's name/flags; refresh what the ledger knows
                conn.execute("UPDATE recurring SET next_date = ?, last_seen_txn_id = ?, last_seen_date = ?, cadence = CASE WHEN status = 'suggested' THEN ? ELSE cadence END, "
                             "expected_cents = CASE WHEN status = 'suggested' THEN ? ELSE expected_cents END WHERE id = ?",
                             (f["next_date"], f["last_seen_txn_id"], f["last_seen_date"], f["cadence"], f["expected_cents"], ex["id"]))
                updated += 1
        logger.info("recurring: %d suggested, %d refreshed", new, updated)
        return {"new": new, "updated": updated}
    finally:
        if own:
            ctx.__exit__(None, None, None)


def listing(conn, status=None):
    where = "1=1" if not status else "r.status = ?"
    rows = db.rows(conn, """SELECT r.*, p.name AS payee_name,
                                   (SELECT c.name FROM splits s JOIN categories c ON c.id = s.category_id WHERE s.transaction_id = r.last_seen_txn_id LIMIT 1) AS category_name
                            FROM recurring r LEFT JOIN payees p ON p.id = r.payee_id WHERE %s ORDER BY r.status, r.next_date""" % where,
                   (status,) if status else ())
    today = config.today()
    for r in rows:
        r["missed"] = bool(r["active"] and r["status"] == "confirmed" and r["next_date"] and date.fromisoformat(r["next_date"]) < today - timedelta(days=MISSED_GRACE_DAYS))
        days = dict((c[0], c[1]) for c in CADENCES).get(r["cadence"], 30)
        r["monthly_equivalent_cents"] = int(round(r["expected_cents"] * 30.4375 / days))
    return rows
