"""The accountant packet: one ZIP with the Schedule C summary, every business
split as a row, income by source, contractors, mileage, home office, the
estimated payments, assets over the de minimis threshold, every transaction
of the year, and the receipts - named so the CSV rows point at them."""

import csv
import io
import os
import re
import zipfile

import attachments as A
import config
import db
import money
import reports
import taxcalc


def _csv(rows, columns):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c[1] for c in columns])
    for r in rows:
        w.writerow([c[2](r) if len(c) > 2 else r.get(c[0], "") for c in columns])
    return buf.getvalue()


def _dollars(c):
    return "%.2f" % (c / 100) if c is not None else ""


def _safe(s, n=40):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s or "").strip("_")
    return s[:n] or "x"


def build(conn, year, estimate=None):
    rates = taxcalc.load_rates(year)
    sc = reports.schedule_c(conn, year)
    frm, to = reports.year_range(year)
    biz = reports.business_splits(conn, frm, to)
    threshold = rates["form_1099_nec_threshold_cents"]
    contractors = reports.contractors(conn, year, threshold)
    sources = reports.income_by_source(conn, year)
    mileage = db.rows(conn, "SELECT * FROM mileage_log WHERE date BETWEEN ? AND ? ORDER BY date", (frm, to))
    ho = db.row(conn, "SELECT * FROM home_office WHERE year = ?", (year,))
    payments = db.rows(conn, "SELECT * FROM tax_payments WHERE year = ? ORDER BY date", (year,))
    assets = reports.assets_over_de_minimis(conn, year, rates["de_minimis_safe_harbor_cents"])
    all_txns = db.rows(conn, """SELECT t.id, t.date, a.name AS account, p.name AS payee, t.payee_raw, t.memo_raw, t.amount_cents,
                                       t.is_business, t.split_count, t.notes, t.status
                                FROM v_transactions t LEFT JOIN accounts a ON a.id = t.account_id LEFT JOIN payees p ON p.id = t.payee_id
                                WHERE t.date BETWEEN ? AND ? AND t.pending = 0 ORDER BY t.date, t.id""", (frm, to))

    receipt_names = {}   # attachment path -> zip name

    def receipt_name(s):
        paths = (s["attachment_paths"] or "").split(";") if s["attachment_paths"] else []
        out = []
        for p in paths:
            if not p:
                continue
            if p not in receipt_names:
                ext = os.path.splitext(p)[1]
                receipt_names[p] = "receipts/%s_%s_%s%s" % (s["date"], _safe(s["payee_name"] or s["payee_raw"]), _dollars(-s["amount_cents"]).replace("-", ""), ext)
            out.append(receipt_names[p])
        return "; ".join(out)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("schedule_c_summary.csv", _csv(sc["lines"], [
            ("line", "line"), ("label", "label"), ("amount_cents", "amount", lambda r: _dollars(r["amount_cents"])),
            ("deductible_cents", "deductible", lambda r: _dollars(r["deductible_cents"])), ("count", "splits"),
            ("missing_receipts", "missing_receipts")]))
        z.writestr("business_transactions.csv", _csv(biz, [
            ("date", "date"), ("account_name", "account"), ("payee_name", "payee"), ("payee_raw", "bank_text"),
            ("amount_cents", "amount", lambda r: _dollars(r["amount_cents"])),
            ("deductible_pct", "deductible_pct"), ("d", "deductible", lambda r: _dollars(reports.deductible(r))),
            ("line", "schedule_c_line"), ("category_name", "category"), ("note", "split_note"), ("notes", "transaction_notes"),
            ("status", "reviewed", lambda r: "yes" if r["status"] == "reviewed" else "no"),
            ("r", "receipt_files", receipt_name), ("transaction_id", "transaction_id")]))
        z.writestr("income_by_source.csv", _csv(sources, [("name", "source"), ("kind", "kind"), ("cents", "amount", lambda r: _dollars(r["cents"])), ("n", "deposits")]))
        z.writestr("contractors.csv", _csv(contractors["contractors"], [
            ("name", "name"), ("contractor_kind", "kind"), ("paid_cents", "paid", lambda r: _dollars(r["paid_cents"])), ("payments", "payments"),
            ("over_threshold", "over_1099_threshold", lambda r: "yes" if r["over_threshold"] else "no"),
            ("w9_on_file", "w9_on_file", lambda r: "yes" if r["w9_on_file"] else "no"),
            ("tin_on_file", "tin_on_file", lambda r: "yes" if r["tin_on_file"] else "no"), ("contact", "contact")]))
        z.writestr("mileage.csv", _csv(mileage, [("date", "date"), ("miles_e2", "miles", lambda r: "%.2f" % (r["miles_e2"] / 100)),
                                                 ("purpose", "purpose"), ("from_place", "from"), ("to_place", "to"),
                                                 ("business", "business", lambda r: "yes" if r["business"] else "no")]))
        ho_text = "No home office recorded for %d.\n" % year if not ho else (
            "Home office %d\nmethod: %s\noffice sqft: %s\ntotal home sqft: %s\nsimplified deduction: %s\n" % (
                year, ho["method"], ho["sqft"], ho["total_sqft"] or "",
                _dollars(reports.home_office_deduction(conn, year, rates)[0])))
        z.writestr("home_office.txt", ho_text)
        z.writestr("estimated_payments.csv", _csv(payments, [("date", "date"), ("quarter", "quarter"), ("jurisdiction", "jurisdiction"),
                                                             ("amount_cents", "amount", lambda r: _dollars(r["amount_cents"])), ("confirmation", "confirmation")]))
        z.writestr("assets_over_de_minimis.csv", _csv(assets, [("date", "date"), ("payee_name", "payee"), ("amount_cents", "amount", lambda r: _dollars(-r["amount_cents"])),
                                                              ("category_name", "category"), ("line", "line"), ("note", "note"), ("attachments", "receipts")]))
        z.writestr("all_transactions.csv", _csv(all_txns, [("date", "date"), ("account", "account"), ("payee", "payee"), ("payee_raw", "bank_text"), ("memo_raw", "memo"),
                                                           ("amount_cents", "amount", lambda r: _dollars(r["amount_cents"])),
                                                           ("is_business", "business", lambda r: "yes" if r["is_business"] else "no"),
                                                           ("split_count", "splits"), ("notes", "notes"), ("id", "id")]))
        for p, name in receipt_names.items():
            fp = A.full_path(p)
            if os.path.exists(fp):
                z.write(fp, name)
        est = estimate or {}
        readme = [
            "Finance Desk - accountant packet for %d" % year,
            "Generated %s. All tax figures are the app's own ESTIMATES (rates file %s, verified: %s)." % (
                config.iso_now(), rates.get("version"), rates.get("verified")),
            "",
            "schedule_c_summary.csv      totals by Schedule C line; 'deductible' applies the meals 50% etc.",
            "business_transactions.csv   one row per business split, with the receipt file names in receipts/",
            "income_by_source.csv        business income by source (AdSense per channel, sponsorships...)",
            "contractors.csv             everyone flagged as a contractor, paid YTD, 1099-NEC threshold %s" % _dollars(threshold),
            "mileage.csv, home_office.txt, estimated_payments.csv, assets_over_de_minimis.csv",
            "all_transactions.csv        every non-pending transaction of the year, personal included",
            "",
            "Estimate: net profit %s, SE tax %s, federal income tax %s, total %s" % (
                _dollars(est.get("net_profit_cents")), _dollars(est.get("se_tax_cents")),
                _dollars(est.get("federal_income_tax_cents")), _dollars(est.get("total_tax_cents"))) if est else "",
            "Unassigned Schedule C line total: %s (these need a line before filing)" % _dollars(sc["unassigned_cents"]),
        ]
        z.writestr("README.txt", "\n".join(readme) + "\n")
    buf.seek(0)
    return buf.getvalue()


def _fmt(c):
    return money.fmt(c)
