"""Backups. The first thing on the rig with a backup story, so it gets one that
has actually been restored.

    python backup.py now                 back up, rotate, encrypt to OneDrive
    python backup.py verify              decrypt the newest OneDrive copy, integrity-check it, compare counts
    python backup.py restore <file.fernet> --key <key> --to <dir>
    python backup.py ensure-key          make data/backup.key if missing (prints it ONCE when new)
    python backup.py fingerprint

WHAT A BACKUP IS
----------------
`sqlite3.Connection.backup()`. Copying finance.db with Copy-Item misses the
-wal file and hands you a database from some minutes ago with the last writes
missing - or a corrupt one. Only the backup API is a backup.

TWO COPIES, TWO REASONS
-----------------------
- data/backups/finance-YYYY-MM-DD.db: plain, local, 14 daily + 12 monthly.
  For "I categorised 300 rows wrong yesterday".
- OneDrive\\FinanceDesk-Backup\\*.fernet: the same file encrypted with the key
  in data/backup.key, plus users.json and every receipt (each file on its own,
  named by sha256, so the receipt copy is incremental). For "the rig is gone".

THE KEY IS THE BACKUP
---------------------
An encrypted copy on OneDrive plus a key that only ever lived on the rig is
not a backup. deploy.ps1 prints the key exactly once when it is created and
Settings nags until you confirm it is in your password manager.
"""

import argparse
import glob
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import timedelta

from cryptography.fernet import Fernet

import config
import db
import log

logger = log.get("backup")

KEY_PATH = os.path.join(config.DATA, "backup.key")
KEEP_DAILY = 14
KEEP_MONTHLY = 12


# --------------------------------------------------------------------------
# key
# --------------------------------------------------------------------------
def ensure_key():
    """-> (key_bytes, created_now)."""
    try:
        with io.open(KEY_PATH, "rb") as fh:
            key = fh.read().strip()
        if key:
            return key, False
    except OSError:
        pass
    key = Fernet.generate_key()
    os.makedirs(config.DATA, exist_ok=True)
    with io.open(KEY_PATH, "wb") as fh:
        fh.write(key)
    return key, True


def fingerprint(key=None):
    key = key or ensure_key()[0]
    return hashlib.sha256(key).hexdigest()[:12]


# --------------------------------------------------------------------------
# local copy + rotation
# --------------------------------------------------------------------------
def _stamp(d):
    return d.strftime("%Y-%m-%d")


def backup_local(when=None):
    when = when or config.now()
    os.makedirs(config.BACKUPS_DIR, exist_ok=True)
    dst = os.path.join(config.BACKUPS_DIR, "finance-%s.db" % _stamp(when))
    tmp = dst + ".tmp"
    src = db.connect()
    try:
        out = sqlite3.connect(tmp)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    os.replace(tmp, dst)
    return dst


def rotate(folder, pattern, today=None):
    """Keep the newest KEEP_DAILY files and the first file of each of the last
    KEEP_MONTHLY months. Delete the rest. Files are named ...YYYY-MM-DD...
    -> list of deleted paths."""
    today = today or config.today()
    files = sorted(glob.glob(os.path.join(folder, pattern)))
    dated = []
    for f in files:
        base = os.path.basename(f)
        try:
            d = base.split("finance-", 1)[1][:10]
            y, m, dd = (int(x) for x in d.split("-"))
        except (IndexError, ValueError):
            continue
        dated.append(((y, m, dd), f))
    dated.sort()
    keep = set(f for _, f in dated[-KEEP_DAILY:])
    by_month = {}
    for (y, m, dd), f in dated:
        by_month.setdefault((y, m), f)  # first of the month, since sorted ascending
    cutoff = (today.replace(day=1) - timedelta(days=31 * KEEP_MONTHLY))
    for (y, m), f in by_month.items():
        if (y, m) >= (cutoff.year, cutoff.month):
            keep.add(f)
    deleted = []
    for _, f in dated:
        if f not in keep:
            os.remove(f)
            deleted.append(f)
    return deleted


# --------------------------------------------------------------------------
# encrypted copy
# --------------------------------------------------------------------------
def _encrypt_file(fernet, src, dst):
    with open(src, "rb") as fh:
        data = fh.read()
    tmp = dst + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(fernet.encrypt(data))
    os.replace(tmp, dst)


