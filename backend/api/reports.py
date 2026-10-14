import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import Response

import config
import db
import reports
from api import require_owner

router = APIRouter(prefix="/api/reports")


@router.get("/summary")
def summary():
    """The dashboard's numbers, all from one place."""
    today = config.today()
    m_from, m_to = reports.month_bounds(today)
    year = today.year
    with db.read() as conn:
        inbox = db.scalar(conn, "SELECT COUNT(*) FROM transactions WHERE status = 'new'")
        suggested = db.scalar(conn, "SELECT COUNT(*) FROM transfers WHERE status = 'suggested'")
        cf = reports.cashflow(conn, m_from, m_to)
        month = cf[0] if cf else {"income_cents": 0, "spending_cents": 0}
        prev_from = (date.fromisoformat(m_from) - timedelta(days=1)).replace(day=1).isoformat()
        prev_to = (date.fromisoformat(m_from) - timedelta(days=1)).isoformat()
        pcf = reports.cashflow(conn, prev_from, prev_to)
        prev = pcf[0] if pcf else {"income_cents": 0, "spending_cents": 0}
        nw = db.row(conn, "SELECT * FROM net_worth_snapshots ORDER BY date DESC LIMIT 1")
        nw_prev = db.row(conn, "SELECT * FROM net_worth_snapshots WHERE date <= ? ORDER BY date DESC LIMIT 1",
                         ((today - timedelta(days=30)).isoformat(),))
        # live fallback for net worth before the first snapshot: accounts only
        from api.accounts import with_balance
        accts = [with_balance(conn, a) for a in db.rows(conn, "SELECT * FROM accounts WHERE is_closed = 0")]
        live_assets = sum(a["balance_cents"] for a in accts if not a["is_liability"])
        live_liab = sum(a["balance_cents"] for a in accts if a["is_liability"])
        try:
            from api.tax import estimate
            _, est = estimate(conn, year)
            set_aside = est["set_aside_cents"]
            ytd_profit = est["net_profit_cents"]
        except Exception:  # noqa: BLE001 - the dashboard must render even if a rates file is missing
            set_aside, ytd_profit = None, None
        upcoming = db.rows(conn, """SELECT r.id, r.name, r.expected_cents, r.next_date, r.cadence, r.business
                                    FROM recurring r WHERE r.active = 1 AND r.status = 'confirmed' AND r.next_date IS NOT NULL
                                    AND r.next_date <= ? ORDER BY r.next_date LIMIT 12""",
                           ((today + timedelta(days=30)).isoformat(),))
        biz_month = reports.pnl(conn, m_from, m_to, "all")
        spend_top = reports.spending(conn, m_from, m_to, by="parent", business="0", limit=6)
    return {
        "today": today.isoformat(),
        "inbox": inbox, "suggested_transfers": suggested,
        "month": {"from": m_from, "to": m_to, "income_cents": month["income_cents"], "spending_cents": month["spending_cents"],
                  "prev_income_cents": prev["income_cents"], "prev_spending_cents": prev["spending_cents"]},
        "net_worth": {"cents": (nw["assets_cents"] + nw["liabilities_cents"]) if nw else live_assets + live_liab,
                      "date": nw["date"] if nw else None, "source": "snapshot" if nw else "accounts",
                      "prev_cents": (nw_prev["assets_cents"] + nw_prev["liabilities_cents"]) if nw_prev else None,
                      "assets_cents": nw["assets_cents"] if nw else live_assets,
                      "liabilities_cents": nw["liabilities_cents"] if nw else live_liab},
        "tax": {"year": year, "set_aside_cents": set_aside, "ytd_net_profit_cents": ytd_profit},
        "business_month": biz_month[0] if biz_month else None,
        "top_spending": spend_top,
        "upcoming": upcoming,
    }


@router.get("/cashflow")
def cashflow(frm: str, to: str, by: str | None = "month", business: str | None = "all"):
    with db.read() as conn:
        return reports.cashflow(conn, frm, to, by or "month", business or "all")


@router.get("/spending")
def spending(frm: str, to: str, by: str | None = "category", business: str | None = "all", limit: int | None = 100):
    with db.read() as conn:
        return reports.spending(conn, frm, to, by or "category", business or "all", limit or 100)


@router.get("/income")
def income(frm: str, to: str):
    with db.read() as conn:
        return db.rows(conn, """
            SELECT COALESCE(c.name, 'Uncategorized') AS name, c.id, s.business, SUM(s.amount_cents) AS cents, COUNT(*) AS n
            FROM splits s JOIN transactions t ON t.id = s.transaction_id LEFT JOIN categories c ON c.id = s.category_id
            WHERE t.pending = 0 AND t.date BETWEEN ? AND ? AND s.amount_cents > 0 AND (c.kind IS NULL OR c.kind = 'income')
            GROUP BY c.id, s.business ORDER BY cents DESC""", (frm, to))


@router.get("/debts")
def debts():
    with db.read() as conn:
        from api.accounts import with_balance
        rows = [with_balance(conn, a) for a in db.rows(conn, "SELECT * FROM accounts WHERE is_closed = 0 AND kind IN ('credit','loan','liability')")]
    out = []
    for a in rows:
        owed = -a["balance_cents"] if a["balance_cents"] < 0 else 0
        months = None
        interest = None
        if owed and a.get("min_payment_cents"):
            apr = (a.get("apr_bp") or 0) / 10000
            r = apr / 12
            p = a["min_payment_cents"]
            if r == 0:
                months = -(-owed // p)
            elif p > owed * r:
                import math
                months = math.ceil(-math.log(1 - owed * r / p) / math.log(1 + r))
            interest = int(months * p - owed) if months else None
        out.append({"id": a["id"], "name": a["name"], "kind": a["kind"], "owed_cents": owed, "apr_bp": a.get("apr_bp"),
                    "min_payment_cents": a.get("min_payment_cents"), "due_day": a.get("due_day"),
                    "months_to_payoff": months, "interest_to_payoff_cents": interest})
    return out


@router.get("/export.csv")
def export_csv(request: Request, frm: str | None = None, to: str | None = None, business: str | None = None, account: int | None = None):
    require_owner(request)
    with db.read() as conn:
        where, args = ["t.pending = 0"], []
        if frm:
            where.append("t.date >= ?"); args.append(frm)
        if to:
            where.append("t.date <= ?"); args.append(to)
        if business in ("0", "1"):
            where.append("t.is_business = ?"); args.append(int(business))
        if account:
            where.append("t.account_id = ?"); args.append(account)
        rows = db.rows(conn, """SELECT t.date, t.account_name AS account, t.payee_name AS payee, t.payee_raw, t.memo_raw, t.amount_cents,
                                       t.is_business, t.notes, t.status, t.id,
                                       (SELECT GROUP_CONCAT(COALESCE(c.name, ''), '|') FROM splits s LEFT JOIN categories c ON c.id = s.category_id WHERE s.transaction_id = t.id) AS categories
                                FROM v_transactions t WHERE %s ORDER BY t.date, t.id""" % " AND ".join(where), tuple(args))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Date", "Account", "Payee", "Description", "Memo", "Amount", "Business", "Categories", "Notes", "Reviewed", "Id"])
    for r in rows:
        w.writerow([r["date"], r["account"], r["payee"] or "", r["payee_raw"], r["memo_raw"], "%.2f" % (r["amount_cents"] / 100),
                    "yes" if r["is_business"] else "no", r["categories"] or "", r["notes"], "yes" if r["status"] == "reviewed" else "no", r["id"]])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="finance-desk-transactions.csv"'})
