import db
import reports
from conftest import make_account, make_txn


def biz(conn, tid, cents, cat, line=None, ded=None):
    with db.write() as w:
        db.set_splits(w, tid, [{"amount_cents": cents, "category_id": cat, "business": 1, "schedule_c_line": line,
                                "deductible_pct": ded}], "user")


def test_schedule_c_rollup_and_meals_haircut(conn, clock):
    a = make_account(conn)
    t1 = make_txn(conn, a, "2026-03-01", -10000, "EDITOR PAYOUT")
    t2 = make_txn(conn, a, "2026-03-02", -6000, "THE DINER")
    t3 = make_txn(conn, a, "2026-03-03", 250000, "GOOGLE ADSENSE")
    t4 = make_txn(conn, a, "2026-03-04", -4000, "MYSTERY")
    t5 = make_txn(conn, a, "2025-12-31", -99999, "LAST YEAR")
    biz(conn, t1, -10000, 101)                 # contract labor -> 11
    biz(conn, t2, -6000, 107, ded=50)          # meals -> 24b, 50%
    biz(conn, t3, 250000, 201)                 # income -> 1
    biz(conn, t4, -4000, None)                 # business, no category -> unassigned
    biz(conn, t5, -99999, 101)
    sc = reports.schedule_c(conn, 2026)
    by = {L["line"]: L for L in sc["lines"]}
    assert by["1"]["amount_cents"] == 250000
    assert by["11"]["amount_cents"] == 10000 and by["11"]["deductible_cents"] == 10000
    assert by["24b"]["amount_cents"] == 6000 and by["24b"]["deductible_cents"] == 3000
    assert by["unassigned"]["amount_cents"] == 4000 and sc["unassigned_cents"] == 4000
    assert sc["expenses_cents"] == 20000 and sc["expenses_deductible_cents"] == 17000
    assert sc["net_profit_before_adjustments_cents"] == 250000 - 17000
    assert [L["line"] for L in sc["lines"]] == ["1", "11", "24b", "unassigned"]
    assert by["11"]["missing_receipts"] == 1

    p = reports.pnl(conn, "2026-01-01", "2026-12-31", "month")
    assert p[0]["key"] == "2026-03" and p[0]["net_cents"] == 250000 - 20000
    assert {r["id"] for r in reports.missing_receipts(conn, 2026, 5000)} == {t1, t2}
    assert [r["id"] for r in reports.assets_over_de_minimis(conn, 2026, 5000)] == [t1, t2]


def test_contractors_threshold_and_unflagged(conn, clock):
    a = make_account(conn)
    with db.write() as w:
        alex = w.execute("INSERT INTO payees (name, normalized, is_contractor, contractor_kind) VALUES ('Alex', 'alex', 1, 'editor')").lastrowid
        pm = w.execute("INSERT INTO payees (name, normalized) VALUES ('PM', 'pm')").lastrowid
    for i in range(5):
        t = make_txn(conn, a, "2026-0%d-01" % (i + 1), -42000, "ALEX", payee_id=alex)
        biz(conn, t, -42000, 101)
    t = make_txn(conn, a, "2026-06-01", -50000, "PM", payee_id=pm)
    biz(conn, t, -50000, 101)
    out = reports.contractors(conn, 2026, 200000)
    d = out["contractors"][0]
    assert d["name"] == "Alex" and d["paid_cents"] == 210000 and d["over_threshold"] and d["needs_w9"]
    assert out["unflagged_contract_labor"][0]["name"] == "PM"


def test_cashflow_and_spending_exclude_transfers(conn, clock):
    chk = make_account(conn)
    card = make_account(conn, "Card", kind="credit")
    make_txn(conn, chk, "2026-09-01", 500000, "PAYCHECK")
    g = make_txn(conn, chk, "2026-09-02", -12000, "GROCERIES")
    pay = make_txn(conn, chk, "2026-09-03", -30000, "PAYMENT TO CARD")
    rcv = make_txn(conn, card, "2026-09-04", 30000, "PAYMENT THANK YOU")
    with db.write() as w:
        db.set_splits(w, g, [{"amount_cents": -12000, "category_id": 411}], "user")
        db.set_splits(w, pay, [{"amount_cents": -30000, "category_id": 501}], "user")
        db.set_splits(w, rcv, [{"amount_cents": 30000, "category_id": 501}], "user")
    cf = reports.cashflow(conn, "2026-09-01", "2026-09-30")
    assert cf[0]["income_cents"] == 500000 and cf[0]["spending_cents"] == 12000
    sp = reports.spending(conn, "2026-09-01", "2026-09-30", by="category")
    assert sp[0]["name"] == "Groceries" and sp[0]["cents"] == 12000 and len(sp) == 1
