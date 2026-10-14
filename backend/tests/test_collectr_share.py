import pytest

import collectr_share as CS
import db

PAYLOAD = {
    "username": "owner", "total_cards": 42, "total_sealed": 3, "total_graded": 5,
    "portfolio_value": [{"price": 1234.56, "date": "2026-09-07"}],
    "products": [
        {"product_name": "Charizard ex", "catalog_group": "Obsidian Flames", "card_number": "125/197", "catalog_category_name": "Pokemon",
         "image_url": "https://x/1.png", "latest_price": 45.0, "quantity": 2},
        {"product_name": "Umbreon VMAX", "catalog_group": "Evolving Skies", "card_number": "215/203", "catalog_category_name": "Pokemon",
         "image_url": "https://x/2.png", "latest_price": "1,100.00"},
        {"product_name": "Booster Box", "catalog_group": "151", "catalog_category_name": "Sealed", "latest_price": 120, "count": 1},
    ],
}


@pytest.mark.parametrize("url,sid", [
    ("https://www.getcollectr.com/showcase/portfolio?id=abc123", "abc123"),
    ("https://www.getcollectr.com/showcase/?id=ABC-9_z", "ABC-9_z"),
    ("getcollectr.com/showcase/portfolio/xyz789", "xyz789"),
    ("https://app.getcollectr.com/showcase/profile/@yourhandle", "@yourhandle"),
    ("https://app.getcollectr.com/showcase/profile/yourhandle", "@yourhandle"),
    ("@yourhandle", "@yourhandle"),
    ("yourhandle", "@yourhandle"),
])
def test_share_id(url, sid):
    assert CS.share_id(url) == sid


def test_share_id_rejects_garbage():
    with pytest.raises(CS.CollectrError):
        CS.share_id("")
    with pytest.raises(CS.CollectrError):
        CS.validate_id("has spaces here")


def test_normalize_reads_the_showcase_shape():
    n = CS.normalize(PAYLOAD)
    assert n["username"] == "owner" and n["portfolio_value_cents"] == 123456
    assert n["total_cards"] == 42 and n["total_sealed"] == 3 and n["total_graded"] == 5
    assert [i["quantity"] for i in n["items"]] == [2, 1, 1]
    assert [i["value_cents"] for i in n["items"]] == [9000, 110000, 12000]
    assert n["items"][0]["set_name"] == "Obsidian Flames" and n["items"][0]["number"] == "125/197"
    assert "latest_price" in n["product_keys"] and "portfolio_value" in n["top_level_keys"]
    assert CS.normalize({"user": "YourHandle", "handle": "yourhandle", "products": []})["username"] == "yourhandle"


def test_blocked_and_empty_feeds_skip_quietly(conn, clock, monkeypatch):
    with db.write() as w:
        w.execute("INSERT INTO settings (key, value) VALUES ('collectr_share_url', '\"https://app.getcollectr.com/showcase/profile/@yourhandle\"')")

    def blocked(sid):
        raise CS.Blocked("403")
    monkeypatch.setattr(CS, "fetch", blocked)
    assert "skipped" in CS.run_sync()
    monkeypatch.setattr(CS, "fetch", lambda sid: {"showcaseEnabled": False, "products": [], "portfolio_value": [], "total_cards": "0"})
    assert "skipped" in CS.run_sync()
    assert db.scalar(conn, "SELECT COUNT(*) FROM accounts WHERE institution = 'Collectr'") == 0
    assert CS.validate_id("@yourhandle") == "@yourhandle"
    bm = CS.bookmarklet("@yourhandle", "http://localhost:8020/")
    assert bm.startswith("javascript:") and '"@yourhandle"' in bm and '"http://localhost:8020"' in bm and "/collectr-drop#" in bm


def test_ingest_paste(conn, clock):
    import json
    with pytest.raises(CS.CollectrError):
        CS.ingest("not json")
    with pytest.raises(CS.CollectrError):
        CS.ingest(json.dumps({"showcaseEnabled": False, "products": [], "portfolio_value": []}))
    aid, total, norm = CS.ingest(json.dumps(dict(PAYLOAD, showcaseEnabled=True)))
    assert total == 123456 and len(norm["items"]) == 3
    assert db.scalar(conn, "SELECT manual_value_cents FROM accounts WHERE id = ?", (aid,)) == 123456


