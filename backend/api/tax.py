from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import audit
import config
import db
import export
import reports
import taxcalc
from api import actor, require_owner

router = APIRouter(prefix="/api/tax")


def _settings(conn, year):
    r = db.row(conn, "SELECT * FROM tax_years WHERE year = ?", (year,))
    if not r:
        r = {"year": year, "filing_status": "single", "state": "", "state_flat_rate_bp": 0, "prior_year_tax_cents": 0,
             "prior_year_agi_cents": 0, "w2_wages_cents": 0, "w2_withholding_cents": 0, "other_income_cents": 0,
             "itemized_cents": None, "notes": ""}
    return r


def estimate(conn, year):
    s = _settings(conn, year)
    rates = taxcalc.load_rates(year)
    sc = reports.schedule_c(conn, year)
    ho_cents, _ = reports.home_office_deduction(conn, year, rates)
    miles = reports.mileage_total(conn, year)
    mileage_cents = int(round(miles / 100 * rates["standard_mileage_cents_per_mile"]))
    pays = db.rows(conn, "SELECT quarter, jurisdiction, SUM(amount_cents) AS c FROM tax_payments WHERE year = ? GROUP BY quarter, jurisdiction", (year,))
    by_q = {}
    fed = st = 0
    for p in pays:
        by_q.setdefault(p["quarter"], {}).setdefault(p["jurisdiction"], 0)
        by_q[p["quarter"]][p["jurisdiction"]] += p["c"]
        if p["jurisdiction"] == "federal":
            fed += p["c"]
        else:
            st += p["c"]
    today = config.today()
    if year < today.year:
        frac = 1.0
    elif year > today.year:
        frac = 0.0
    else:
        frac = (today - date(year, 1, 1)).days / (366 if year % 4 == 0 else 365)
    inputs = taxcalc.TaxInputs(
        year=year, filing_status=s["filing_status"],
        business_income_cents=sc["income_cents"], business_expenses_cents=sc["expenses_deductible_cents"],
        home_office_cents=ho_cents, mileage_cents=mileage_cents,
        w2_wages_cents=s["w2_wages_cents"], w2_withholding_cents=s["w2_withholding_cents"],
        other_income_cents=s["other_income_cents"], itemized_cents=s["itemized_cents"],
        state_flat_rate_bp=s["state_flat_rate_bp"], prior_year_tax_cents=s["prior_year_tax_cents"],
        prior_year_agi_cents=s["prior_year_agi_cents"], payments_federal_cents=fed, payments_state_cents=st,
        payments_by_quarter=by_q, year_fraction=max(frac, 0.02),
    )
    est = taxcalc.compute(inputs, rates)
    est["unassigned_line_cents"] = sc["unassigned_cents"]
    est["schedule_c"] = sc
    return s, est


@router.get("/{year}")
def get_year(year: int):
    with db.read() as conn:
        s, est = estimate(conn, year)
    return {"settings": s, "estimate": est}


class SettingsReq(BaseModel):
    filing_status: str | None = None
    state: str | None = None
    state_flat_rate_bp: int | None = None
    prior_year_tax_cents: int | None = None
    prior_year_agi_cents: int | None = None
    w2_wages_cents: int | None = None
    w2_withholding_cents: int | None = None
    other_income_cents: int | None = None
    itemized_cents: int | None = None
    clear_itemized: bool | None = None
    notes: str | None = None


@router.put("/{year}/settings")
def put_settings(year: int, req: SettingsReq, request: Request):
    fields = {k: v for k, v in req.model_dump().items() if v is not None and k != "clear_itemized"}
    if req.clear_itemized:
        fields["itemized_cents"] = None
    if "filing_status" in fields and fields["filing_status"] not in ("single", "mfj", "mfs", "hoh"):
        raise HTTPException(400, "filing_status must be single, mfj, mfs or hoh")
    with db.write() as conn:
        conn.execute("INSERT OR IGNORE INTO tax_years (year) VALUES (?)", (year,))
        if fields:
            sets = ", ".join("%s = ?" % k for k in fields)
            conn.execute("UPDATE tax_years SET %s WHERE year = ?" % sets, tuple(fields.values()) + (year,))
        audit.log(conn, actor(request), "tax_year", year, "settings", None, fields)
        s, est = estimate(conn, year)
    return {"settings": s, "estimate": est}


@router.get("/{year}/payments")
def payments(year: int):
    with db.read() as conn:
        return db.rows(conn, "SELECT * FROM tax_payments WHERE year = ? ORDER BY date", (year,))


class PaymentReq(BaseModel):
    quarter: int
    jurisdiction: str
    amount_cents: int
    date: str
    txn_id: int | None = None
    confirmation: str | None = ""


@router.post("/{year}/payments")
def add_payment(year: int, req: PaymentReq, request: Request):
    if req.jurisdiction not in ("federal", "state") or not 1 <= req.quarter <= 4:
        raise HTTPException(400, "bad payment")
    with db.write() as conn:
        cur = conn.execute("INSERT INTO tax_payments (year, quarter, jurisdiction, amount_cents, date, txn_id, confirmation) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (year, req.quarter, req.jurisdiction, req.amount_cents, req.date, req.txn_id, req.confirmation or ""))
        if req.txn_id:
            amt = db.scalar(conn, "SELECT amount_cents FROM transactions WHERE id = ?", (req.txn_id,))
            if amt is not None:
                db.set_splits(conn, req.txn_id, [{"amount_cents": amt, "category_id": 506}], "user")
                conn.execute("UPDATE transactions SET status = 'reviewed' WHERE id = ?", (req.txn_id,))
        audit.log(conn, actor(request), "tax_payment", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM tax_payments WHERE id = ?", (cur.lastrowid,))


@router.delete("/{year}/payments/{pid}")
def delete_payment(year: int, pid: int, request: Request):
    with db.write() as conn:
        conn.execute("DELETE FROM tax_payments WHERE id = ? AND year = ?", (pid, year))
        audit.log(conn, actor(request), "tax_payment", pid, "delete")
    return {"ok": True}


@router.get("/rates/{year}")
def rates(year: int):
    return taxcalc.load_rates(year)


@router.get("/{year}/export.zip")
def export_zip(year: int, request: Request):
    require_owner(request)
    with db.read() as conn:
        _, est = estimate(conn, year)
        data = export.build(conn, year, est)
        audit.log if False else None
    with db.write() as conn:
        audit.log(conn, actor(request), "tax_year", year, "export", None, {"bytes": len(data)})
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="finance-desk-%d-accountant.zip"' % year})
