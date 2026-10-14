"""Rollups over splits. SQL where SQL is enough, a little Python where the
shape needs it. Everything the Business, Taxes, Cash flow and Reports pages
show comes from here, and so does the accountant export - one implementation
per number, so two views of it cannot disagree."""

from datetime import date

import db

LINE_LABELS = {
    "1": "Gross receipts", "8": "Advertising", "9": "Car and truck", "10": "Commissions and fees",
    "11": "Contract labor", "13": "Depreciation", "15": "Insurance", "16b": "Interest (other)",
    "17": "Legal and professional", "18": "Office expense", "20b": "Rent (other)", "21": "Repairs and maintenance",
    "22": "Supplies", "23": "Taxes and licenses", "24a": "Travel", "24b": "Meals", "25": "Utilities",
    "26": "Wages", "27a": "Other expenses", "30": "Home office",
}

BUSINESS_SPLITS = """
    SELECT s.id AS split_id, s.transaction_id, s.amount_cents, s.business, s.deductible_pct,
           COALESCE(s.schedule_c_line, c.default_schedule_c_line) AS line,
           s.category_id, c.name AS category_name, c.kind AS category_kind, c.parent_id AS category_parent_id,
           s.income_source_id, s.note,
           t.date, t.account_id, t.payee_id, t.payee_raw, t.description_raw, t.memo_raw, t.notes, t.status,
           p.name AS payee_name, p.is_contractor, p.contractor_kind, p.w9_on_file, p.tin_on_file,
           a.name AS account_name,
           (SELECT COUNT(*) FROM attachments at WHERE at.transaction_id = t.id) AS attachments,
           (SELECT GROUP_CONCAT(at.original_name, '; ') FROM attachments at WHERE at.transaction_id = t.id) AS attachment_names,
           (SELECT GROUP_CONCAT(at.path, ';') FROM attachments at WHERE at.transaction_id = t.id) AS attachment_paths
    FROM splits s
    JOIN transactions t ON t.id = s.transaction_id
    LEFT JOIN categories c ON c.id = s.category_id
    LEFT JOIN payees p ON p.id = t.payee_id
    LEFT JOIN accounts a ON a.id = t.account_id
    WHERE s.business = 1 AND t.pending = 0 AND t.date >= ? AND t.date <= ?
      AND (c.kind IS NULL OR c.kind <> 'transfer')
    ORDER BY t.date, t.id, s.id"""


def year_range(year):
    return "%d-01-01" % year, "%d-12-31" % year


def business_splits(conn, frm, to):
    return db.rows(conn, BUSINESS_SPLITS, (frm, to))


def deductible(split):
    return int(round(split["amount_cents"] * (split["deductible_pct"] or 100) / 100))


def schedule_c(conn, year):
    """Lines with totals. Income (positive amounts) -> line 1; expenses (negative)
    -> their line, or 'unassigned'. Deductible = amount x deductible_pct."""
    frm, to = year_range(year)
    rows = business_splits(conn, frm, to)
    lines = {}
    income, expenses, expenses_deductible = 0, 0, 0
    unassigned = 0
    for s in rows:
        if s["amount_cents"] > 0 or s["category_kind"] == "income":
            key = "1"
            income += s["amount_cents"]
            amt, ded = s["amount_cents"], s["amount_cents"]
        else:
            key = s["line"] or "unassigned"
            amt, ded = -s["amount_cents"], -deductible(s)
            expenses += amt
            expenses_deductible += ded
            if key == "unassigned":
                unassigned += amt
        L = lines.setdefault(key, {"line": key, "label": LINE_LABELS.get(key, "Unassigned line" if key == "unassigned" else key),
                                   "amount_cents": 0, "deductible_cents": 0, "count": 0, "missing_receipts": 0})
        L["amount_cents"] += amt
        L["deductible_cents"] += ded
        L["count"] += 1
        if key != "1" and not s["attachments"]:
            L["missing_receipts"] += 1

    def order(k):
        if k == "1":
            return (0, 0)
        if k == "unassigned":
            return (2, 0)
        num = "".join(ch for ch in k if ch.isdigit())
        return (1, int(num or 0), k)

    return {
        "year": year,
        "lines": [lines[k] for k in sorted(lines, key=order)],
        "income_cents": income, "expenses_cents": expenses, "expenses_deductible_cents": expenses_deductible,
        "unassigned_cents": unassigned,
        "net_profit_before_adjustments_cents": income - expenses_deductible,
        "split_count": len(rows),
    }


