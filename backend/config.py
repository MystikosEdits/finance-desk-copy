"""Paths, config.json, and the clock.

Every path is derived from this file's location, so it does not matter what
the working directory is when the Scheduled Task starts the app.

WHY THE CLOCK IS HERE
---------------------
Nothing else in the backend calls datetime.now() directly. Tests replace
`config.now` with a fixed clock, which is the only way "is this pending charge
older than 7 days" or "which quarter are we in" can be tested without sleeping.
"""

import io
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA, "finance.db")
SECRETS_DIR = os.path.join(DATA, "secrets")
ATTACHMENTS_DIR = os.path.join(DATA, "attachments")
BACKUPS_DIR = os.path.join(DATA, "backups")
LOGS_DIR = os.path.join(DATA, "logs")
FRONTEND_DIST = os.path.join(ROOT, "frontend", "dist")
CONFIG_PATH = os.path.join(ROOT, "config.json")
RATES_DIR = os.path.join(HERE, "rates")

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8020,
    "auth_enabled": True,
    "sync_times": ["06:30", "14:30", "22:30"],
    "prices_time": "04:00",
    "backup_time": "03:30",
    "onedrive_backup_dir": "",
    "receipt_required_over_cents": 7500,
}


def load():
    """config.json over the defaults. Read as utf-8-sig: PowerShell's
    `Out-File -Encoding utf8` writes a BOM and json.load chokes on it. That
    cost the pipeline a session once; it will not cost this one."""
    cfg = dict(DEFAULTS)
    try:
        with io.open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            cfg.update(json.load(fh))
    except OSError:
        pass
    return cfg


CFG = load()


def now():
    """Local wall-clock time, naive. The rig is one machine in one timezone
    and every timestamp in the database is meant to be read by a human on it."""
    return datetime.now()


def today():
    return now().date()


def iso_now():
    return now().isoformat(timespec="seconds")


def ensure_dirs():
    for d in (DATA, SECRETS_DIR, ATTACHMENTS_DIR, BACKUPS_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
