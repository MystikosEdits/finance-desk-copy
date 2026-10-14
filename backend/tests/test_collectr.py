import pytest
from fastapi.testclient import TestClient

import config
from importers import collectr as C

H = {"X-Requested-With": "FinanceDesk"}

EXPORT = b"""Item Name,Set,Card Number,Condition,Quantity,Market Price,Total Value,Purchase Price
Charizard ex,Obsidian Flames,125/197,Near Mint,2,"$45.00","$90.00","$30.00"
Pikachu VMAX,Vivid Voltage,044/185,Lightly Played,1,$12.50,$12.50,
Umbreon VMAX (Alt Art),Evolving Skies,215/203,Near Mint,1,"$1,250.00","$1,250.00","$800.00"
"""

MINIMAL = b"""Name,Market Value,Qty
Booster Box,120.00,3
"""


def test_parse_full_export():
    sn = C.sniff(EXPORT)
    cm = sn["guess"]["column_map"]
    assert cm["name"] == "Item Name" and cm["set"] == "Set" and cm["number"] == "Card Number" and cm["quantity"] == "Quantity"
    assert cm["unit_value"] == "Market Price" and cm["total_value"] == "Total Value" and cm["cost"] == "Purchase Price"
    rows, errs = C.parse(EXPORT, sn["guess"])
    assert errs == [] and len(rows) == 3
    assert rows[0]["value_cents"] == 9000 and rows[0]["unit_value_cents"] == 4500 and rows[0]["cost_cents"] == 3000
    assert rows[1]["cost_cents"] is None and rows[2]["value_cents"] == 125000


def test_parse_minimal_export_derives_total():
    sn = C.sniff(MINIMAL)
    rows, errs = C.parse(MINIMAL, sn["guess"])
    assert errs == [] and rows[0]["quantity"] == 3 and rows[0]["value_cents"] == 36000


@pytest.fixture
def c(fresh_db, monkeypatch):
    monkeypatch.setitem(config.CFG, "auth_enabled", False)
    import app as appmod
    with TestClient(appmod.app) as client:
        yield client


def test_import_replaces_snapshot_and_sets_value(c):
    a = c.post("/api/collectr/account", json={"name": "Pokemon"}, headers=H).json()
    assert a["kind"] == "asset" and a["institution"] == "Collectr"
    pv = c.post("/api/imports/collectr/preview", files={"file": ("c.csv", EXPORT, "text/csv")}, headers=H).json()
    assert pv["total"] == 3 and pv["total_value_cents"] == 135250
    import json
    r = c.post("/api/imports/collectr", files={"file": ("c.csv", EXPORT, "text/csv")}, data={"account_id": str(a["id"]), "profile": json.dumps(pv["profile"])}, headers=H).json()
    assert r["items"] == 3 and r["replaced"] == 0
    lst = c.get("/api/collectibles").json()
    assert lst["total_value_cents"] == 135250 and lst["items"] == 4 and lst["accounts"][0]["items"] == 3
    acct = next(x for x in c.get("/api/accounts").json() if x["id"] == a["id"])
    assert acct["balance_cents"] == 135250
    assert c.get("/api/networth/now").json()["assets_cents"] == 135250
    h = c.get("/api/holdings").json()
    assert h["total_value_cents"] == 135250 and h["collectibles_cents"] == 135250 and h["securities_value_cents"] == 0
    assert h["by_kind"] == {"collectibles": 135250} and h["unrealized_cents"] == 0
    r = c.post("/api/imports/collectr", files={"file": ("c.csv", MINIMAL, "text/csv")}, data={"account_id": str(a["id"]), "profile": json.dumps(C.sniff(MINIMAL)["guess"])}, headers=H).json()
    assert r["replaced"] == 3 and r["items"] == 1
    assert c.get("/api/collectibles").json()["total_value_cents"] == 36000