def pnl(conn, frm, to, by="month"):
    rows = business_splits(conn, frm, to)
    buckets = {}
    for s in rows:
        if by == "month":
            key = s["date"][:7]
        elif by == "category":
            key = s["category_name"] or "Uncategorized"
        elif by == "source":
            key = s["income_source_id"] or 0
        else:
            key = "all"
        b = buckets.setdefault(key, {"key": key, "income_cents": 0, "expenses_cents": 0, "deductible_cents": 0, "count": 0})
        if s["amount_cents"] > 0 or s["category_kind"] == "income":
            b["income_cents"] += s["amount_cents"]
        else:
            b["expenses_cents"] += -s["amount_cents"]
            b["deductible_cents"] += -deductible(s)
        b["count"] += 1
    out = sorted(buckets.values(), key=lambda b: str(b["key"]))
    for b in out:
        b["net_cents"] = b["income_cents"] - b["expenses_cents"]
    if by == "source":
        names = {r["id"]: r["name"] for r in db.rows(conn, "SELECT id, name FROM income_sources")}
        for b in out:
            b["name"] = names.get(b["key"], "Unassigned source")
    return out


def income_by_source(conn, year):
    frm, to = year_range(year)
    rows = db.rows(conn, """
        SELECT COALESCE(i.name, 'Unassigned') AS name, i.kind, SUM(s.amount_cents) AS cents, COUNT(*) AS n
        FROM splits s JOIN transactions t ON t.id = s.transaction_id
        LEFT JOIN categories c ON c.id = s.category_id LEFT JOIN income_sources i ON i.id = s.income_source_id
        WHERE s.business = 1 AND s.amount_cents > 0 AND t.pending = 0 AND t.date BETWEEN ? AND ?
          AND (c.kind IS NULL OR c.kind = 'income')
        GROUP BY i.id ORDER BY cents DESC""", (frm, to))
    return rows


def contractors(conn, year, threshold_cents):
    frm, to = year_range(year)
    rows = db.rows(conn, """
        SELECT p.id, p.name, p.contractor_kind, p.w9_on_file, p.tin_on_file, p.contact,
               COALESCE(SUM(CASE WHEN s.business = 1 THEN -s.amount_cents END), 0) AS paid_cents,
               COUNT(DISTINCT t.id) AS payments,
               MIN(t.date) AS first_date, MAX(t.date) AS last_date
        FROM payees p
        LEFT JOIN transactions t ON t.payee_id = p.id AND t.pending = 0 AND t.date BETWEEN ? AND ? AND t.amount_cents < 0
        LEFT JOIN splits s ON s.transaction_id = t.id
        WHERE p.is_contractor = 1
        GROUP BY p.id ORDER BY paid_cents DESC""", (frm, to))
    for r in rows:
        r["over_threshold"] = r["paid_cents"] >= threshold_cents
        r["needs_w9"] = r["over_threshold"] and not r["w9_on_file"]
    # Contract-labor line rows paid to someone NOT flagged as a contractor: a nudge.
    unflagged = db.rows(conn, """
        SELECT p.id, p.name, SUM(-s.amount_cents) AS paid_cents, COUNT(*) AS payments
        FROM splits s JOIN transactions t ON t.id = s.transaction_id JOIN payees p ON p.id = t.payee_id
        LEFT JOIN categories c ON c.id = s.category_id
        WHERE s.business = 1 AND t.pending = 0 AND t.date BETWEEN ? AND ? AND s.amount_cents < 0
          AND COALESCE(s.schedule_c_line, c.default_schedule_c_line) = '11' AND p.is_contractor = 0
        GROUP BY p.id ORDER BY paid_cents DESC""", (frm, to))
    return {"contractors": rows, "unflagged_contract_labor": unflagged, "threshold_cents": threshold_cents}


def missing_receipts(conn, year, min_cents):
    frm, to = year_range(year)
    return db.rows(conn, """
        SELECT t.id, t.date, t.amount_cents, t.payee_raw, p.name AS payee_name, a.name AS account_name,
               SUM(-s.amount_cents) AS business_cents, GROUP_CONCAT(DISTINCT c.name) AS categories
        FROM transactions t JOIN splits s ON s.transaction_id = t.id
        LEFT JOIN categories c ON c.id = s.category_id LEFT JOIN payees p ON p.id = t.payee_id LEFT JOIN accounts a ON a.id = t.account_id
        WHERE s.business = 1 AND s.amount_cents < 0 AND t.pending = 0 AND t.date BETWEEN ? AND ?
          AND (c.kind IS NULL OR c.kind = 'expense')
          AND NOT EXISTS (SELECT 1 FROM attachments at WHERE at.transaction_id = t.id)
        GROUP BY t.id HAVING business_cents >= ? ORDER BY t.date DESC""", (frm, to, min_cents))


