import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import config

H = {"X-Requested-With": "FinanceDesk"}


@pytest.fixture
def c(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", False)
    import app as appmod
    with TestClient(appmod.app) as client:
        yield client


def test_attachments_business_tax_export(c):
    chk = c.post("/api/accounts", json={"name": "Checking", "kind": "checking"}, headers=H).json()
    t = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-02-10", "amount_cents": -29900,
                                           "payee": "TOPAZ LABS", "category_id": 102, "business": 1}, headers=H).json()
    inc = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-02-20", "amount_cents": 400000,
                                             "payee": "GOOGLE ADSENSE", "category_id": 201, "business": 1}, headers=H).json()
    # receipt upload, view, dedupe
    r = c.post("/api/attachments", files={"file": ("topaz.pdf", b"%PDF-1.4 fake", "application/pdf")},
               data={"transaction_id": str(t["id"]), "kind": "receipt"}, headers=H)
    assert r.status_code == 200 and r.json()["original_name"] == "topaz.pdf"
    aid = r.json()["id"]
    assert c.get("/api/attachments/%d" % aid).content == b"%PDF-1.4 fake"
    r2 = c.post("/api/attachments", files={"file": ("topaz.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"transaction_id": str(t["id"]), "kind": "receipt"}, headers=H)
    assert r2.json()["id"] == aid
    assert c.post("/api/attachments", files={"file": ("x.exe", b"MZ", "application/octet-stream")},
                  data={"transaction_id": str(t["id"])}, headers=H).status_code == 400
    assert c.get("/api/transactions/%d" % t["id"]).json()["has_attachment"] == 1
    assert c.get("/api/attachments/missing?year=2026").json()["rows"] == []

    # income source + adsense split across channels
    s1 = c.post("/api/business/income-sources", json={"name": "Channel A", "kind": "adsense"}, headers=H).json()
    s2 = c.post("/api/business/income-sources", json={"name": "Channel A 2.0", "kind": "adsense"}, headers=H).json()
    r = c.post("/api/business/adsense-split", json={"txn_id": inc["id"], "allocations": [
        {"income_source_id": s1["id"], "amount_cents": 300000}, {"income_source_id": s2["id"], "amount_cents": 100000}]}, headers=H)
    assert r.status_code == 200
    src = c.get("/api/business/income-by-source?year=2026").json()["rows"]
    assert {x["name"]: x["cents"] for x in src} == {"Channel A": 300000, "Channel A 2.0": 100000}

    # schedule C, home office, mileage
    c.put("/api/business/home-office/2026", json={"sqft": 400, "method": "simplified"}, headers=H)
    c.post("/api/business/mileage", json={"date": "2026-03-01", "miles": 100, "purpose": "shoot"}, headers=H)
    sc = c.get("/api/business/schedule-c?year=2026").json()
    assert sc["income_cents"] == 400000 and sc["expenses_cents"] == 29900
    assert sc["home_office_cents"] == 150000     # capped at 300 sqft x $5
    assert sc["mileage_cents"] == round(100 * 72.5)
    assert sc["net_profit_cents"] == 400000 - 29900 - 150000 - sc["mileage_cents"]

    # tax estimate, settings, payments
    est = c.get("/api/tax/2026").json()["estimate"]
    assert est["net_profit_cents"] == sc["net_profit_cents"] and est["rates_verified"] is False
    r = c.put("/api/tax/2026/settings", json={"filing_status": "single", "state_flat_rate_bp": 0, "prior_year_tax_cents": 500000}, headers=H).json()
    assert r["settings"]["prior_year_tax_cents"] == 500000
    pay_txn = c.post("/api/transactions", json={"account_id": chk["id"], "date": "2026-04-10", "amount_cents": -50000, "payee": "IRS", "reviewed": False}, headers=H).json()
    r = c.post("/api/tax/2026/payments", json={"quarter": 1, "jurisdiction": "federal", "amount_cents": 50000, "date": "2026-04-10", "txn_id": pay_txn["id"]}, headers=H)
    assert r.status_code == 200
    got = c.get("/api/transactions/%d" % pay_txn["id"]).json()
    assert got["splits"][0]["category_id"] == 506 and got["status"] == "reviewed"
    est = c.get("/api/tax/2026").json()["estimate"]
    assert est["payments_federal_cents"] == 50000 and est["quarters"][0]["federal_paid_cents"] == 50000
    assert est["set_aside_cents"] == max(est["federal_total_cents"] - 50000, 0)

    # contractors
    p = c.post("/api/payees", json={"name": "Alex", "is_contractor": 1, "contractor_kind": "editor"}, headers=H).json()
    for d in ("2026-01-05", "2026-02-05", "2026-03-05", "2026-04-05", "2026-05-05"):
        c.post("/api/transactions", json={"account_id": chk["id"], "date": d, "amount_cents": -42000, "payee": "Alex", "category_id": 101, "business": 1}, headers=H)
    c.patch("/api/payees/%d" % p["id"], json={"apply_to_existing": True}, headers=H)
    con = c.get("/api/business/contractors?year=2026").json()
    alex = next(x for x in con["contractors"] if x["name"] == "Alex")
    assert alex["paid_cents"] == 210000 and alex["over_threshold"] and alex["needs_w9"]
    assert con["threshold_cents"] == 200000

    # the accountant packet
    r = c.get("/api/tax/2026/export.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    for n in ("schedule_c_summary.csv", "business_transactions.csv", "income_by_source.csv", "contractors.csv",
              "mileage.csv", "home_office.txt", "estimated_payments.csv", "assets_over_de_minimis.csv", "all_transactions.csv", "README.txt"):
        assert n in names
    receipts = [n for n in names if n.startswith("receipts/")]
    assert len(receipts) == 1 and receipts[0].endswith(".pdf")
    biz = z.read("business_transactions.csv").decode("utf-8")
    assert receipts[0] in biz
    summary = z.read("schedule_c_summary.csv").decode("utf-8")
    assert "11,Contract labor,2100.00,2100.00" in summary
    # Schedule C view totals equal the export's per-line sum to the cent
    sc = c.get("/api/business/schedule-c?year=2026").json()
    import csv
    rows = list(csv.DictReader(io.StringIO(summary)))
    assert sum(round(float(r["deductible"]) * 100) for r in rows if r["line"] != "1") == sc["expenses_deductible_cents"]
