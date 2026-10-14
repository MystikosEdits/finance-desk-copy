"""The sync's reconcile() over SimpleFIN-shaped fixtures, then the whole
apply path against a real database."""

import json
import os
from datetime import datetime

import db
import simplefin
import sync
from conftest import make_account

HERE = os.path.dirname(os.path.abspath(__file__))


def feed(name, account="ACT-CHK-1"):
    with open(os.path.join(HERE, "fixtures", "simplefin", name), encoding="utf-8") as fh:
        accounts, _ = simplefin.normalize(json.load(fh))
    return next(a for a in accounts if a["simplefin_id"] == account)


def loc(**kw):
    base = {"id": 1, "external_id": "X", "pending": 0, "amount_cents": -100, "date": "2026-09-01",
            "posted_date": "2026-09-01", "payee_raw": "", "description_raw": "", "vanished_at": None, "touched": 0}
    base.update(kw)
    return base


NOW = datetime(2026, 9, 7, 12, 0)


def test_first_sync_inserts_everything():
    plan = sync.reconcile(feed("day1.json")["transactions"], [], NOW)
    assert len(plan.inserts) == 4 and not plan.updates and not plan.rekeys


def test_second_sync_inserts_nothing_new_and_updates_only_mutable_fields():
    f = feed("day1.json")["transactions"]
    local = [loc(id=i + 1, external_id=t["external_id"], pending=t["pending"], amount_cents=t["amount_cents"],
                 date=t["date"], posted_date=t["posted_date"], payee_raw=t["payee_raw"], description_raw=t["description_raw"])
             for i, t in enumerate(f)]
    plan = sync.reconcile(f, local, NOW)
    assert plan.counts() == {"inserted": 0, "updated": 0, "rekeyed": 0, "vanished": 0, "deleted": 0}


def test_pending_then_posted_same_id():
    f = [{"external_id": "P1", "date": "2026-09-05", "posted_date": "2026-09-06", "amount_cents": -5500,
          "payee_raw": "", "description_raw": "THE DINER", "memo_raw": "", "pending": 0}]
    local = [loc(id=7, external_id="P1", pending=1, amount_cents=-5500, date="2026-09-05", posted_date=None, description_raw="THE DINER")]
    plan = sync.reconcile(f, local, NOW)
    assert plan.updates == [(7, {"pending": 0, "posted_date": "2026-09-06"})]
    assert not plan.inserts and not plan.rekeys


def test_pending_then_posted_new_id_is_rekeyed_not_duplicated():
    day1 = feed("day1.json")["transactions"]
    local = [loc(id=i + 1, external_id=t["external_id"], pending=t["pending"], amount_cents=t["amount_cents"],
                 date=t["date"], posted_date=t["posted_date"], payee_raw=t["payee_raw"], description_raw=t["description_raw"])
             for i, t in enumerate(day1)]
    day2 = feed("day2.json")["transactions"]
    plan = sync.reconcile(day2, local, NOW)
    # PEND-9 (-55.00 THE DINER) became TXN-3 (-66.00, tip added) -> tip rekey
    assert len(plan.rekeys) == 1
    tid, new_ext, upd = plan.rekeys[0]
    assert tid == 3 and new_ext == "TXN-3" and upd["amount_cents"] == -6600 and upd["pending"] == 0
    # SPOTIFY is genuinely new; PEND-HOLD vanished
    assert [i["external_id"] for i in plan.inserts] == ["TXN-4"]
    assert plan.vanished == [4] and plan.deletes == []


def test_tip_rekey_refuses_a_row_the_user_touched():
    f = [{"external_id": "N", "date": "2026-09-05", "posted_date": "2026-09-06", "amount_cents": -6600,
          "payee_raw": "", "description_raw": "THE DINER SF", "memo_raw": "", "pending": 0}]
    local = [loc(id=3, external_id="P", pending=1, amount_cents=-5500, date="2026-09-05", posted_date=None,
                 description_raw="THE DINER SF", touched=1)]
    plan = sync.reconcile(f, local, NOW)
    assert not plan.rekeys and len(plan.inserts) == 1 and plan.vanished == [3]


