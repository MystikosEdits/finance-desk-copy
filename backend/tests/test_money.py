import pytest

import money


@pytest.mark.parametrize("text,cents", [
    ("-12.30", -1230), ("12.30", 1230), ("$1,234.56", 123456), ("(12.30)", -1230),
    ("", 0), (None, 0), ("+5", 500), ("0.005", 0), ("0.015", 2), ("0.025", 2), ("-0.005", 0),
    ("  7 ", 700), ("-$0.99", -99),
])
def test_parse(text, cents):
    assert money.parse(text) == cents


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        money.parse("twelve")


def test_fmt():
    assert money.fmt(-1230) == "-$12.30"
    assert money.fmt(123456) == "$1,234.56"
    assert money.fmt(5) == "$0.05"


def test_e8():
    assert money.to_e8("0.00234") == 234000
    assert money.e8_str(234000) == "0.00234"
    assert money.e8_str(100000000) == "1"
    assert money.mul_e8_to_cents(money.to_e8("0.5"), money.to_e8("60000.10")) == 3000005


def test_split_by_pct_remainder_on_last():
    assert money.split_by_pct(1001, [40, 60]) == [400, 601]
    assert money.split_by_pct(-1001, [40, 60]) == [-400, -601]
    assert sum(money.split_by_pct(999, [33, 33, 34])) == 999
    assert money.split_by_pct(500, [100]) == [500]
