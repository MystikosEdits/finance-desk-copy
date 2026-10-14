"""Positions, lots and realized gains from a list of trades. Pure.

Trades (per account, per instrument, in date order):
  buy          qty in, cost = qty x price + fee
  sell         qty out; lots consumed FIFO unless sell_allocations say which; realized gain
  transfer_in  qty in with a carried basis (cost_basis_cents) or qty x price
  transfer_out qty out FIFO, no gain (the basis leaves with the shares)
  split        qty_e8 = shares ADDED (2:1 on 10 shares -> +10); every open lot scales, basis unchanged
  dividend / interest / fee   cash only; tracked as income/cost, no position change

Quantities and prices are 1e8-scaled integers; money is cents.
"""

from datetime import date

import money

LONG_TERM_DAYS = 366


def _lot(trade, qty_e8, cost_cents):
    return {"trade_id": trade["id"], "date": trade["date"], "qty_e8": qty_e8, "cost_cents": cost_cents,
            "unit_cost_e8": int(cost_cents * money.E8 / qty_e8 * 100 // 100) if qty_e8 else 0}


def _consume(lots, qty_e8, picks=None):
    """Take qty_e8 out of lots (FIFO, or the specific picks {buy_trade_id: qty_e8}).
    -> (consumed [(lot_ref, qty, cost)], remaining qty not covered)."""
    out = []
    need = qty_e8
    order = lots
    if picks:
        order = [l for l in lots if l["trade_id"] in picks] + [l for l in lots if l["trade_id"] not in picks]
    for lot in order:
        if need <= 0:
            break
        if lot["qty_e8"] <= 0:
            continue
        take = min(lot["qty_e8"], picks.get(lot["trade_id"], need) if picks and lot["trade_id"] in picks else need)
        take = min(take, need)
        cost = int(round(lot["cost_cents"] * take / lot["qty_e8"])) if lot["qty_e8"] else 0
        lot["qty_e8"] -= take
        lot["cost_cents"] -= cost
        out.append((lot, take, cost))
        need -= take
    return out, need


def compute(trades, allocations=None):
    """trades: dicts with id, account_id, instrument_id, date, kind, qty_e8, price_e8, fee_cents,
    total_cents, cost_basis_cents. allocations: {sell_trade_id: {buy_trade_id: qty_e8}}.
    -> {"positions": [...], "realized": [...], "income": [...], "warnings": [...]}"""
    allocations = allocations or {}
    by_key = {}
    realized, income, warnings = [], [], []
    for t in sorted(trades, key=lambda x: (x["date"], x["id"])):
        key = (t["account_id"], t["instrument_id"])
        pos = by_key.setdefault(key, {"account_id": t["account_id"], "instrument_id": t["instrument_id"], "lots": []})
        k = t["kind"]
        if k == "buy":
            cost = money.mul_e8_to_cents(t["qty_e8"], t["price_e8"]) + (t.get("fee_cents") or 0)
            pos["lots"].append(_lot(t, t["qty_e8"], cost))
        elif k == "transfer_in":
            cost = t["cost_basis_cents"] if t.get("cost_basis_cents") is not None else money.mul_e8_to_cents(t["qty_e8"], t["price_e8"])
            pos["lots"].append(_lot(t, t["qty_e8"], cost))
        elif k in ("sell", "transfer_out"):
            consumed, short = _consume(pos["lots"], t["qty_e8"], allocations.get(t["id"]))
            if short > 0:
                warnings.append({"trade_id": t["id"], "msg": "sold %s more than held" % money.e8_str(short)})
            if k == "sell":
                proceeds = money.mul_e8_to_cents(t["qty_e8"], t["price_e8"]) - (t.get("fee_cents") or 0)
                cost = sum(c for _, _, c in consumed)
                sold_qty = sum(q for _, q, _ in consumed)
                # long-term if every consumed lot was held over a year
                lt = all((date.fromisoformat(t["date"]) - date.fromisoformat(l["date"])).days >= LONG_TERM_DAYS for l, _, _ in consumed) if consumed else False
                realized.append({"sell_trade_id": t["id"], "date": t["date"], "account_id": t["account_id"], "instrument_id": t["instrument_id"],
                                 "qty_e8": sold_qty, "proceeds_cents": proceeds, "cost_cents": cost, "gain_cents": proceeds - cost,
                                 "long_term": lt, "lots": [{"buy_trade_id": l["trade_id"], "qty_e8": q, "cost_cents": c} for l, q, c in consumed]})
            pos["lots"] = [l for l in pos["lots"] if l["qty_e8"] > 0]
        elif k == "split":
            held = sum(l["qty_e8"] for l in pos["lots"])
            if held > 0 and t["qty_e8"]:
                factor = (held + t["qty_e8"]) / held
                for l in pos["lots"]:
                    l["qty_e8"] = int(round(l["qty_e8"] * factor))
        elif k in ("dividend", "interest"):
            income.append({"trade_id": t["id"], "date": t["date"], "account_id": t["account_id"], "instrument_id": t["instrument_id"],
                           "kind": k, "cents": t["total_cents"]})
        elif k == "fee":
            income.append({"trade_id": t["id"], "date": t["date"], "account_id": t["account_id"], "instrument_id": t["instrument_id"],
                           "kind": k, "cents": t["total_cents"]})
    positions = []
    for pos in by_key.values():
        qty = sum(l["qty_e8"] for l in pos["lots"])
        cost = sum(l["cost_cents"] for l in pos["lots"])
        positions.append({"account_id": pos["account_id"], "instrument_id": pos["instrument_id"], "qty_e8": qty, "cost_cents": cost,
                          "lots": [dict(l) for l in pos["lots"]]})
    return {"positions": positions, "realized": realized, "income": income, "warnings": warnings}
