import os

from importers import csvmap

HERE = os.path.dirname(os.path.abspath(__file__))


def raw(name):
    with open(os.path.join(HERE, "fixtures", "csv", name), "rb") as fh:
        return fh.read()


def test_sniff_chase_card_signed_amount():
    sn = csvmap.sniff(raw("chase_card.csv"))
    g = sn["guess"]
    assert g["column_map"] == {"date": "Transaction Date", "amount": "Amount", "payee": "Description", "memo": "Memo"}
    assert g["date_format"] == "%m/%d/%Y" and g["negate"] == 0


def test_sniff_capital_one_debit_credit_with_bom():
    sn = csvmap.sniff(raw("capone_debit_credit.csv"))
    g = sn["guess"]
    assert sn["headers"][0] == "Transaction Date"  # BOM stripped
    assert g["column_map"]["debit"] == "Debit" and g["column_map"]["credit"] == "Credit"
    assert g["date_format"] == "%Y-%m-%d"
    rows, errs = csvmap.parse(raw("capone_debit_credit.csv"), g, account_id=1)
    assert errs == [] and [r["amount_cents"] for r in rows] == [-900, 25000]


def test_sniff_amex_positive_charges_sets_negate():
    sn = csvmap.sniff(raw("amex_positive_charges.csv"))
    assert sn["guess"]["negate"] == 1
    rows, _ = csvmap.parse(raw("amex_positive_charges.csv"), sn["guess"], account_id=1)
    assert [r["amount_cents"] for r in rows] == [-29900, -4900, -3250, -2000, 40000]


def test_parse_quoted_commas_and_identical_rows_get_distinct_ids():
    sn = csvmap.sniff(raw("chase_card.csv"))
    rows, errs = csvmap.parse(raw("chase_card.csv"), sn["guess"], account_id=5)
    assert errs == [] and len(rows) == 4
    assert rows[1]["payee_raw"] == "SQ *BLUE BOTTLE, 0412"
    assert rows[1]["external_id"] != rows[2]["external_id"]
    # same file again -> same ids (deterministic)
    rows2, _ = csvmap.parse(raw("chase_card.csv"), sn["guess"], account_id=5)
    assert [r["external_id"] for r in rows] == [r["external_id"] for r in rows2]
    # a different account -> different ids
    rows3, _ = csvmap.parse(raw("chase_card.csv"), sn["guess"], account_id=6)
    assert rows3[0]["external_id"] != rows[0]["external_id"]


def test_fuzzy_duplicate_against_simplefin_row():
    existing = [{"date": "2026-09-03", "amount_cents": -1999, "payee_raw": "", "description_raw": "ADOBE *CREATIVE CLOUD 408-536-6000 CA", "external_id": "C-2"}]
    row = {"date": "2026-09-02", "amount_cents": -1999, "payee_raw": "ADOBE *CREATIVE CLOUD", "memo_raw": ""}
    assert csvmap.find_fuzzy_duplicate(existing, row)["external_id"] == "C-2"
    row2 = dict(row, amount_cents=-2000)
    assert csvmap.find_fuzzy_duplicate(existing, row2) is None


def test_bad_date_is_an_error_not_a_zero():
    data = b"Date,Description,Amount\nnot-a-date,X,1.00\n09/02/2026,Y,2.00\n"
    sn = csvmap.sniff(data)
    rows, errs = csvmap.parse(data, sn["guess"], account_id=1)
    assert len(rows) == 1 and len(errs) == 1 and "bad date" in errs[0]
