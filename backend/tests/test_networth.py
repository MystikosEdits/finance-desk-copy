import db
import money
import networth as N
from conftest import make_account


def test_holdings_override_balance_and_snapshot(conn, clock):
    chk = make_account(conn, "Checking")
    card = make_account(conn, "Card", kind="credit")
    brk = make_account(conn, "Brokerage", kind="brokerage")
    car = make_account(conn, "Car", kind="asset", manual_value_cents=1500000)
    with db.write() as w:
        for acct, bal in ((chk, 250000), (card, -40000), (brk, 999999)):
            w.execute("INSERT INTO account_balances (account_id, date, balance_cents, source) VALUES (?, '2026-09-06', ?, 'simplefin')", (acct, bal))
        inst = w.execute("INSERT INTO instruments (symbol, kind) VALUES ('VTI', 'etf')").lastrowid
        w.execute("INSERT INTO trades (account_id, instrument_id, date, kind, qty_e8, price_e8, total_cents) VALUES (?, ?, '2026-01-01', 'buy', ?, ?, -30000)",
                  (brk, inst, money.to_e8("10"), money.to_e8("30")))
        w.execute("INSERT INTO prices (instrument_id, date, price_e8, source, fetched_at) VALUES (?, '2026-09-07', ?, 'manual', 'x')", (inst, money.to_e8("35")))
    out = N.compute(conn)
    acc = {a["name"]: a for a in out["accounts"]}
    assert acc["Brokerage"]["value_cents"] == 35000 and acc["Brokerage"]["source"] == "holdings"   # not the bank's 9999.99
    assert acc["Checking"]["value_cents"] == 250000 and acc["Car"]["value_cents"] == 1500000
    assert acc["Card"]["liability"] and out["liabilities_cents"] == -40000
    assert out["assets_cents"] == 250000 + 35000 + 1500000
    assert out["net_cents"] == out["assets_cents"] - 40000
    assert out["stale_prices"] == []
    with db.write() as w:
        N.snapshot(w)
    h = N.history(conn)
    assert len(h) == 1 and h[0]["net_cents"] == out["net_cents"] and h[0]["date"] == "2026-09-07"


def test_stale_price_is_flagged(conn, clock):
    brk = make_account(conn, "Brokerage", kind="brokerage")
    with db.write() as w:
        inst = w.execute("INSERT INTO instruments (symbol, kind) VALUES ('AAPL', 'stock')").lastrowid
        w.execute("INSERT INTO trades (account_id, instrument_id, date, kind, qty_e8, price_e8, total_cents) VALUES (?, ?, '2026-01-01', 'buy', ?, ?, -1)",
                  (brk, inst, money.to_e8("1"), money.to_e8("1")))
        w.execute("INSERT INTO prices (instrument_id, date, price_e8, source, fetched_at) VALUES (?, '2026-08-20', ?, 'yahoo', 'x')", (inst, money.to_e8("200")))
    out = N.compute(conn)
    assert out["stale_prices"] == ["AAPL"] and out["assets_cents"] == 20000
