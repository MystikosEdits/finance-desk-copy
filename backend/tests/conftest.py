"""Every test gets a fresh database built by the real migrations, a data
directory of its own, and a clock it can set."""

import os
import sys
from datetime import datetime

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import db  # noqa: E402


class Clock:
    def __init__(self, at):
        self.at = at

    def now(self):
        return self.at

    def set(self, at):
        self.at = at


@pytest.fixture
def clock(monkeypatch):
    c = Clock(datetime(2026, 9, 7, 12, 0, 0))
    monkeypatch.setattr(config, "now", c.now)
    monkeypatch.setattr(config, "today", lambda: c.at.date())
    monkeypatch.setattr(config, "iso_now", lambda: c.at.isoformat(timespec="seconds"))
    return c


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = str(tmp_path / "data")
    monkeypatch.setattr(config, "DATA", d)
    monkeypatch.setattr(config, "DB_PATH", os.path.join(d, "finance.db"))
    monkeypatch.setattr(config, "SECRETS_DIR", os.path.join(d, "secrets"))
    monkeypatch.setattr(config, "ATTACHMENTS_DIR", os.path.join(d, "attachments"))
    monkeypatch.setattr(config, "BACKUPS_DIR", os.path.join(d, "backups"))
    monkeypatch.setattr(config, "LOGS_DIR", os.path.join(d, "logs"))
    monkeypatch.setattr(db, "PATH", os.path.join(d, "finance.db"))
    import auth
    monkeypatch.setattr(auth, "USERS_PATH", os.path.join(d, "users.json"))
    monkeypatch.setattr(auth, "SECRET_PATH", os.path.join(d, "secret.key"))
    import backup
    monkeypatch.setattr(backup, "KEY_PATH", os.path.join(d, "backup.key"))
    monkeypatch.setitem(config.CFG, "onedrive_backup_dir", str(tmp_path / "onedrive"))
    config.ensure_dirs()
    return d


@pytest.fixture
def fresh_db(data_dir, clock):
    db.migrate()
    return db.PATH


@pytest.fixture
def conn(fresh_db):
    c = db.connect()
    yield c
    c.close()


def make_account(conn, name="Checking", kind="checking", **kw):
    cols = {"name": name, "kind": kind, "created_at": "2026-01-01T00:00:00"}
    cols.update(kw)
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute("BEGIN")
    cur = conn.execute("INSERT INTO accounts (%s) VALUES (%s)" % (keys, marks), tuple(cols.values()))
    conn.execute("COMMIT")
    return cur.lastrowid


def make_txn(conn, account_id, date, cents, payee_raw="", **kw):
    cols = {"account_id": account_id, "date": date, "amount_cents": cents, "payee_raw": payee_raw,
            "description_raw": kw.pop("description_raw", payee_raw), "source": "manual",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}
    cols.update(kw)
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute("BEGIN")
    cur = conn.execute("INSERT INTO transactions (%s) VALUES (%s)" % (keys, marks), tuple(cols.values()))
    tid = cur.lastrowid
    conn.execute("INSERT INTO splits (transaction_id, amount_cents) VALUES (?, ?)", (tid, cents))
    conn.execute("COMMIT")
    return tid
