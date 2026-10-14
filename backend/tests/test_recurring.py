from datetime import date

import db
import recurring as R
from conftest import make_account, make_txn


def rows(pid, name, dates, cents, business=0):
    return [{"txn_id": i + 1, "payee_id": pid, "payee_name": name, "date": d, "amount_cents": -cents, "business": business} for i, d in enumerate(dates)]


def test_monthly_subscription_is_found_and_next_date_rolls():
    out = R.detect(rows(1, "Adobe", ["2026-05-02", "2026-06-02", "2026-07-02"], 1999), date(2026, 7, 10))
    assert len(out) == 1
    s = out[0]
    assert s["cadence"] == "monthly" and s["expected_cents"] == 1999 and s["next_date"] == "2026-08-02" and s["occurrences"] == 3
    # a series that stopped months ago is not suggested
    assert R.detect(rows(1, "Adobe", ["2026-01-02", "2026-02-02", "2026-03-02"], 1999), date(2026, 7, 10)) == []


def test_two_charges_or_irregular_amounts_are_not_subscriptions():
    assert R.detect(rows(1, "Adobe", ["2026-05-02", "2026-06-02"], 1999), date(2026, 7, 1)) == []
    assert R.detect(rows(2, "Costco", ["2026-05-02", "2026-06-01", "2026-07-03"], 0) and
                    [dict(r, amount_cents=-c) for r, c in zip(rows(2, "Costco", ["2026-05-02", "2026-06-01", "2026-07-03"], 1), (5000, 21000, 9000))],
                    date(2026, 7, 10)) == []
    assert R.detect(rows(3, "Diner", ["2026-05-02", "2026-05-09", "2026-06-20", "2026-07-30"], 4000), date(2026, 8, 1)) == []


def test_weekly_and_yearly():
    w = R.detect(rows(1, "Gym", ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"], 1500), date(2026, 6, 25))
    assert w[0]["cadence"] == "weekly" and w[0]["next_date"] == "2026-06-29"
    y = R.detect(rows(2, "Domain", ["2024-03-01", "2025-03-01", "2026-03-01"], 1200), date(2026, 9, 1))
    assert y[0]["cadence"] == "yearly" and y[0]["next_date"] == "2027-03-01"


def test_store_confirm_missed_and_dismissed(conn, clock):
    a = make_account(conn)
    import payees as P
    with db.write() as w:
        pid = P.resolve(w, "ADOBE *CREATIVE CLOUD")
    for d in ("2026-05-02", "2026-06-02", "2026-07-02"):
        make_txn(conn, a, d, -1999, "ADOBE *CREATIVE CLOUD", payee_id=pid)
    res = R.detect_and_store()
    assert res["new"] == 1
    r = R.listing(conn)[0]
    assert r["status"] == "suggested" and r["next_date"] == "2026-08-02" and r["monthly_equivalent_cents"] == 2028  # 1999 x 30.4375 / 30
    with db.write() as w:
        w.execute("UPDATE recurring SET status = 'confirmed' WHERE id = ?", (r["id"],))
    r = R.listing(conn)[0]
    assert r["missed"] is True          # today is 2026-09-07, next was 2026-08-02
    make_txn(conn, a, "2026-08-02", -1999, "ADOBE *CREATIVE CLOUD", payee_id=pid)
    make_txn(conn, a, "2026-09-02", -1999, "ADOBE *CREATIVE CLOUD", payee_id=pid)
    assert R.detect_and_store()["updated"] == 1
    r = R.listing(conn)[0]
    assert r["next_date"] == "2026-10-02" and r["missed"] is False
    with db.write() as w:
        w.execute("UPDATE recurring SET status = 'dismissed' WHERE id = ?", (r["id"],))
    assert R.detect_and_store() == {"new": 0, "updated": 0}
