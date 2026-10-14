import holdings as H
import money


def t(i, kind, date, qty, price="0", fee=0, total=0, basis=None, acct=1, inst=1):
    return {"id": i, "account_id": acct, "instrument_id": inst, "date": date, "kind": kind,
            "qty_e8": money.to_e8(qty), "price_e8": money.to_e8(price), "fee_cents": fee, "total_cents": total, "cost_basis_cents": basis}


def test_fifo_partial_sell_and_long_term():
    res = H.compute([
        t(1, "buy", "2024-01-10", "10", "100", fee=100),     # cost 1000.00 + 1.00
        t(2, "buy", "2025-06-10", "10", "200"),              # cost 2000.00, held < 1 year at the sale
        t(3, "sell", "2026-01-15", "15", "300", fee=50),     # proceeds 4500 - .50
    ])
    p = res["positions"][0]
    assert p["qty_e8"] == money.to_e8("5") and p["cost_cents"] == 100000
    r = res["realized"][0]
    assert r["qty_e8"] == money.to_e8("15")
    assert r["cost_cents"] == 100100 + 100000            # all of lot 1, half of lot 2
    assert r["proceeds_cents"] == 449950 and r["gain_cents"] == 449950 - 200100
    assert r["long_term"] is False                       # lot 2 held < 1 year
    assert res["warnings"] == []


def test_specific_lot_allocation():
    trades = [t(1, "buy", "2024-01-10", "10", "100"), t(2, "buy", "2025-06-10", "10", "200"), t(3, "sell", "2026-01-15", "10", "300")]
    res = H.compute(trades, {3: {2: money.to_e8("10")}})
    assert res["realized"][0]["cost_cents"] == 200000 and res["realized"][0]["long_term"] is False
    res = H.compute(trades)
    assert res["realized"][0]["cost_cents"] == 100000 and res["realized"][0]["long_term"] is True


def test_split_and_transfer_in_with_carried_basis():
    res = H.compute([
        t(1, "buy", "2024-01-10", "10", "100"),
        t(2, "split", "2024-06-01", "10"),                       # 2:1 -> 20 shares, same basis
        t(3, "transfer_in", "2025-01-01", "5", "0", basis=25000),  # 5 shares carried at $250
        t(4, "sell", "2026-01-15", "22", "60"),
    ])
    p = res["positions"][0]
    assert p["qty_e8"] == money.to_e8("3") and p["cost_cents"] == 15000     # 3 of the carried shares left
    r = res["realized"][0]
    assert r["cost_cents"] == 100000 + 10000 and r["proceeds_cents"] == 132000


def test_overselling_warns_and_income_is_tracked():
    res = H.compute([t(1, "buy", "2026-01-01", "1", "10"), t(2, "sell", "2026-02-01", "2", "10"),
                     t(3, "dividend", "2026-03-01", "0", total=1234), t(4, "fee", "2026-03-02", "0", total=-99)])
    assert res["warnings"][0]["trade_id"] == 2
    assert [i["cents"] for i in res["income"]] == [1234, -99]


def test_crypto_fractions():
    res = H.compute([t(1, "buy", "2026-01-01", "0.00234", "60000.10"), t(2, "sell", "2026-06-01", "0.001", "70000")])
    assert res["positions"][0]["qty_e8"] == money.to_e8("0.00134")
    assert res["realized"][0]["proceeds_cents"] == 7000
