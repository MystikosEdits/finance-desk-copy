from importers import brokers


FIDELITY = b"""Run Date,Action,Symbol,Description,Quantity,Price ($),Commission ($),Fees ($),Amount ($)
09/02/2026,YOU BOUGHT VANGUARD TOTAL STOCK,VTI,VANGUARD,10,280.50,0,0,-2805.00
09/03/2026,DIVIDEND RECEIVED,VTI,VANGUARD,,,,,12.34
09/04/2026,YOU SOLD APPLE,AAPL,APPLE INC,-2,230.00,0,0.02,459.98
"""

COINBASE = b"""Timestamp,Transaction Type,Asset,Quantity Transacted,Spot Price at Transaction,Subtotal,Total (inclusive of fees and/or spread),Fees and/or Spread,Notes
2026-09-01 10:00:00,Buy,BTC,0.00234,60000.10,140.40,142.40,2.00,Bought
2026-09-05 10:00:00,Send,BTC,0.001,,,,,Sent to cold wallet
"""


def test_fidelity_shape():
    sn = brokers.sniff(FIDELITY)
    cm = sn["guess"]["column_map"]
    assert cm["date"] == "Run Date" and cm["action"] == "Action" and cm["symbol"] == "Symbol" and cm["quantity"] == "Quantity"
    rows, errs = brokers.parse(FIDELITY, sn["guess"], account_id=1)
    assert errs == [] and [r["kind"] for r in rows] == ["buy", "dividend", "sell"]
    assert rows[0]["total_cents"] == -280500 and rows[1]["total_cents"] == 1234 and rows[2]["total_cents"] == 45998
    assert rows[2]["qty_e8"] == 200000000


def test_coinbase_shape():
    sn = brokers.sniff(COINBASE)
    rows, errs = brokers.parse(COINBASE, sn["guess"], account_id=2)
    assert errs == [] and rows[0]["kind"] == "buy" and rows[0]["qty_e8"] == 234000 and rows[0]["symbol"] == "BTC"
    assert rows[0]["total_cents"] == -14240 and rows[0]["fee_cents"] == 200
    assert rows[1]["kind"] == "transfer_out"
    assert rows[0]["external_id"] != rows[1]["external_id"]
