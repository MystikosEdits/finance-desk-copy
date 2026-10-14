"""Receipts, invoices, W-9s. Files live under data/attachments/YYYY/ named by
their sha256, so the same receipt uploaded twice is one file, and the nightly
backup can copy the folder incrementally by name."""

import hashlib
import os

import config
import db

ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".txt", ".csv", ".eml", ".html", ".htm", ".doc", ".docx", ".xls", ".xlsx"}
MAX_BYTES = 25 * 1024 * 1024


def store(conn, data: bytes, original_name: str, kind: str, transaction_id=None, payee_id=None, when=None):
    if not transaction_id and not payee_id:
        raise ValueError("an attachment belongs to a transaction or a payee")
    if len(data) > MAX_BYTES:
        raise ValueError("file is over 25 MB")
    ext = os.path.splitext(original_name or "")[1].lower() or ".bin"
    if ext not in ALLOWED:
        raise ValueError("file type %s is not accepted" % ext)
    sha = hashlib.sha256(data).hexdigest()
    when = when or config.iso_now()
    year = when[:4]
    rel = os.path.join(year, sha + ext)
    full = os.path.join(config.ATTACHMENTS_DIR, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if not os.path.exists(full):
        tmp = full + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, full)
    dup = db.row(conn, "SELECT id FROM attachments WHERE sha256 = ? AND transaction_id IS ? AND payee_id IS ?",
                 (sha, transaction_id, payee_id))
    if dup:
        return dup["id"], False
    cur = conn.execute("""INSERT INTO attachments (transaction_id, payee_id, kind, path, original_name, sha256, bytes, added_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (transaction_id, payee_id, kind, rel.replace("\\", "/"), original_name or sha + ext, sha, len(data), when))
    return cur.lastrowid, True


def full_path(rel):
    return os.path.join(config.ATTACHMENTS_DIR, rel.replace("/", os.sep))


def delete(conn, attachment_id):
    a = db.row(conn, "SELECT * FROM attachments WHERE id = ?", (attachment_id,))
    if not a:
        return False
    conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    others = db.scalar(conn, "SELECT COUNT(*) FROM attachments WHERE path = ?", (a["path"],))
    if not others:
        try:
            os.remove(full_path(a["path"]))
        except OSError:
            pass
    return True
