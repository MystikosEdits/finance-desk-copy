import pytest

import db
import payees as P


@pytest.mark.parametrize("raw,norm", [
    ("SQ *BLUE BOTTLE 0412 SAN FRANCISCO CA", "blue bottle san francisco"),
    ("AMZN Mktp US*2K4L98HJ2 Amzn.com/bill WA", "amazon"),
    ("PAYPAL *ADOBE INC 402-935-7733 CA", "adobe"),
    ("TST* THE DINER SF", "the diner"),
    ("TST* THE DINER SAN FRANCISCO CA", "the diner san francisco"),
    ("Google *YouTubePremium", "youtube premium"),
    ("CHEVRON 0091 PRE-AUTH", "gas station"),
    ("", ""),
])
def test_normalize(raw, norm):
    assert P.normalize(raw) == norm


def test_similarity():
    assert P.similarity("TST* THE DINER SF", "THE DINER SAN FRANCISCO") > 0.4
    assert P.similarity("SPOTIFY USA", "THE DINER") == 0.0


def test_resolve_is_stable_across_sources(conn):
    with db.write() as w:
        a = P.resolve(w, "SQ *BLUE BOTTLE 0412 SAN FRANCISCO CA")
        b = P.resolve(w, "sq *blue bottle 0999 san francisco ca")
        c = P.resolve(w, "", "SQ *BLUE BOTTLE 0412 SAN FRANCISCO CA")
        d = P.resolve(w, "NETFLIX.COM")
    assert a == b == c and d != a
    assert db.scalar(conn, "SELECT name FROM payees WHERE id = ?", (a,)) == "Blue Bottle San Francisco"