def backup_offsite(local_db_path, when=None):
    """Encrypt the local backup, users.json and any new receipts into the
    OneDrive folder. -> dict of what was written, or None if no folder is set."""
    folder = config.CFG.get("onedrive_backup_dir")
    if not folder:
        return None
    when = when or config.now()
    key, _ = ensure_key()
    f = Fernet(key)
    os.makedirs(os.path.join(folder, "attachments"), exist_ok=True)
    out = {"db": None, "users": None, "attachments": 0}
    dst = os.path.join(folder, "finance-%s.db.fernet" % _stamp(when))
    _encrypt_file(f, local_db_path, dst)
    out["db"] = dst
    users = os.path.join(config.DATA, "users.json")
    if os.path.exists(users):
        _encrypt_file(f, users, os.path.join(folder, "users.json.fernet"))
        out["users"] = True
    for root, _dirs, files in os.walk(config.ATTACHMENTS_DIR):
        for name in files:
            dst_a = os.path.join(folder, "attachments", name + ".fernet")
            if not os.path.exists(dst_a):
                _encrypt_file(f, os.path.join(root, name), dst_a)
                out["attachments"] += 1
    rotate(folder, "finance-*.db.fernet", when.date())
    return out


def run():
    """The nightly job."""
    local = backup_local()
    deleted = rotate(config.BACKUPS_DIR, "finance-*.db")
    off = backup_offsite(local)
    logger.info("backup: %s; rotated out %d; offsite %s", local, len(deleted), off)
    return {"local": local, "rotated": len(deleted), "offsite": off}


# --------------------------------------------------------------------------
# restore + verify
# --------------------------------------------------------------------------
def restore(enc_path, key, to_dir):
    """Decrypt one .fernet file into to_dir. -> path of the plain file."""
    f = Fernet(key if isinstance(key, bytes) else key.encode("ascii"))
    with open(enc_path, "rb") as fh:
        data = f.decrypt(fh.read())
    os.makedirs(to_dir, exist_ok=True)
    out = os.path.join(to_dir, os.path.basename(enc_path)[:-len(".fernet")])
    with open(out, "wb") as fh:
        fh.write(data)
    return out


def newest_offsite():
    folder = config.CFG.get("onedrive_backup_dir")
    if not folder:
        return None
    files = sorted(glob.glob(os.path.join(folder, "finance-*.db.fernet")))
    return files[-1] if files else None


def verify():
    """Decrypt the newest offsite copy into a temp dir, run integrity_check,
    and compare per-table row counts with the live database. This proves the
    encrypted copy AND the key together, which is the only proof that matters."""
    enc = newest_offsite()
    if not enc:
        return {"ok": False, "error": "no offsite backup found"}
    key, _ = ensure_key()
    tmp = tempfile.mkdtemp(prefix="fd-verify-")
    try:
        plain = restore(enc, key, tmp)
        c = sqlite3.connect(plain)
        try:
            integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
            c.row_factory = sqlite3.Row
            restored = db.table_counts(c)
        finally:
            c.close()
        with db.read() as live:
            live_counts = db.table_counts(live)
        diff = {k: (live_counts.get(k), restored.get(k)) for k in set(live_counts) | set(restored)
                if live_counts.get(k) != restored.get(k)}
        return {"ok": integrity == "ok", "file": enc, "integrity": integrity,
                "tables": len(restored), "count_differences": diff,
                "note": "differences are expected for rows written since the backup"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def list_backups():
    local = sorted(glob.glob(os.path.join(config.BACKUPS_DIR, "finance-*.db")))
    folder = config.CFG.get("onedrive_backup_dir") or ""
    off = sorted(glob.glob(os.path.join(folder, "finance-*.db.fernet"))) if folder else []
    return {
        "local": [{"path": p, "bytes": os.path.getsize(p)} for p in local],
        "offsite": [{"path": p, "bytes": os.path.getsize(p)} for p in off],
        "offsite_dir": folder,
        "key_fingerprint": fingerprint(),
    }


def _cli():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("now")
    sub.add_parser("verify")
    sub.add_parser("ensure-key")
    sub.add_parser("fingerprint")
    r = sub.add_parser("restore")
    r.add_argument("file")
    r.add_argument("--key", required=True)
    r.add_argument("--to", required=True)
    a = ap.parse_args()
    log.setup()
    if a.cmd == "now":
        db.migrate()
        print(run())
    elif a.cmd == "verify":
        print(verify())
    elif a.cmd == "ensure-key":
        key, new = ensure_key()
        if new:
            print("NEW BACKUP KEY - put this in your password manager NOW. It is not shown again.")
            print(key.decode("ascii"))
        print("fingerprint: %s" % fingerprint(key))
    elif a.cmd == "fingerprint":
        print(fingerprint())
    elif a.cmd == "restore":
        print(restore(a.file, a.key, a.to))
    else:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
