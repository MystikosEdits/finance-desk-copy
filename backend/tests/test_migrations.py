import sqlite3

import db


def test_fresh_then_rerun_is_idempotent(data_dir, clock):
    applied = db.migrate()
    assert applied and applied[0].startswith("0001_")
    assert db.migrate() == []
    c = db.connect()
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "transactions", "splits", "categories", "jobs"} <= names
    assert c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 40
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    c.close()


def test_strict_tables_refuse_floats(conn):
    conn.execute("BEGIN")
    conn.execute("INSERT INTO accounts (name, kind, created_at) VALUES ('a', 'checking', 'x')")
    try:
        conn.execute("INSERT INTO transactions (account_id, date, amount_cents, source, created_at, updated_at) "
                     "VALUES (1, '2026-01-01', 12.30, 'manual', 'x', 'x')")
        assert False, "a float got into a cents column"
    except sqlite3.IntegrityError:
        pass
    conn.execute("ROLLBACK")


def test_split_statements_handles_comments_and_strings():
    sql = "-- a; comment\nINSERT INTO t VALUES ('a;b'); CREATE TABLE x (y TEXT); -- tail\n"
    stmts = db._split_statements(sql)
    assert stmts == ["INSERT INTO t VALUES ('a;b')", "CREATE TABLE x (y TEXT)"]
