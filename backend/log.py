"""One rotating log file. A Scheduled Task's stdout goes nowhere, so a nightly
job that fails at 03:30 is invisible unless it is written here."""

import logging
import logging.handlers
import os

import config

_configured = False


def setup():
    global _configured
    if _configured:
        return
    _configured = True
    config.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(config.LOGS_DIR, "app.log"),
        maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get(name):
    return logging.getLogger(name)
