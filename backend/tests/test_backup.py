import os
import sqlite3
from datetime import date, datetime

import backup
import db
from conftest import make_account, make_txn


def test_backup_encrypt_restore_roundtrip(conn, clock, tmp_path):
    a = make_account(conn)
    make_txn(conn, a, "2026-09-01", -1000, "COFFEE")
    res = backup.run()
    assert os.path.exists(res["local"])
    assert res["offsite"]["db"].endswith("finance-2026-09-07.db.fernet")
    v = backup.verify()
    assert v["ok"] and v["integrity"] == "ok" and v["count_differences"] == {}

    key, new = backup.ensure_key()
    assert not new
    plain = backup.restore(res["offsite"]["db"], key, str(tmp_path / "restored"))
    c = sqlite3.connect(plain)
    assert c.execute("SELECT payee_raw FROM transactions").fetchone()[0] == "COFFEE"
    c.close()


def test_rotation_keeps_daily_and_monthly(tmp_path):
    folder = str(tmp_path / "b")
    os.makedirs(folder)
    d = date(2025, 1, 1)
    while d <= date(2026, 9, 7):
        open(os.path.join(folder, "finance-%s.db" % d.isoformat()), "w").close()
        d = date.fromordinal(d.toordinal() + 1)
    backup.rotate(folder, "finance-*.db", date(2026, 9, 7))
    left = sorted(os.listdir(folder))
    assert "finance-2026-09-07.db" in left and "finance-2026-08-25.db" in left
    assert "finance-2026-08-24.db" not in left  # outside the 14 daily
    assert "finance-2026-08-01.db" in left and "finance-2025-10-01.db" in left  # monthly keeps
    assert "finance-2025-01-01.db" not in left  # older than 12 months
    assert len(left) <= 14 + 13


def test_fingerprint_is_stable(data_dir):
    k1, new = backup.ensure_key()
    assert new
    assert backup.fingerprint() == backup.fingerprint(k1) and len(backup.fingerprint()) == 12
