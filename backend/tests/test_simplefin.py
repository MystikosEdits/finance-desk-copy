import json
import os

import pytest

import simplefin

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, "fixtures", "simplefin", name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_normalize_epoch_pending_and_duplicate_ids():
    accounts, errs = simplefin.normalize(load("day1"))
    assert errs == []
    chk = accounts[0]
    assert chk["simplefin_id"] == "ACT-CHK-1" and chk["institution"] == "Chase"
    assert chk["balance_cents"] == 245012 and chk["available_cents"] == 240012
    ids = [t["external_id"] for t in chk["transactions"]]
    assert ids == ["TXN-1", "TXN-2", "PEND-9", "PEND-HOLD"]  # duplicate TXN-1 collapsed
    pend = chk["transactions"][2]
    assert pend["pending"] == 1 and pend["posted_date"] is None and pend["amount_cents"] == -5500
    # dates are the LOCAL date of the epoch, whatever zone the box is in
    from datetime import datetime
    assert pend["date"] == datetime.fromtimestamp(1788739200).date().isoformat()  # transacted_at
    assert chk["transactions"][0]["posted_date"] == datetime.fromtimestamp(1788652800).date().isoformat()


def test_normalize_errlist():
    _, errs = simplefin.normalize(load("day2"))
    assert errs == ["con.auth: Wells Fargo needs re-authentication"]


def test_split_access_url_and_redact():
    base, u, p = simplefin.split_access_url("https://abc123:s3cret@bridge.simplefin.org/simplefin")
    assert base == "https://bridge.simplefin.org/simplefin" and u == "abc123" and p == "s3cret"
    r = simplefin.redact("https://abc123:s3cret@bridge.simplefin.org/simplefin")
    assert "s3cret" not in r and "bridge.simplefin.org" in r
    with pytest.raises(simplefin.SimpleFinError):
        simplefin.split_access_url("https://bridge.simplefin.org/simplefin")
