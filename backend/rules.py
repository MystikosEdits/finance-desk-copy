"""Auto-categorisation. A rule is a match (all present keys must hold) and an
action. Rules run on NEW rows at sync/import time, and on demand from the
Rules page. They never overwrite a split a person set: db.set_splits with
keep_user=True refuses, and that refusal is the whole guarantee.

match_json keys:  payee_contains, regex, payee_id, account_id, sign ("in"|"out"),
                  amount_min_cents, amount_max_cents  (on the absolute value)
action_json keys: payee_id, category_id, business, schedule_c_line, deductible_pct,
                  income_source_id, tags [names], note, mark_reviewed,
                  split [{pct, category_id, business, schedule_c_line, deductible_pct, income_source_id, note}]
"""

import json
import re

import config
import db
import money


def _text(t):
    return "%s %s %s %s" % (t.get("payee_name") or "", t.get("payee_raw") or "",
                            t.get("description_raw") or "", t.get("memo_raw") or "")


def matches(match, t):
    if match.get("payee_contains"):
        if match["payee_contains"].lower() not in _text(t).lower():
            return False
    if match.get("regex"):
        try:
            if not re.search(match["regex"], _text(t), re.I):
                return False
        except re.error:
            return False
    if match.get("payee_id") is not None and t.get("payee_id") != match["payee_id"]:
        return False
    if match.get("account_id") is not None and t.get("account_id") != match["account_id"]:
        return False
    if match.get("sign") == "in" and t["amount_cents"] <= 0:
        return False
    if match.get("sign") == "out" and t["amount_cents"] >= 0:
        return False
    a = abs(t["amount_cents"])
    if match.get("amount_min_cents") is not None and a < int(match["amount_min_cents"]):
        return False
    if match.get("amount_max_cents") is not None and a > int(match["amount_max_cents"]):
        return False
    return True


def splits_for(action, t, conn=None):
    """The split list an action produces for transaction t. Pure, apart from an
    optional category lookup for the deductible default."""
    amt = t["amount_cents"]

    def ded(part):
        if part.get("deductible_pct") is not None:
            return int(part["deductible_pct"])
        cat = part.get("category_id")
        if cat and conn is not None:
            r = db.row(conn, "SELECT default_deductible_pct FROM categories WHERE id = ?", (cat,))
            if r:
                return r["default_deductible_pct"]
        return 100

    def line(part):
        if part.get("schedule_c_line"):
            return part["schedule_c_line"]
        return None

    if action.get("split"):
        parts = action["split"]
        amounts = money.split_by_pct(amt, [float(p.get("pct", 0)) for p in parts])
        out = []
        for p, a in zip(parts, amounts):
            out.append({"amount_cents": a, "category_id": p.get("category_id", action.get("category_id")),
                        "business": 1 if p.get("business", action.get("business")) else 0,
                        "deductible_pct": ded(p), "schedule_c_line": line(p),
                        "income_source_id": p.get("income_source_id"), "note": p.get("note", "")})
        return out
    return [{"amount_cents": amt, "category_id": action.get("category_id"),
             "business": 1 if action.get("business") else 0, "deductible_pct": ded(action),
             "schedule_c_line": line(action), "income_source_id": action.get("income_source_id"),
             "note": action.get("note", "")}]


def _load_rules(conn):
    out = []
    for r in db.rows(conn, "SELECT * FROM rules WHERE active = 1 ORDER BY priority, id"):
        try:
            out.append({"id": r["id"], "stop": r["stop"], "match": json.loads(r["match_json"]),
                        "action": json.loads(r["action_json"])})
        except ValueError:
            continue
    return out


def _txn(conn, tid):
    return db.row(conn, "SELECT * FROM v_transactions WHERE id = ?", (tid,))


def apply_to(conn, rule, t, force=False):
    """Apply one rule's action to one transaction. -> True if anything changed."""
    action = rule["action"]
    changed = False
    set_by = "rule:%d" % rule["id"]
    wants_split = any(k in action for k in ("category_id", "business", "schedule_c_line", "deductible_pct",
                                             "income_source_id", "split", "note"))
    if wants_split:
        if db.set_splits(conn, t["id"], splits_for(action, t, conn), set_by, keep_user=not force):
            changed = True
        else:
            # A person already decided this row. The rest of the action
            # (reviewed flag, tags) is theirs to make too - leave it alone.
            return False
    if action.get("payee_id") and t.get("payee_id") != action["payee_id"]:
        conn.execute("UPDATE transactions SET payee_id = ? WHERE id = ?", (action["payee_id"], t["id"]))
        changed = True
    for name in action.get("tags") or []:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tag_id = db.scalar(conn, "SELECT id FROM tags WHERE name = ?", (name,))
        conn.execute("INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)", (t["id"], tag_id))
        changed = True
    if action.get("mark_reviewed") and t["status"] == "new":
        conn.execute("UPDATE transactions SET status = 'reviewed', updated_at = ? WHERE id = ?",
                     (config.iso_now(), t["id"]))
        changed = True
    if changed:
        conn.execute("UPDATE rules SET hits = hits + 1, last_hit_at = ? WHERE id = ?", (config.iso_now(), rule["id"]))
    return changed


def apply_rules(conn, txn_ids, rule_ids=None, force=False):
    """Run the rule set over these transactions. -> number of transactions changed."""
    rules = _load_rules(conn)
    if rule_ids is not None:
        rules = [r for r in rules if r["id"] in set(rule_ids)]
    n = 0
    for tid in txn_ids:
        t = _txn(conn, tid)
        if not t:
            continue
        for rule in rules:
            if not matches(rule["match"], t):
                continue
            if apply_to(conn, rule, t, force=force):
                n += 1
            if rule["stop"]:
                break
    return n


def preview(conn, match, limit=200):
    rows = db.rows(conn, "SELECT * FROM v_transactions ORDER BY date DESC LIMIT 5000")
    return [r for r in rows if matches(match, r)][:limit]


def suggest_from_transaction(conn, tid):
    """Prefill a rule from a row: contains-match on the payee's normalized name,
    action = the row's current first split."""
    t = _txn(conn, tid)
    if not t:
        return None
    import payees as P
    # The first two tokens of the normalized text: "blue bottle san francisco"
    # -> "blue bottle", so the rule also catches the Oakland store.
    key = " ".join(P.normalize(t.get("payee_raw") or t.get("description_raw") or t.get("payee_name") or "").split()[:2])
    s = db.row(conn, "SELECT * FROM splits WHERE transaction_id = ? ORDER BY id LIMIT 1", (tid,))
    action = {"payee_id": t.get("payee_id")}
    if s:
        for k in ("category_id", "business", "schedule_c_line", "deductible_pct", "income_source_id"):
            if s[k] not in (None, 0, "") or k == "business":
                action[k] = s[k]
    return {"name": "%s" % key, "match_json": {"payee_contains": key, "sign": "out" if t["amount_cents"] < 0 else "in"},
            "action_json": action}
