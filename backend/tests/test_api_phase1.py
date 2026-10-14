"""Accounts -> manual + CSV transactions -> inbox -> splits -> rules -> transfers,
through the HTTP API with auth off (localhost)."""

import os

import pytest
from fastapi.testclient import TestClient

import config

H = {"X-Requested-With": "FinanceDesk"}
HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def c(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", False)
    import app as appmod
    with TestClient(appmod.app) as client:
        yield client


def test_accounts_transactions_inbox_flow(c):
    chk = c.post("/api/accounts", json={"name": "Checking", "kind": "checking", "institution": "Chase"}, headers=H).json()
    card = c.post("/api/accounts", json={"name": "Card", "kind": "credit", "institution": "Chase"}, headers=H).json()
    assert chk["balance_cents"] == 0 and card["is_liability"]

    # manual entry lands reviewed with the user's split
    r = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-09-01", "amount_cents": -4200,
                                           "payee": "Blue Bottle", "category_id": 413}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "reviewed" and r.json()["splits"][0]["category_id"] == 413

    # CSV preview then commit
    with open(os.path.join(HERE, "fixtures", "csv", "chase_card.csv"), "rb") as fh:
        pv = c.post("/api/imports/csv/preview", files={"file": ("chase.csv", fh, "text/csv")},
                    data={"account_id": str(card["id"])}, headers=H).json()
    assert pv["would_insert"] == 4 and pv["would_skip"] == 0
    import json
    with open(os.path.join(HERE, "fixtures", "csv", "chase_card.csv"), "rb") as fh:
        r = c.post("/api/imports/csv", files={"file": ("chase.csv", fh, "text/csv")},
                   data={"account_id": str(card["id"]), "profile": json.dumps(pv["profile"]), "save_profile_as": "Chase card"}, headers=H)
    assert r.status_code == 200 and r.json()["inserted"] == 4
    with open(os.path.join(HERE, "fixtures", "csv", "chase_card.csv"), "rb") as fh:
        r = c.post("/api/imports/csv", files={"file": ("chase.csv", fh, "text/csv")},
                   data={"account_id": str(card["id"]), "profile": json.dumps(pv["profile"])}, headers=H)
    assert r.status_code == 409
    assert c.get("/api/imports/csv/profiles").json()[0]["name"] == "Chase card"

    inbox = c.get("/api/transactions/inbox").json()
    assert inbox["total"] == 4 and inbox["rows"][0]["date"] <= inbox["rows"][-1]["date"]
    assert len(inbox["rows"]) == inbox["total"]  # never capped
    adobe = next(t for t in inbox["rows"] if "ADOBE" in t["payee_raw"])

    # split it 40/60 business, review
    r = c.put("/api/transactions/%d/splits" % adobe["id"], json={"splits": [
        {"amount_cents": -800, "category_id": 102, "business": 1}, {"amount_cents": -1199, "category_id": 463}], "review": True}, headers=H)
    assert r.status_code == 200 and r.json()["is_business"] == 1 and r.json()["split_count"] == 2
    r = c.put("/api/transactions/%d/splits" % adobe["id"], json={"splits": [{"amount_cents": -1}]}, headers=H)
    assert r.status_code == 400

    # a rule from the adobe row, applied to everything: must not touch the user's split
    sug = c.post("/api/rules/from-transaction/%d" % adobe["id"], headers=H).json()
    rule = c.post("/api/rules", json={"name": sug["name"], "match": sug["match_json"], "action": {"category_id": 102, "business": 1}}, headers=H).json()
    assert c.post("/api/rules/%d/apply?scope=all" % rule["id"], headers=H).json()["changed"] == 0
    got = c.get("/api/transactions/%d" % adobe["id"]).json()
    assert got["split_count"] == 2

    # the card payment pairs with a checking-side payment as a transfer
    pay = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-09-04", "amount_cents": -120000,
                                             "payee": "ONLINE PAYMENT TO CHASE CARD", "reviewed": False}, headers=H).json()
    c.post("/api/transfers/detect", headers=H)
    got = c.get("/api/transactions/%d" % pay["id"]).json()
    assert got["transfer_id"] and got["splits"][0]["category_id"] == 501 and got["status"] == "reviewed"
    lst = c.get("/api/transfers").json()
    assert len(lst) == 1 and lst[0]["status"] == "auto"
    # reject it: both sides go back to the inbox
    c.post("/api/transfers/%d/reject" % lst[0]["id"], headers=H)
    assert c.get("/api/transactions/%d" % pay["id"]).json()["status"] == "new"
    c.post("/api/transfers/detect", headers=H)
    assert c.get("/api/transfers").json() == []

    # bulk categorise + review, then filters
    ids = [t["id"] for t in c.get("/api/transactions/inbox").json()["rows"]]
    r = c.post("/api/transactions/bulk", json={"ids": ids, "action": "review"}, headers=H).json()
    assert r["changed"] == len(ids)
    assert c.get("/api/transactions/inbox").json()["total"] == 0
    assert c.get("/api/transactions?business=1").json()["total"] == 1
    assert c.get("/api/transactions?q=blue").json()["total"] == 3
    # a synced-looking row cannot be deleted; a csv one can
    assert c.delete("/api/transactions/%d" % adobe["id"], headers=H).status_code == 200
    # undo import removes only untouched new rows (none left now)
    imp = c.get("/api/imports").json()[0]
    assert c.post("/api/imports/%d/undo" % imp["id"], headers=H).json()["deleted"] == 0


def test_categories_and_payees(c):
    cats = c.get("/api/categories").json()
    meals = next(x for x in cats if x["name"] == "Business meals")
    assert meals["default_deductible_pct"] == 50 and meals["full_name"] == "Business › Business meals"
    new = c.post("/api/categories", json={"name": "Thumbnails", "kind": "expense", "parent_id": 100, "default_business": 1, "default_schedule_c_line": "11"}, headers=H).json()
    assert new["default_schedule_c_line"] == "11"
    assert c.post("/api/categories/%d/merge-into/101" % new["id"], headers=H).json()["ok"]
    p = c.post("/api/payees", json={"name": "Alex", "is_contractor": 1, "contractor_kind": "editor"}, headers=H).json()
    assert c.get("/api/payees?contractors=1").json()[0]["id"] == p["id"]
    r = c.patch("/api/payees/%d" % p["id"], json={"default_category_id": 101, "default_business": 1}, headers=H).json()
    assert r["default_category_id"] == 101


def test_credit_scores_and_car_parts(c):
    assert any(x["name"] == "Car parts" for x in c.get("/api/categories").json())
    assert c.post("/api/credit", json={"score": 900}, headers=H).status_code == 400
    c.post("/api/credit", json={"score": 701, "date": "2026-07-01", "source": "Credit Karma (TransUnion)"}, headers=H)
    c.post("/api/credit", json={"score": 742, "date": "2026-09-01", "source": "Credit Karma (TransUnion)"}, headers=H)
    c.post("/api/credit", json={"score": 760, "date": "2026-09-02", "source": "Experian"}, headers=H)
    r = c.get("/api/credit").json()
    assert r["latest"]["score"] == 760 and r["previous_same_source"] is None and r["delta"] is None
    c.delete("/api/credit/%d" % r["latest"]["id"], headers=H)
    r = c.get("/api/credit").json()
    assert r["latest"]["score"] == 742 and r["delta"] == 41
