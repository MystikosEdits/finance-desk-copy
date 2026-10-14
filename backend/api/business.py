from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import config
import db
import reports
import taxcalc
from api import actor

router = APIRouter(prefix="/api/business")


def _year(y):
    return y or config.today().year


@router.get("/pnl")
def pnl(year: int | None = None, frm: str | None = None, to: str | None = None, by: str | None = "month"):
    if not frm or not to:
        frm, to = reports.year_range(_year(year))
    with db.read() as conn:
        rows = reports.pnl(conn, frm, to, by or "month")
    return {"from": frm, "to": to, "by": by, "rows": rows,
            "income_cents": sum(r["income_cents"] for r in rows), "expenses_cents": sum(r["expenses_cents"] for r in rows),
            "deductible_cents": sum(r["deductible_cents"] for r in rows)}


@router.get("/schedule-c")
def schedule_c(year: int | None = None):
    year = _year(year)
    with db.read() as conn:
        out = reports.schedule_c(conn, year)
        rates = taxcalc.load_rates(year)
        ho_cents, ho = reports.home_office_deduction(conn, year, rates)
        miles = reports.mileage_total(conn, year)
        out["home_office_cents"] = ho_cents
        out["home_office"] = ho
        out["mileage_miles_e2"] = miles
        out["mileage_cents"] = int(round(miles / 100 * rates["standard_mileage_cents_per_mile"]))
        out["mileage_rate_cents"] = rates["standard_mileage_cents_per_mile"]
        out["net_profit_cents"] = out["net_profit_before_adjustments_cents"] - ho_cents - out["mileage_cents"]
        out["rates_version"] = rates.get("version")
    return out


@router.get("/contractors")
def contractors(year: int | None = None):
    year = _year(year)
    with db.read() as conn:
        rates = taxcalc.load_rates(year)
        out = reports.contractors(conn, year, rates["form_1099_nec_threshold_cents"])
    out["year"] = year
    return out


@router.get("/income-sources")
def income_sources():
    with db.read() as conn:
        return db.rows(conn, "SELECT * FROM income_sources ORDER BY active DESC, name")


class SourceReq(BaseModel):
    name: str
    kind: str
    youtube_channel_id: str | None = None
    active: int | None = 1