def test_apply_and_sync(conn, clock, monkeypatch):
    monkeypatch.setattr(CS, "fetch", lambda sid: PAYLOAD)
    with db.write() as w:
        w.execute("INSERT INTO settings (key, value) VALUES ('collectr_share_url', '\"https://www.getcollectr.com/showcase/portfolio?id=abc123\"')")
    res = CS.run_sync()
    assert res["items"] == 3 and res["value_cents"] == 123456
    a = db.row(conn, "SELECT * FROM accounts WHERE institution = 'Collectr'")
    assert a["kind"] == "asset" and a["manual_value_cents"] == 123456 and a["name"] == "Collectr · owner"
    assert db.scalar(conn, "SELECT balance_cents FROM account_balances WHERE account_id = ? AND date = '2026-09-07'", (a["id"],)) == 123456
    assert db.scalar(conn, "SELECT COUNT(*) FROM collectibles WHERE account_id = ?", (a["id"],)) == 3
    # a second run replaces, does not accumulate, and keeps the same account
    CS.run_sync()
    assert db.scalar(conn, "SELECT COUNT(*) FROM collectibles") == 3
    assert db.scalar(conn, "SELECT COUNT(*) FROM accounts WHERE institution = 'Collectr'") == 1
    import networth as N
    assert N.compute(conn)["assets_cents"] == 123456


PRIVATE = {
    "source": "private", "handle": "@yourhandle",
    "value": [{"price": "4596.4859", "insertion_date": "2026-09-08T00:00:00.000Z"}],
    "summary": {"total_cards": "194", "total_graded": "1", "total_sealed": "0", "total_value": "4596.48590000"},
    "collections": [{"name": "Main", "totalCards": "193", "totalCount": "194", "totalGraded": "1", "totalSealed": "0"}],
    "history": [{"price": "4596.49", "insertion_date": "2026-09-08T00:00:00.000Z"}, {"price": "4582.21", "insertion_date": "2026-09-05"}],
    "products": [
        {"product_name": "Charizard ", "catalog_group": "Base Set (Unlimited)", "card_number": "4", "card_condition": "NM", "grade_id": "6",
         "market_price": "420.7347", "quantity": "1", "price_override": None, "product_sub_type": "Holofoil", "is_card": True},
        {"product_name": "Pikachu", "catalog_group": "Jungle", "card_number": "60", "card_condition": "LP", "grade_id": None,
         "market_price": "3.10", "quantity": "3", "price_override": "5.00", "product_sub_type": ""},
    ],
}


def test_normalize_private_portfolio_shape():
    n = CS.normalize(PRIVATE)
    assert n["username"] == "yourhandle" and n["portfolio_value_cents"] == 459649
    assert n["total_cards"] == 194 and n["total_graded"] == 1 and n["collections"] == ["Main"]
    assert n["items"][0]["name"] == "Charizard" and n["items"][0]["unit_value_cents"] == 42073 and n["items"][0]["condition"] == "NM Holofoil"
    assert n["items"][1]["unit_value_cents"] == 500 and n["items"][1]["value_cents"] == 1500   # override wins, x3
    assert n["items_value_cents"] == 42073 + 1500
    assert n["history"] == [{"date": "2026-09-08", "cents": 459649}, {"date": "2026-09-05", "cents": 458221}]


def test_ingest_private_writes_history(conn, clock):
    import json
    aid, total, norm = CS.ingest(json.dumps(PRIVATE))
    assert total == 459649 and len(norm["items"]) == 2
    assert db.scalar(conn, "SELECT name FROM accounts WHERE id = ?", (aid,)) == "Collectr · yourhandle"
    rows = db.rows(conn, "SELECT date, balance_cents FROM account_balances WHERE account_id = ? ORDER BY date", (aid,))
    assert [(r["date"], r["balance_cents"]) for r in rows] == [("2026-09-05", 458221), ("2026-09-07", 459649)]
    with pytest.raises(CS.CollectrError):
        CS.ingest(json.dumps({"source": "private", "value": None, "products": [], "collections": []}))
    bm = CS.bookmarklet("@yourhandle", "http://localhost:8020")
    assert "collectrToken" in bm and "/collections/" in bm and "collections/summary" in bm
    payload_literal = bm.split("first={")[1].split("}")[0]
    assert "tok" not in payload_literal and "token" not in payload_literal
