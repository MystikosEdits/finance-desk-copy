"""Broker CSV -> trades. Same idea as csvmap: a profile maps columns
(date, symbol, action, quantity, price, amount, fee) and sniff() drafts one.
The action column is mapped by keyword: buy/bought -> buy, sell/sold -> sell,
div -> dividend, int -> interest, fee -> fee, split -> split."""

import csv
import hashlib
import io
from datetime import datetime

import money
from importers import csvmap

_HDRS = {
    "date": ("trade date", "date", "run date", "activity date", "transaction date", "settlement date", "timestamp", "time", "date/time"),
    "symbol": ("symbol", "ticker", "asset", "coin", "currency", "instrument"),
    "action": ("action", "type", "transaction type", "activity", "side", "description"),
    "quantity": ("quantity", "qty", "shares", "units", "amount (asset)"),
    "price": ("price", "unit price", "price per share", "spot price", "price ($)"),
    "amount": ("amount", "total", "net amount", "total ($)", "cash"),
    "fee": ("fee", "fees", "commission", "fees and/or spread", "commission ($)"),
}
_ACTIONS = [("split", "split"), ("dividend", "dividend"), ("div", "dividend"), ("interest", "interest"), ("int ", "interest"),
            ("fee", "fee"), ("bought", "buy"), ("buy", "buy"), ("purchase", "buy"), ("sold", "sell"), ("sell", "sell"),
            ("receive", "transfer_in"), ("transfer in", "transfer_in"), ("deposit", "transfer_in"),
            ("send", "transfer_out"), ("transfer out", "transfer_out"), ("withdraw", "transfer_out")]


def map_action(text):
    t = (text or "").lower()
    for k, v in _ACTIONS:
        if k in t:
            return v
    return None


def sniff(raw, skip_rows=0):
    base = csvmap.sniff(raw, skip_rows)
    headers = base["headers"]
    lower = [h.lower() for h in headers]
    cmap = {}
    for key, cands in _HDRS.items():
        for c in cands:
            if c in lower:
                cmap[key] = headers[lower.index(c)]
                break
        # "Total (inclusive of fees)" must beat "Subtotal" for "total": a header
        # that STARTS with the candidate wins before one that merely contains it.
        for test in (lambda h, c: h.startswith(c), lambda h, c: c in h):
            if key in cmap:
                break
            for c in cands:
                for i, h in enumerate(lower):
                    if test(h, c) and headers[i] not in cmap.values():
                        cmap[key] = headers[i]
                        break
                if key in cmap:
                    break
    fmt = "%m/%d/%Y"
    if "date" in cmap and base["sample"]:
        col = headers.index(cmap["date"])
        fmt = csvmap.guess_date_format([r[col] for r in base["sample"] if len(r) > col and r[col].strip()]) or fmt
    base["guess"] = {"column_map": cmap, "date_format": fmt, "skip_rows": skip_rows}
    return base


def parse(raw, profile, account_id, delimiter=None):
    text = csvmap.decode(raw)
    lines = text.splitlines()
    body = "\n".join(lines[int(profile.get("skip_rows") or 0):])
    delim = delimiter or csvmap.sniff(raw, int(profile.get("skip_rows") or 0))["delimiter"]
    reader = csv.DictReader(io.StringIO(body), delimiter=delim)
    cm = profile["column_map"]
    fmt = profile.get("date_format") or "%m/%d/%Y"
    rows, errors = [], []
    for n, rec in enumerate(reader, start=2):
        rec = {(k or "").strip(): (v or "").strip() for k, v in rec.items()}
        if not any(rec.values()):
            continue
        try:
            d = datetime.strptime(rec.get(cm.get("date", ""), ""), fmt).date().isoformat()
        except ValueError:
            errors.append("line %d: bad date" % n)
            continue
        symbol = rec.get(cm.get("symbol", ""), "").upper().strip()
        kind = map_action(rec.get(cm.get("action", ""), ""))
        if not symbol or not kind:
            errors.append("line %d: no symbol or unknown action %r" % (n, rec.get(cm.get("action", ""), "")))
            continue
        try:
            qty = abs(money.to_e8(rec.get(cm.get("quantity", ""), "") or "0"))
            price = abs(money.to_e8(rec.get(cm.get("price", ""), "") or "0"))
            fee = abs(money.parse(rec.get(cm.get("fee", ""), "") or "0"))
            amount = money.parse(rec.get(cm.get("amount", ""), "") or "0")
        except ValueError as e:
            errors.append("line %d: %s" % (n, e))
            continue
        if not amount and qty and price:
            amount = money.mul_e8_to_cents(qty, price) + fee
        total = -abs(amount) if kind in ("buy", "fee") else abs(amount) if kind in ("sell", "dividend", "interest") else 0
        ext = hashlib.sha256(("%s|%s|%s|%s|%d|%d|%d" % (account_id, d, symbol, kind, qty, price, total)).encode()).hexdigest()
        rows.append({"date": d, "symbol": symbol, "kind": kind, "qty_e8": qty, "price_e8": price, "fee_cents": fee,
                     "total_cents": total, "external_id": ext})
    return rows, errors
