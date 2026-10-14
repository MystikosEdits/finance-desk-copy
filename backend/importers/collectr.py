"""Collectr CSV export -> collectibles rows. Collectr has no public API; the
app's PRO export and the community Chrome exporter both produce a CSV with
some subset of: item/card name, set, number, condition, quantity, market
price (per unit), total value, purchase price. Headers vary by version, so
this maps by keyword and treats anything money-shaped ("$12.34") as cents."""

import csv
import io

import money
from importers import csvmap

_HDRS = {
    "name": ("item name", "card name", "name", "item", "card", "product", "title"),
    "set": ("set name", "set", "expansion", "series", "edition"),
    "number": ("card number", "number", "no.", "#", "collector number"),
    "condition": ("condition", "grade", "cond"),
    "quantity": ("quantity", "qty", "count", "owned"),
    "unit_value": ("market price", "market value", "price", "unit value", "value each", "current price", "current value", "tcg market price"),
    "total_value": ("total value", "total", "market total", "value total"),
    "cost": ("purchase price", "cost", "paid", "purchase cost", "buy price", "cost basis"),
}


def sniff(raw, skip_rows=0):
    base = csvmap.sniff(raw, skip_rows)
    headers = base["headers"]
    lower = [h.lower().strip() for h in headers]
    cmap = {}
    for key, cands in _HDRS.items():
        for test in (lambda h, c: h == c, lambda h, c: h.startswith(c), lambda h, c: c in h):
            if key in cmap:
                break
            for c in cands:
                for i, h in enumerate(lower):
                    if test(h, c) and headers[i] not in cmap.values():
                        cmap[key] = headers[i]
                        break
                if key in cmap:
                    break
    base["guess"] = {"column_map": cmap, "skip_rows": skip_rows}
    return base


def _money(v):
    v = (v or "").strip()
    if not v or v in ("-", "—", "N/A", "n/a"):
        return 0
    try:
        return money.parse(v)
    except ValueError:
        return 0


def _int(v):
    v = (v or "").strip().replace(",", "")
    if not v:
        return 1
    try:
        return max(0, int(float(v)))
    except ValueError:
        return 1


def parse(raw, profile, delimiter=None):
    text = csvmap.decode(raw)
    lines = text.splitlines()
    body = "\n".join(lines[int(profile.get("skip_rows") or 0):])
    delim = delimiter or csvmap.sniff(raw, int(profile.get("skip_rows") or 0))["delimiter"]
    reader = csv.DictReader(io.StringIO(body), delimiter=delim)
    cm = profile["column_map"]
    if "name" not in cm:
        return [], ["could not find a name column"]
    rows, errors, seen = [], [], {}
    for n, rec in enumerate(reader, start=2):
        rec = {(k or "").strip(): (v or "").strip() for k, v in rec.items()}
        name = rec.get(cm["name"], "")
        if not name:
            continue
        qty = _int(rec.get(cm.get("quantity", ""), "1")) if "quantity" in cm else 1
        unit = _money(rec.get(cm.get("unit_value", ""), "")) if "unit_value" in cm else 0
        total = _money(rec.get(cm.get("total_value", ""), "")) if "total_value" in cm else 0
        if not total and unit:
            total = unit * qty
        if not unit and total and qty:
            unit = total // qty
        cost = _money(rec.get(cm.get("cost", ""), "")) if "cost" in cm else None
        key = "|".join([name, rec.get(cm.get("set", ""), ""), rec.get(cm.get("number", ""), ""), rec.get(cm.get("condition", ""), "")]).lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = "%s#%d" % (key, seen[key])
        rows.append({"external_key": key, "name": name, "set_name": rec.get(cm.get("set", ""), ""), "number": rec.get(cm.get("number", ""), ""),
                     "condition": rec.get(cm.get("condition", ""), ""), "quantity": qty, "unit_value_cents": unit, "value_cents": total,
                     "cost_cents": cost if cost else None})
    if not rows:
        errors.append("no rows with a name")
    return rows, errors
