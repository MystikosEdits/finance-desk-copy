from datetime import datetime

import db
import scheduler


def test_next_due_picks_first_future_time():
    at = datetime(2026, 9, 7, 12, 0)
    assert scheduler.next_due(["06:30", "14:30", "22:30"], at) == datetime(2026, 9, 7, 14, 30)
    assert scheduler.next_due(["06:30"], at) == datetime(2026, 9, 8, 6, 30)
    assert scheduler.next_due(["12:00"], at) == datetime(2026, 9, 8, 12, 0)


def test_catch_up_runs_overdue_job_once(fresh_db, clock, monkeypatch):
    runs = []
    scheduler._jobs.clear()
    scheduler._guards.clear()
    scheduler.register("t", lambda: runs.append(1), ["03:30"])
    with db.write() as w:
        w.execute("INSERT INTO jobs (name, next_due_at) VALUES ('t', '2026-09-07T03:30:00')")
    scheduler._ensure_rows()
    # the loop body, once
    with db.read() as conn:
        due = [r["name"] for r in db.rows(conn, "SELECT name FROM jobs WHERE next_due_at <= ?",
                                          (clock.at.isoformat(timespec="seconds"),))]
    for n in due:
        scheduler.run(n)
    assert runs == [1]
    with db.read() as conn:
        assert db.scalar(conn, "SELECT next_due_at FROM jobs WHERE name = 't'") == "2026-09-08T03:30:00"
        assert db.scalar(conn, "SELECT last_ok FROM jobs WHERE name = 't'") == 1


def test_failed_job_records_error_and_reschedules(fresh_db, clock):
    scheduler._jobs.clear()
    scheduler._guards.clear()

    def boom():
        raise RuntimeError("nope")

    scheduler.register("b", boom, ["01:00"])
    scheduler._ensure_rows()
    assert scheduler.run("b")
    with db.read() as conn:
        r = db.row(conn, "SELECT * FROM jobs WHERE name = 'b'")
    assert r["last_ok"] == 0 and "nope" in r["last_error"] and r["running"] == 0
