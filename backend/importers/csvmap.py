"""Bank CSV in, normalized rows out. Every bank's CSV is different in the same
four ways: which column is the date and in what format, whether the amount is
one signed column or a debit and a credit column, which sign means money out,
and what the text columns are called. A profile answers those four questions
once per bank; sniff() guesses a first draft from the headers.

WHY A DETERMINISTIC external_id
-------------------------------
CSV rows have no bank id. Without one, importing last year's statement after
SimpleFIN already pulled the last 90 days double-counts the overlap, and
importing the same file twice doubles everything. So each row gets
sha256(account|date|cents|normalized text|n) where n is the occurrence index
of identical rows within the file - two identical coffees on one day are two
rows, the same file twice is zero. Then a fuzzy pass catches the SimpleFIN
overlap, where the bank's text differs slightly between feed and export.
"""

import csv
import hashlib
import io
from datetime import datetime

import money
import payees

DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d/%m/%Y", "%b %d, %Y", "%m-%d-%Y", "%Y/%m/%d", "%d-%b-%Y",
                "%B %d, %Y", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]

_DATE_HDRS = ("transaction date", "trans date", "posted date", "post date", "date", "trans. date")
_AMOUNT_HDRS = ("amount", "transaction amount", "amt")
_DEBIT_HDRS = ("debit", "withdrawal", "withdrawals", "money out", "outflow", "charge")
_CREDIT_HDRS = ("credit", "deposit", "deposits", "money in", "inflow", "payment")
_PAYEE_HDRS = ("description", "payee", "name", "merchant", "merchant name", "transaction description", "details", "narrative")
_MEMO_HDRS = ("memo", "notes", "note", "extended description", "original description", "reference")


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def sniff(raw: bytes, skip_rows=0):
    """-> {headers, delimiter, guess (column_map, date_format, negate), sample}"""
    text = decode(raw)
    lines = text.splitlines()
    body = "\n".join(lines[skip_rows:])
    try:
        dialect = csv.Sniffer().sniff(body[:4096], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    reader = csv.reader(io.StringIO(body), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return {"headers": [], "delimiter": delim, "guess": {}, "sample": []}
    headers = [h.strip() for h in rows[0]]
    sample = rows[1:21]
    lower = [h.lower() for h in headers]

    def pick(cands):
        for c in cands:
            if c in lower:
                return headers[lower.index(c)]
        for c in cands:
            for i, h in enumerate(lower):
                if c in h:
                    return headers[i]
        return None

    cmap = {}
    d = pick(_DATE_HDRS)
    if d:
        cmap["date"] = d
    debit, credit = pick(_DEBIT_HDRS), pick(_CREDIT_HDRS)
    amount = pick(_AMOUNT_HDRS)
    if debit and credit and debit != credit:
        cmap["debit"], cmap["credit"] = debit, credit
    elif amount:
        cmap["amount"] = amount
    elif debit or credit:
        cmap["amount"] = debit or credit
    p = pick(_PAYEE_HDRS)
    if p:
        cmap["payee"] = p
    m = pick(_MEMO_HDRS)
    if m and m != p:
        cmap["memo"] = m
    date_format = "%m/%d/%Y"
    if d and sample:
        col = headers.index(d)
        vals = [r[col] for r in sample if len(r) > col and r[col].strip()]
        date_format = guess_date_format(vals) or date_format
    negate = 0
    if "amount" in cmap and sample and "debit" not in cmap:
        # If most rows are positive and the text looks like purchases, the bank
        # writes charges as positive numbers. Weak signal; the preview shows it.
        col = headers.index(cmap["amount"])
        vals = []
        for r in sample:
            try:
                vals.append(money.parse(r[col]))
            except (ValueError, IndexError):
                pass
        if vals and sum(1 for v in vals if v > 0) >= 0.75 * len(vals):
            negate = 1
    return {"headers": headers, "delimiter": delim,
            "guess": {"column_map": cmap, "date_format": date_format, "negate": negate, "skip_rows": skip_rows},
            "sample": sample}


def guess_date_format(values):
    for fmt in DATE_FORMATS:
        ok = 0
        for v in values[:10]:
            try:
                datetime.strptime(v.strip(), fmt)
                ok += 1
            except ValueError:
                pass
        if ok == len(values[:10]) and ok:
            return fmt
    return None


def parse(raw: bytes, profile, account_id, delimiter=None):
    """profile: {column_map, date_format, negate, skip_rows}. -> (rows, errors).
    Rows carry date, amount_cents, payee_raw, description_raw, memo_raw, external_id."""
    text = decode(raw)
    lines = text.splitlines()
    body = "\n".join(lines[int(profile.get("skip_rows") or 0):])
    delim = delimiter or sniff(raw, int(profile.get("skip_rows") or 0))["delimiter"]
    reader = csv.DictReader(io.StringIO(body), delimiter=delim)
    cmap = profile["column_map"]
    fmt = profile.get("date_format") or "%m/%d/%Y"
    negate = bool(profile.get("negate"))
    rows, errors, seen = [], [], {}
    for n, rec in enumerate(reader, start=2):
        rec = {(k or "").strip(): (v or "").strip() for k, v in rec.items()}
        if not any(rec.values()):
            continue
        try:
            dv = rec.get(cmap.get("date", ""), "")
            d = datetime.strptime(dv, fmt).date().isoformat()
        except ValueError:
            errors.append("line %d: bad date %r" % (n, rec.get(cmap.get("date", ""), "")))
            continue
        try:
            if "amount" in cmap:
                cents = money.parse(rec.get(cmap["amount"], ""))
                if negate:
                    cents = -cents
            else:
                debit = money.parse(rec.get(cmap.get("debit", ""), "") or "0")
                credit = money.parse(rec.get(cmap.get("credit", ""), "") or "0")
                cents = credit - abs(debit)
        except ValueError as e:
            errors.append("line %d: %s" % (n, e))
            continue
        payee = rec.get(cmap.get("payee", ""), "")
        memo = rec.get(cmap.get("memo", ""), "") if cmap.get("memo") else ""
        key = (d, cents, payees.normalize(payee or memo))
        seen[key] = seen.get(key, 0) + 1
        ext = hashlib.sha256(("%s|%s|%d|%s|%d" % (account_id, d, cents, key[2], seen[key])).encode("utf-8")).hexdigest()
        rows.append({"date": d, "posted_date": d, "amount_cents": cents, "payee_raw": payee,
                     "description_raw": payee, "memo_raw": memo, "external_id": ext, "pending": 0})
    return rows, errors


def find_fuzzy_duplicate(existing, row, days=2, sim=0.6):
    """existing: rows of the account (date, amount_cents, payee_raw, description_raw, external_id).
    -> the existing row that looks like `row`, or None."""
    from datetime import date
    rd = date.fromisoformat(row["date"])
    best = None
    for e in existing:
        if e["amount_cents"] != row["amount_cents"]:
            continue
        if abs((date.fromisoformat(e["date"]) - rd).days) > days:
            continue
        s = payees.similarity("%s %s" % (e.get("payee_raw") or "", e.get("description_raw") or ""),
                              "%s %s" % (row.get("payee_raw") or "", row.get("memo_raw") or ""))
        if s >= sim and (best is None or s > best[0]):
            best = (s, e)
    return best[1] if best else None