@router.post("/income-sources")
def create_source(req: SourceReq, request: Request):
    if req.kind not in ("adsense", "sponsorship", "affiliate", "other"):
        raise HTTPException(400, "bad kind")
    with db.write() as conn:
        try:
            cur = conn.execute("INSERT INTO income_sources (name, kind, youtube_channel_id, active) VALUES (?, ?, ?, ?)",
                               (req.name.strip(), req.kind, req.youtube_channel_id, 1 if req.active is None else req.active))
        except Exception as e:
            raise HTTPException(400, "could not create: %s" % e)
        audit.log(conn, actor(request), "income_source", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM income_sources WHERE id = ?", (cur.lastrowid,))


@router.patch("/income-sources/{sid}")
def patch_source(sid: int, req: SourceReq, request: Request):
    with db.write() as conn:
        conn.execute("UPDATE income_sources SET name = ?, kind = ?, youtube_channel_id = ?, active = ? WHERE id = ?",
                     (req.name.strip(), req.kind, req.youtube_channel_id, 1 if req.active is None else req.active, sid))
        audit.log(conn, actor(request), "income_source", sid, "patch", None, req.model_dump())
        return db.row(conn, "SELECT * FROM income_sources WHERE id = ?", (sid,))


@router.get("/income-by-source")
def income_by_source(year: int | None = None):
    year = _year(year)
    with db.read() as conn:
        return {"year": year, "rows": reports.income_by_source(conn, year)}


class SplitAlloc(BaseModel):
    income_source_id: int
    amount_cents: int


class AdsenseSplitReq(BaseModel):
    txn_id: int
    allocations: list[SplitAlloc]


@router.post("/adsense-split")
def adsense_split(req: AdsenseSplitReq, request: Request):
    """One AdSense deposit -> one split per channel."""
    with db.write() as conn:
        t = db.row(conn, "SELECT * FROM transactions WHERE id = ?", (req.txn_id,))
        if not t:
            raise HTTPException(404, "no such transaction")
        parts = [{"amount_cents": a.amount_cents, "category_id": 201, "business": 1, "income_source_id": a.income_source_id}
                 for a in req.allocations]
        try:
            db.set_splits(conn, req.txn_id, parts, "user")
        except ValueError as e:
            raise HTTPException(400, str(e))
        conn.execute("UPDATE transactions SET status = 'reviewed' WHERE id = ?", (req.txn_id,))
        audit.log(conn, actor(request), "transaction", req.txn_id, "adsense-split", None, req.model_dump())
    return {"ok": True}


# --------------------------------------------------------------------------
# mileage
# --------------------------------------------------------------------------
@router.get("/mileage")
def mileage(year: int | None = None):
    year = _year(year)
    frm, to = reports.year_range(year)
    with db.read() as conn:
        rows = db.rows(conn, "SELECT * FROM mileage_log WHERE date BETWEEN ? AND ? ORDER BY date DESC", (frm, to))
        rates = taxcalc.load_rates(year)
    total = sum(r["miles_e2"] for r in rows if r["business"])
    return {"year": year, "rows": rows, "business_miles_e2": total, "rate_cents_per_mile": rates["standard_mileage_cents_per_mile"],
            "deduction_cents": int(round(total / 100 * rates["standard_mileage_cents_per_mile"]))}


class MileageReq(BaseModel):
    date: str
    miles: float
    purpose: str
    from_place: str | None = ""
    to_place: str | None = ""
    business: int | None = 1


@router.post("/mileage")
def add_mileage(req: MileageReq, request: Request):
    with db.write() as conn:
        cur = conn.execute("INSERT INTO mileage_log (date, miles_e2, purpose, from_place, to_place, business) VALUES (?, ?, ?, ?, ?, ?)",
                           (req.date, int(round(req.miles * 100)), req.purpose, req.from_place or "", req.to_place or "", 1 if req.business is None else req.business))
        audit.log(conn, actor(request), "mileage", cur.lastrowid, "create", None, req.model_dump())
        return db.row(conn, "SELECT * FROM mileage_log WHERE id = ?", (cur.lastrowid,))


@router.delete("/mileage/{mid}")
def delete_mileage(mid: int, request: Request):
    with db.write() as conn:
        conn.execute("DELETE FROM mileage_log WHERE id = ?", (mid,))
        audit.log(conn, actor(request), "mileage", mid, "delete")
    return {"ok": True}


# --------------------------------------------------------------------------
# home office
# --------------------------------------------------------------------------
@router.get("/home-office/{year}")
def home_office(year: int):
    with db.read() as conn:
        ho = db.row(conn, "SELECT * FROM home_office WHERE year = ?", (year,))
        rates = taxcalc.load_rates(year)
        cents, _ = reports.home_office_deduction(conn, year, rates)
    return {"year": year, "home_office": ho, "deduction_cents": cents,
            "simplified_rate_cents_per_sqft": rates["home_office"]["simplified_rate_cents_per_sqft"],
            "simplified_max_sqft": rates["home_office"]["simplified_max_sqft"]}


class HomeOfficeReq(BaseModel):
    sqft: int
    total_sqft: int | None = None
    method: str | None = "simplified"


@router.put("/home-office/{year}")
def put_home_office(year: int, req: HomeOfficeReq, request: Request):
    if req.method not in ("simplified", "actual", "none"):
        raise HTTPException(400, "method must be simplified, actual or none")
    with db.write() as conn:
        conn.execute("""INSERT INTO home_office (year, sqft, total_sqft, method) VALUES (?, ?, ?, ?)
                        ON CONFLICT(year) DO UPDATE SET sqft = excluded.sqft, total_sqft = excluded.total_sqft, method = excluded.method""",
                     (year, req.sqft, req.total_sqft, req.method or "simplified"))
        audit.log(conn, actor(request), "home_office", year, "put", None, req.model_dump())
    return home_office(year)


@router.get("/assets")
def assets(year: int | None = None):
    year = _year(year)
    with db.read() as conn:
        rates = taxcalc.load_rates(year)
        rows = reports.assets_over_de_minimis(conn, year, rates["de_minimis_safe_harbor_cents"])
    return {"year": year, "threshold_cents": rates["de_minimis_safe_harbor_cents"], "rows": rows}