def test_exact_amount_rekey_works_even_when_touched():
    f = [{"external_id": "N", "date": "2026-09-05", "posted_date": "2026-09-06", "amount_cents": -5500,
          "payee_raw": "", "description_raw": "THE DINER SF", "memo_raw": "", "pending": 0}]
    local = [loc(id=3, external_id="P", pending=1, amount_cents=-5500, date="2026-09-05", posted_date=None,
                 description_raw="TST* THE DINER", touched=1)]
    plan = sync.reconcile(f, local, NOW)
    assert len(plan.rekeys) == 1 and plan.rekeys[0][1] == "N" and "amount_cents" not in plan.rekeys[0][2]


def test_vanished_untouched_pending_is_deleted_after_grace():
    local = [loc(id=5, external_id="HOLD", pending=1, amount_cents=-100, date="2026-08-20", vanished_at="2026-08-28T10:00:00"),
             loc(id=6, external_id="HOLD2", pending=1, amount_cents=-100, date="2026-08-20", vanished_at="2026-08-28T10:00:00", touched=1),
             loc(id=8, external_id="HOLD3", pending=1, amount_cents=-100, date="2026-09-06", vanished_at="2026-09-06T10:00:00")]
    plan = sync.reconcile([], local, NOW)
    assert plan.deletes == [5] and plan.vanished == []


def test_two_identical_pendings_rekey_one_to_one():
    f = [{"external_id": "A", "date": "2026-09-05", "posted_date": "2026-09-06", "amount_cents": -400, "payee_raw": "", "description_raw": "COFFEE", "memo_raw": "", "pending": 0},
         {"external_id": "B", "date": "2026-09-05", "posted_date": "2026-09-06", "amount_cents": -400, "payee_raw": "", "description_raw": "COFFEE", "memo_raw": "", "pending": 0}]
    local = [loc(id=1, external_id="p1", pending=1, amount_cents=-400, date="2026-09-05", description_raw="COFFEE"),
             loc(id=2, external_id="p2", pending=1, amount_cents=-400, date="2026-09-05", description_raw="COFFEE")]
    plan = sync.reconcile(f, local, NOW)
    assert sorted(t for t, _, _ in plan.rekeys) == [1, 2] and not plan.inserts


def test_apply_end_to_end_keeps_user_work_across_rekey(conn, clock):
    a_id = make_account(conn, "Checking", simplefin_id="ACT-CHK-1", institution="Chase")
    account = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (a_id,))
    day1 = feed("day1.json")
    with db.write() as w:
        plan = sync.reconcile(day1["transactions"], [], clock.at)
        ids = sync.apply(w, account, plan, clock.at.isoformat())
        assert len(ids) == 4
        # the person categorises the pending diner charge and adds a note
        diner = db.row(w, "SELECT id FROM transactions WHERE external_id = 'PEND-9'")["id"]
        db.set_splits(w, diner, [{"amount_cents": -5500, "category_id": 412}], "user")
        w.execute("UPDATE transactions SET notes = 'lunch with editor' WHERE id = ?", (diner,))
        # and the coffee row gets a receipt-less categorisation by a rule (not user)
        coffee = db.row(w, "SELECT id FROM transactions WHERE external_id = 'TXN-1'")["id"]
        db.set_splits(w, coffee, [{"amount_cents": -4210, "category_id": 413}], "rule:1")
    with db.write() as w:
        local = sync._local_rows(w, a_id, clock.at)
        touched = {r["id"]: r["touched"] for r in local}
        assert touched[diner] == 1 and touched[coffee] == 0
        plan = sync.reconcile(feed("day2.json")["transactions"], local, clock.at)
        # user-touched diner (-55) vs posted -66: tip pass refuses -> insert + vanish
        assert not plan.rekeys and [i["external_id"] for i in plan.inserts] == ["TXN-3", "TXN-4"]
        sync.apply(w, account, plan, clock.at.isoformat())
    assert db.scalar(conn, "SELECT notes FROM transactions WHERE id = ?", (diner,)) == "lunch with editor"
    assert db.scalar(conn, "SELECT vanished_at FROM transactions WHERE id = ?", (diner,)) is not None
    assert db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE account_id = ?", (a_id,)) == 6
    assert db.integrity_check()["ok"]