def assets_over_de_minimis(conn, year, threshold_cents):
    frm, to = year_range(year)
    return db.rows(conn, """
        SELECT t.id, t.date, t.amount_cents, t.payee_raw, p.name AS payee_name, c.name AS category_name,
               COALESCE(s.schedule_c_line, c.default_schedule_c_line) AS line, s.note,
               (SELECT COUNT(*) FROM attachments at WHERE at.transaction_id = t.id) AS attachments
        FROM splits s JOIN transactions t ON t.id = s.transaction_id
        LEFT JOIN categories c ON c.id = s.category_id LEFT JOIN payees p ON p.id = t.payee_id
        WHERE s.business = 1 AND s.amount_cents <= -? AND t.pending = 0 AND t.date BETWEEN ? AND ?
          AND (c.kind IS NULL OR c.kind = 'expense')
        ORDER BY s.amount_cents""", (threshold_cents, frm, to))


def mileage_total(conn, year):
    frm, to = year_range(year)
    return db.scalar(conn, "SELECT COALESCE(SUM(miles_e2), 0) FROM mileage_log WHERE business = 1 AND date BETWEEN ? AND ?", (frm, to))


def home_office_deduction(conn, year, rates):
    ho = db.row(conn, "SELECT * FROM home_office WHERE year = ?", (year,))
    if not ho or ho["method"] == "none":
        return 0, ho
    if ho["method"] == "simplified":
        sqft = min(ho["sqft"], rates["home_office"]["simplified_max_sqft"])
        return sqft * rates["home_office"]["simplified_rate_cents_per_sqft"], ho
    # 'actual' needs the accountant's numbers; we report the percentage only.
    return 0, ho


def cashflow(conn, frm, to, by="month", business="all"):
    """Personal-view cash flow: income vs spending per period, transfers excluded."""
    where = "t.pending = 0 AND t.date BETWEEN ? AND ? AND (c.kind IS NULL OR c.kind <> 'transfer')"
    args = [frm, to]
    if business == "0":
        where += " AND s.business = 0"
    elif business == "1":
        where += " AND s.business = 1"
    key = "substr(t.date, 1, 7)" if by == "month" else "substr(t.date, 1, 4)"
    return db.rows(conn, """
        SELECT %s AS period,
               COALESCE(SUM(CASE WHEN s.amount_cents > 0 THEN s.amount_cents END), 0) AS income_cents,
               COALESCE(SUM(CASE WHEN s.amount_cents < 0 THEN -s.amount_cents END), 0) AS spending_cents,
               COUNT(*) AS n
        FROM splits s JOIN transactions t ON t.id = s.transaction_id LEFT JOIN categories c ON c.id = s.category_id
        WHERE %s GROUP BY period ORDER BY period""" % (key, where), tuple(args))


def spending(conn, frm, to, by="category", business="all", limit=200):
    where = "t.pending = 0 AND t.date BETWEEN ? AND ? AND s.amount_cents < 0 AND (c.kind IS NULL OR c.kind = 'expense')"
    args = [frm, to]
    if business in ("0", "1"):
        where += " AND s.business = ?"
        args.append(int(business))
    if by == "payee":
        sel, grp = "COALESCE(p.name, t.payee_raw) AS name, p.id AS id", "COALESCE(p.id, t.payee_raw)"
    elif by == "parent":
        sel, grp = "COALESCE(pc.name, c.name, 'Uncategorized') AS name, COALESCE(pc.id, c.id) AS id", "COALESCE(pc.id, c.id)"
    else:
        sel, grp = "COALESCE(c.name, 'Uncategorized') AS name, c.id AS id", "c.id"
    return db.rows(conn, """
        SELECT %s, SUM(-s.amount_cents) AS cents, COUNT(*) AS n
        FROM splits s JOIN transactions t ON t.id = s.transaction_id
        LEFT JOIN categories c ON c.id = s.category_id LEFT JOIN categories pc ON pc.id = c.parent_id
        LEFT JOIN payees p ON p.id = t.payee_id
        WHERE %s GROUP BY %s ORDER BY cents DESC LIMIT ?""" % (sel, where, grp), tuple(args) + (limit,))


def month_bounds(d=None):
    d = d or date.today()
    start = d.replace(day=1)
    if d.month == 12:
        end = d.replace(day=31)
    else:
        end = d.replace(month=d.month + 1, day=1).fromordinal(d.replace(month=d.month + 1, day=1).toordinal() - 1)
    return start.isoformat(), end.isoformat()
