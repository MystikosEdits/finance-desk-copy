"""One daemon thread that runs the periodic jobs: bank sync, prices, backup,
integrity. Jobs run one at a time, in this thread, so they never overlap
each other. "Run now" from the UI runs the job in its own thread but under
the same per-job guard, so it cannot overlap the scheduled run either.

WHY CATCH-UP ON BOOT
--------------------
The FinanceDesk task starts at LOGON, like Reaction Desk's. After an
unattended reboot (Windows Update at 3am) nothing runs until you log in,
and a 04:00 price job or 03:30 backup would simply be skipped. So at boot,
any job whose next_due_at is in the past runs once, then re-schedules. That
is one catch-up run per job, not a flood.
"""

import threading
import time
import traceback
from datetime import datetime, timedelta

import config
import db
import log

logger = log.get("scheduler")

_jobs = {}            # name -> {"fn": callable, "times": ["HH:MM", ...]}
_guards = {}          # name -> threading.Lock
_stop = threading.Event()
_thread = None


def register(name, fn, times):
    _jobs[name] = {"fn": fn, "times": list(times)}
    _guards[name] = threading.Lock()


def next_due(times, after):
    """The first HH:MM in `times` strictly after `after` (local)."""
    best = None
    for t in times:
        hh, mm = (int(x) for x in t.split(":"))
        cand = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= after:
            cand += timedelta(days=1)
        if best is None or cand < best:
            best = cand
    return best


def _ensure_rows():
    with db.write() as conn:
        for name, j in _jobs.items():
            r = db.row(conn, "SELECT next_due_at FROM jobs WHERE name = ?", (name,))
            if not r:
                conn.execute("INSERT INTO jobs (name, next_due_at) VALUES (?, ?)",
                             (name, next_due(j["times"], config.now()).isoformat(timespec="seconds")))
            elif r["next_due_at"] is None:
                conn.execute("UPDATE jobs SET next_due_at = ? WHERE name = ?",
                             (next_due(j["times"], config.now()).isoformat(timespec="seconds"), name))
        # A crash mid-job leaves running=1 forever; nothing is running at boot.
        conn.execute("UPDATE jobs SET running = 0")


def run(name):
    """Run one job now if it is not already running. -> True if it ran."""
    j = _jobs[name]
    guard = _guards[name]
    if not guard.acquire(blocking=False):
        return False
    try:
        with db.write() as conn:
            conn.execute("UPDATE jobs SET running = 1, last_started_at = ? WHERE name = ?",
                         (config.iso_now(), name))
        ok, err = 1, None
        try:
            logger.info("job %s: start", name)
            j["fn"]()
            logger.info("job %s: ok", name)
        except Exception as e:  # noqa: BLE001 - a job must never kill the loop
            ok, err = 0, "%s: %s" % (type(e).__name__, e)
            logger.error("job %s: FAILED %s\n%s", name, err, traceback.format_exc())
        with db.write() as conn:
            conn.execute("""UPDATE jobs SET running = 0, last_finished_at = ?, last_ok = ?,
                            last_error = ?, next_due_at = ? WHERE name = ?""",
                         (config.iso_now(), ok, err,
                          next_due(j["times"], config.now()).isoformat(timespec="seconds"), name))
        return True
    finally:
        guard.release()


def run_now(name):
    """From the UI: run in the background, immediately. -> False if busy."""
    if name not in _jobs:
        raise KeyError(name)
    if _guards[name].locked():
        return False
    threading.Thread(target=run, args=(name,), daemon=True, name="job-" + name).start()
    return True


def _loop():
    while not _stop.is_set():
        try:
            now = config.now()
            with db.read() as conn:
                due = [r["name"] for r in db.rows(
                    conn, "SELECT name, next_due_at FROM jobs WHERE next_due_at <= ?",
                    (now.isoformat(timespec="seconds"),)) if r["name"] in _jobs]
            for name in due:
                run(name)
        except Exception:  # noqa: BLE001
            logger.error("scheduler loop: %s", traceback.format_exc())
        _stop.wait(30)


def start():
    global _thread
    _ensure_rows()
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="scheduler")
    _thread.start()


def stop():
    _stop.set()


def status():
    with db.read() as conn:
        out = db.rows(conn, "SELECT * FROM jobs ORDER BY name")
    for r in out:
        r["times"] = _jobs.get(r["name"], {}).get("times", [])
        r["registered"] = r["name"] in _jobs
    return out
