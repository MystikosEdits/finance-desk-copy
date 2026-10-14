import threading

import pytest

import db
from conftest import make_account, make_txn


def test_set_splits_must_sum(conn):
    a = make_account(conn)
    t = make_txn(conn, a, "2026-09-01", -1000)
    with db.write() as w:
        with pytest.raises(ValueError):
            db.set_splits(w, t, [{"amount_cents": -400}, {"amount_cents": -500}], "user")
        assert db.set_splits(w, t, [{"amount_cents": -400, "business": 1, "category_id": 102},
                                    {"amount_cents": -600}], "user")
    got = db.rows(conn, "SELECT amount_cents, business, set_by FROM splits WHERE transaction_id = ? ORDER BY id", (t,))
    assert [(r["amount_cents"], r["business"], r["set_by"]) for r in got] == [(-400, 1, "user"), (-600, 0, "user")]
    assert db.scalar(conn, "SELECT is_business FROM v_transactions WHERE id = ?", (t,)) == 1


def test_rules_keep_user_splits(conn):
    a = make_account(conn)
    t = make_txn(conn, a, "2026-09-01", -1000)
    with db.write() as w:
        db.set_splits(w, t, [{"amount_cents": -1000, "category_id": 411}], "user")
        assert db.set_splits(w, t, [{"amount_cents": -1000, "category_id": 412}], "rule:1", keep_user=True) is False
    assert db.scalar(conn, "SELECT category_id FROM splits WHERE transaction_id = ?", (t,)) == 411


def test_write_is_reentrant_and_serialised(conn):
    a = make_account(conn)
    with db.write() as w1:
        with db.write() as w2:
            assert w1 is w2
            w2.execute("UPDATE accounts SET name = 'x' WHERE id = ?", (a,))
    assert db.scalar(conn, "SELECT name FROM accounts WHERE id = ?", (a,)) == "x"

    # Two threads writing at once must not raise "database is locked".
    errors = []

    def worker(n):
        try:
            for i in range(20):
                with db.write() as w:
                    w.execute("INSERT INTO tags (name) VALUES (?)", ("t%d-%d" % (n, i),))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    assert db.scalar(conn, "SELECT COUNT(*) FROM tags") == 80


def test_integrity_check_finds_split_mismatch(conn):
    a = make_account(conn)
    t = make_txn(conn, a, "2026-09-01", -1000)
    assert db.integrity_check()["ok"]
    conn.execute("BEGIN")
    conn.execute("UPDATE splits SET amount_cents = -999 WHERE transaction_id = ?", (t,))
    conn.execute("COMMIT")
    res = db.integrity_check()
    assert not res["ok"] and res["splits_mismatch"][0]["id"] == t
