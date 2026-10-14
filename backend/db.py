"""SQLite access. One process, one writer, one lock.

WHY NOT AN ORM
--------------
The app is one process with one writer. An ORM would buy a second query
language and nothing else. The rig's SQLite (3.50) has STRICT tables, UPSERT,
RETURNING and window functions - everything the reports need.

WHY EVERY WRITE GOES THROUGH write()
------------------------------------
uvicorn runs one worker; the scheduler is one daemon thread. A request and a
sync can still both want to write in the same millisecond. `write()` takes a
process-wide lock and opens `BEGIN IMMEDIATE`, so SQLite never has to arbitrate
between two writers and "database is locked" cannot happen from inside this
process. Readers never take the lock; WAL lets them read while a write is
open. Anything slow (SimpleFIN over HTTP, a price fetch) happens BEFORE the
lock is taken, so it is held for milliseconds.

Connections are never shared across threads. connect() is cheap.
"""

import contextlib
import glob
import os
import sqlite3
import threading

import config

PATH = config.DB_PATH          # tests point this at a temp file
MIGRATIONS = os.path.join(config.HERE, "migrations")

WRITE_LOCK = threading.RLock()
_local = threading.local()


def connect(path=None):
    conn = sqlite3.connect(path or PATH, isolation_level=None, timeout=5.0,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextlib.contextmanager
def read():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def write():
    """The single write path. Re-entrant: a nested write() joins the
    outer transaction rather than deadlocking on the lock."""
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield _local.conn
        finally:
            _local.depth -= 1
        return
    with WRITE_LOCK:
        conn = connect()
        _local.conn = conn
        _local.depth = 1
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            _local.depth = 0
            _local.conn = None
            conn.close()


# --------------------------------------------------------------------------
# migrations
# --------------------------------------------------------------------------
def migrate(path=None):
    """Apply migrations/NNNN_*.sql in order, each in its own transaction,
    recorded in schema_migrations. Safe to run at every startup."""
    path = path or PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT""")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        applied = []
        for fn in sorted(glob.glob(os.path.join(MIGRATIONS, "*.sql"))):
            base = os.path.basename(fn)
            version = int(base.split("_", 1)[0])
            if version in done:
                continue
            with open(fn, "r", encoding="utf-8") as fh:
                sql = fh.read()
            conn.execute("BEGIN IMMEDIATE")
            try:
                _exec_script(conn, sql)
                conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)",
                             (version, base, config.iso_now()))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            applied.append(base)
        return applied
    finally:
        conn.close()


def _exec_script(conn, sql):
    # executescript() issues its own COMMIT first, which would break the
    # per-migration transaction; run statements one at a time instead.
    for stmt in _split_statements(sql):
        conn.execute(stmt)


def _split_statements(sql):
    """Split on ';' outside of comments and quotes. Good enough for our own
    migration files, which never put a ';' inside a string."""
    out, buf, in_str, in_line_comment = [], [], None, False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
                buf.append(ch)
            i += 1
            continue
        if in_str:
            buf.append(ch)
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch == "-" and sql[i:i + 2] == "--":
            in_line_comment = True
            i += 2
            continue
        if ch in ("'", '"'):
            in_str = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def row(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return r[0] if r else None


def set_splits(conn, txn_id, splits, set_by, keep_user=False):
    """Replace a transaction's splits. THE one place splits are written.

    `splits` is a list of dicts with amount_cents and any of category_id,
    business, deductible_pct, schedule_c_line, income_source_id, note.
    The amounts must sum to the transaction's amount, to the cent.

    keep_user=True refuses to touch a transaction whose splits a person set
    (used by rules), and returns False instead.
    """
    t = row(conn, "SELECT amount_cents FROM transactions WHERE id = ?", (txn_id,))
    if not t:
        raise ValueError("no transaction %s" % txn_id)
    if keep_user and scalar(conn, "SELECT COUNT(*) FROM splits WHERE transaction_id = ? AND set_by = 'user'", (txn_id,)):
        return False
    if not splits:
        raise ValueError("a transaction needs at least one split")
    total = sum(int(s["amount_cents"]) for s in splits)
    if total != t["amount_cents"]:
        raise ValueError("splits sum to %d but the transaction is %d" % (total, t["amount_cents"]))
    conn.execute("DELETE FROM splits WHERE transaction_id = ?", (txn_id,))
    for s in splits:
        conn.execute("""INSERT INTO splits (transaction_id, amount_cents, category_id, business,
                          deductible_pct, schedule_c_line, income_source_id, note, set_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (txn_id, int(s["amount_cents"]), s.get("category_id"),
                      1 if s.get("business") else 0,
                      int(s.get("deductible_pct", 100) if s.get("deductible_pct") is not None else 100),
                      s.get("schedule_c_line") or None, s.get("income_source_id"),
                      s.get("note") or "", set_by))
    conn.execute("UPDATE transactions SET updated_at = ? WHERE id = ?", (config.iso_now(), txn_id))
    return True


def integrity_check():
    """What the nightly job and /api/system/integrity report."""
    with read() as conn:
        out = {"pragma": scalar(conn, "PRAGMA integrity_check")}
        out["splits_mismatch"] = rows(conn, """
            SELECT t.id, t.amount_cents, COALESCE(SUM(s.amount_cents), 0) AS split_sum
            FROM transactions t LEFT JOIN splits s ON s.transaction_id = t.id
            GROUP BY t.id HAVING split_sum <> t.amount_cents LIMIT 50""")
        out["orphan_attachments"] = scalar(conn, """
            SELECT COUNT(*) FROM attachments WHERE transaction_id IS NULL AND payee_id IS NULL""")
        out["fk_violations"] = len(list(conn.execute("PRAGMA foreign_key_check")))
        out["ok"] = (out["pragma"] == "ok" and not out["splits_mismatch"] and out["fk_violations"] == 0)
        return out


def table_counts(conn):
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {n: scalar(conn, "SELECT COUNT(*) FROM \"%s\"" % n) for n in names}
