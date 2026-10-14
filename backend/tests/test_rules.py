import json

import db
import rules as R
from conftest import make_account, make_txn


def add_rule(conn, name, match, action, priority=100, stop=1):
    conn.execute("BEGIN")
    cur = conn.execute("INSERT INTO rules (priority, name, stop, match_json, action_json) VALUES (?, ?, ?, ?, ?)",
                       (priority, name, stop, json.dumps(match), json.dumps(action)))
    conn.execute("COMMIT")
    return cur.lastrowid


def test_matches():
    t = {"payee_name": "Adobe", "payee_raw": "ADOBE *CREATIVE CLOUD", "description_raw": "", "memo_raw": "",
         "amount_cents": -1999, "account_id": 2, "payee_id": 9}
    assert R.matches({"payee_contains": "adobe"}, t)
    assert not R.matches({"payee_contains": "netflix"}, t)
    assert R.matches({"regex": r"creative\s+cloud", "sign": "out"}, t)
    assert not R.matches({"sign": "in"}, t)
    assert R.matches({"amount_min_cents": 1000, "amount_max_cents": 2000}, t)
    assert not R.matches({"amount_max_cents": 1000}, t)
    assert R.matches({"account_id": 2, "payee_id": 9}, t)
    assert not R.matches({"regex": "("}, t)


def test_percent_split_sums_to_the_cent():
    t = {"amount_cents": -10001}
    out = R.splits_for({"split": [{"pct": 40, "business": 1, "category_id": 105}, {"pct": 60, "category_id": 402}]}, t)
    assert [s["amount_cents"] for s in out] == [-4000, -6001]
    assert out[0]["business"] == 1 and out[1]["business"] == 0


def test_apply_respects_priority_stop_and_user_splits(conn, clock):
    a = make_account(conn)
    t1 = make_txn(conn, a, "2026-09-01", -1999, "ADOBE *CREATIVE CLOUD")
    t2 = make_txn(conn, a, "2026-09-01", -1500, "NETFLIX.COM")
    t3 = make_txn(conn, a, "2026-09-01", -1999, "ADOBE *CREATIVE CLOUD")
    r1 = add_rule(conn, "adobe biz", {"payee_contains": "adobe"}, {"category_id": 102, "business": 1, "mark_reviewed": True}, priority=10)
    add_rule(conn, "adobe personal (never reached)", {"payee_contains": "adobe"}, {"category_id": 463}, priority=20)
    add_rule(conn, "netflix", {"payee_contains": "netflix"}, {"category_id": 441, "tags": ["fun"]}, priority=30)
    with db.write() as w:
        db.set_splits(w, t3, [{"amount_cents": -1999, "category_id": 463}], "user")
        n = R.apply_rules(w, [t1, t2, t3])
    assert n == 2
    s1 = db.row(conn, "SELECT * FROM splits WHERE transaction_id = ?", (t1,))
    assert s1["category_id"] == 102 and s1["business"] == 1 and s1["set_by"] == "rule:%d" % r1
    assert db.scalar(conn, "SELECT status FROM transactions WHERE id = ?", (t1,)) == "reviewed"
    assert db.scalar(conn, "SELECT category_id FROM splits WHERE transaction_id = ?", (t2,)) == 441
    assert db.rows(conn, "SELECT g.name FROM transaction_tags tt JOIN tags g ON g.id = tt.tag_id WHERE tt.transaction_id = ?", (t2,))[0]["name"] == "fun"
    # the user's choice on t3 survived
    assert db.scalar(conn, "SELECT category_id FROM splits WHERE transaction_id = ?", (t3,)) == 463
    assert db.scalar(conn, "SELECT hits FROM rules WHERE id = ?", (r1,)) == 1
    # re-running changes nothing and writes nothing
    before = db.scalar(conn, "SELECT COUNT(*) FROM audit_log")
    with db.write() as w:
        R.apply_rules(w, [t1, t2, t3])
    assert db.scalar(conn, "SELECT COUNT(*) FROM audit_log") == before
    assert db.scalar(conn, "SELECT category_id FROM splits WHERE transaction_id = ?", (t3,)) == 463


def test_meals_get_deductible_default_from_category(conn, clock):
    a = make_account(conn)
    t = make_txn(conn, a, "2026-09-01", -6000, "THE DINER")
    add_rule(conn, "meals", {"payee_contains": "diner"}, {"category_id": 107, "business": 1})
    with db.write() as w:
        R.apply_rules(w, [t])
    s = db.row(conn, "SELECT * FROM splits WHERE transaction_id = ?", (t,))
    assert s["deductible_pct"] == 50 and s["business"] == 1


def test_suggest_from_transaction(conn, clock):
    a = make_account(conn)
    import payees as P
    with db.write() as w:
        pid = P.resolve(w, "SQ *BLUE BOTTLE 0412 SAN FRANCISCO CA")
    t = make_txn(conn, a, "2026-09-01", -420, "SQ *BLUE BOTTLE 0412 SAN FRANCISCO CA", payee_id=pid)
    with db.write() as w:
        db.set_splits(w, t, [{"amount_cents": -420, "category_id": 413}], "user")
    s = R.suggest_from_transaction(conn, t)
    assert s["match_json"]["payee_contains"].lower() == "blue bottle" and s["match_json"]["sign"] == "out"
    assert s["action_json"]["category_id"] == 413 and s["action_json"]["payee_id"] == pid
