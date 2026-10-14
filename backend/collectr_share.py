"""Collectr via its portfolio share link. No API key, no CSV.

HOW THE SHARE PAGE WORKS (read out of the showcase app's bundle, 2026-09-07)
  The link Collectr gives you opens www.getcollectr.com/showcase/... with an
  `id` query parameter. The page then GETs
    https://djk9wkkysj.execute-api.us-east-1.amazonaws.com/data/showcase/<id>?limit=200
  and renders: portfolio_value[0].price ("Estimated Portfolio Value (USD)"),
  total_cards, total_sealed, total_graded, username, and a `products` list
  with product_name, catalog_category_name, catalog_group, card_number,
  image_url, latest_price. Anything beyond that (quantity key, pagination) is
  read defensively and reported by `connect`, so it can be corrected against
  a real link rather than guessed.

The portfolio_value figure is Collectr's own total and is what the account
is valued at; the product rows are the detail underneath it.
"""

import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx

import config
import db
import log
import money

logger = log.get("collectr")
ENDPOINT = "https://djk9wkkysj.execute-api.us-east-1.amazonaws.com/data/showcase/"   # old ?id= share links
ENDPOINT_V2 = "https://api-v2.getcollectr.com/data/showcase/"                         # @handle profile links
TIMEOUT = 30.0
PAGE = 200
PAGE_V2 = 30
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinanceDesk/1.0", "Accept": "application/json",
      "Origin": "https://www.getcollectr.com", "Referer": "https://www.getcollectr.com/"}
QTY_KEYS = ("quantity", "count", "qty", "owned", "num_owned", "amount")
PRICE_KEYS = ("latest_price", "price", "market_price", "value", "estimated_value")
NAME_KEYS = ("product_name", "name", "card_name", "title")
SET_KEYS = ("set_name", "set", "catalog_group", "expansion")
NUMBER_KEYS = ("card_number", "number", "collector_number")
CAT_KEYS = ("catalog_category_name", "category", "product_type")


class CollectrError(Exception):
    pass


class Blocked(CollectrError):
    """The WAF said no. Not a bug to retry; a different path to take."""


def share_id(url):
    """What identifies the portfolio in a Collectr link.

    Two kinds exist: the profile handle (app.getcollectr.com/showcase/profile/@name,
    or just "@name" / "name") and the older opaque id (.../showcase/portfolio?id=...).
    Handles always come back with their @, because that is how the v2 API
    wants them in the path."""
    u = url.strip()
    if not u:
        raise CollectrError("paste the share link")
    if "://" not in u and "/" not in u and "?" not in u:
        return u if u.startswith("@") else "@" + u
    parts = urlsplit(u if "://" in u else "https://" + u)
    q = parse_qs(parts.query)
    for k in ("id", "portfolio", "portfolioId", "share"):
        if q.get(k):
            return q[k][0]
    seg = [x for x in parts.path.split("/") if x and x not in ("showcase", "portfolio", "share", "p")]
    if seg:
        last = seg[-1]
        if seg[0] == "profile" or last.startswith("@") or "app.getcollectr.com" in (parts.netloc or ""):
            return last if last.startswith("@") else "@" + last
        return last
    raise CollectrError("could not find a handle or id in that link")


def is_handle(sid):
    return sid.startswith("@")


def bookmarklet(handle, origin):
    """JavaScript for a bookmark, run on any app.getcollectr.com page.

    Signed in: reads the collectrToken cookie and pulls the owner's own
    portfolio - every page of /collections/{user}/products, the collections
    summary (Collectr's totals) and the overview (value history) - then opens
    the desk's drop page with the JSON in the URL fragment. The token is used
    for the fetches and is NOT part of the payload.
    Signed out: falls back to the public showcase for `handle`.
    No clipboard: browsers refuse clipboard writes once a click's gesture has
    aged past the network round trips. The frontend fetches this script from
    /api/collectr/bookmarklet so there is exactly one copy of it."""
    js = (
        "(async()=>{try{const h=%s,o=%s,A='https://api-v2.getcollectr.com';"
        "const ck=Object.fromEntries(document.cookie.split(';').map(c=>{const i=c.indexOf('=');return[c.slice(0,i).trim(),decodeURIComponent(c.slice(i+1))]}));"
        "let tok=null;try{tok=ck.collectrToken?JSON.parse(ck.collectrToken):null}catch(e){}"
        "let first=null,all=[];"
        "if(tok&&tok.token&&tok.username){const H={Authorization:tok.token,Accept:'application/json'};const u=tok.username;"
        "const g=async p=>{const r=await fetch(A+p,{headers:H,credentials:'include'});if(r.status===401)throw new Error('session expired - sign in to Collectr again');if(!r.ok)throw new Error('Collectr said '+r.status);return r.json()};"
        "const cols=((await g('/accounts/'+u+'/collections?details=true')).data)||[];"
        "const sum=(await g('/accounts/'+u+'/collections/summary?allCollections=true')).data||{};"
        "let hist=[];try{hist=((await g('/collections/'+u+'/overview?allCollections=true&days=all')).data||{}).historical_values||[]}catch(e){}"
        "let off=0,value=null;for(let i=0;i<400;i++){const j=await g('/collections/'+u+'/products?offset='+off+'&limit=30&allCollections=true&unstackedView=true&currency=USD');"
        "if(value===null)value=j.value;const p=j.data||[];all=all.concat(p);if(p.length<30)break;off+=p.length}"
        "first={source:'private',handle:h,value:value,summary:sum,collections:cols,history:hist,products:all}}"
        "else{let off=0;for(let i=0;i<80;i++){const r=await fetch(A+'/data/showcase/'+encodeURIComponent(h)+'?offset='+off+'&limit=30&unstackedView=true&username=anon',{credentials:'include',headers:{Accept:'application/json'}});"
        "if(!r.ok){alert('Collectr said '+r.status+' - sign in to app.getcollectr.com first, then click again');return}"
        "const j=await r.json();if(!first)first=j;const p=(j.products||[]);all=all.concat(p);if(p.length<30)break;off+=p.length}first.products=all}"
        "const u2=o+'/collectr-drop#'+encodeURIComponent(JSON.stringify(first));"
        "if(u2.length>1900000){alert('Too big for a link ('+all.length+' items)');return}"
        "const w=window.open(u2,'_blank');if(!w)location.href=u2}"
        "catch(e){alert('Could not read Collectr: '+e)}})();"
    ) % (json.dumps(handle), json.dumps(origin.rstrip("/")))
    return "javascript:" + js


def _first(d, keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def _money(v):
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(round(v * 100))
    try:
        return money.parse(str(v))
    except ValueError:
        return 0


def _int(v, default=1):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def fetch(sid):
    """GET every page of the showcase feed. -> the first page's dict with `products` merged across pages."""
    first = None
    products = []
    offset = 0
    page = PAGE_V2 if is_handle(sid) else PAGE
    for _ in range(80):  # never loop forever on a feed that ignores offset
        if is_handle(sid):
            url = ENDPOINT_V2 + sid
            params = {"offset": offset, "limit": page, "unstackedView": "true", "username": "anon"}
        else:
            url = ENDPOINT + sid
            params = {"limit": page}
            if offset:
                params["offset"] = offset
        with httpx.Client(timeout=TIMEOUT, headers=UA) as c:
            r = c.get(url, params=params)
        if r.status_code == 404:
            raise CollectrError("Collectr says that portfolio does not exist (404). Is sharing still turned on?")
        if r.status_code == 403:
            # AWS WAF in front of the API only admits browsers that ran its
            # challenge script. That is bot protection, and this app does
            # not try to get around it: the bookmarklet path reads the feed
            # inside your own browser session instead.
            raise Blocked("Collectr's edge blocks server-side reads (403). Use the bookmarklet from Investments > collectibles.")
        if r.status_code != 200:
            raise CollectrError("Collectr returned HTTP %d" % r.status_code)
        try:
            j = r.json()
        except ValueError:
            raise CollectrError("Collectr returned something that is not JSON")
        if isinstance(j, dict) and "body" in j and isinstance(j["body"], str):
            try:
                j = json.loads(j["body"])   # API Gateway lambda-proxy shape
            except ValueError:
                pass
        if first is None:
            first = j
        rows_page = j.get("products") if isinstance(j, dict) else None
        if not isinstance(rows_page, list):
            rows_page = j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), list) else []
        products.extend(rows_page)
        if len(rows_page) < page:
            break
        offset += len(rows_page)
    if not isinstance(first, dict):
        raise CollectrError("unexpected response shape: %s" % type(first).__name__)
    first = dict(first)
    first["products"] = products
    return first


def normalize(payload):
    if payload.get("source") == "private":
        return _normalize_private(payload)
    pv = payload.get("portfolio_value")
    if isinstance(pv, list) and pv:
        total = _money(_first(pv[0], ("price", "value")))
    elif isinstance(pv, dict):
        total = _money(_first(pv, ("price", "value")))
    else:
        total = _money(pv)
    items = []
    for p in payload.get("products") or []:
        if not isinstance(p, dict):
            continue
        name = _first(p, NAME_KEYS, "")
        if not name:
            continue
        qty = _int(_first(p, QTY_KEYS, 1), 1)
        unit = _money(_first(p, PRICE_KEYS))
        items.append({
            "name": str(name), "set_name": str(_first(p, SET_KEYS, "") or ""), "number": str(_first(p, NUMBER_KEYS, "") or ""),
            "condition": str(_first(p, CAT_KEYS, "") or ""), "quantity": max(qty, 0), "unit_value_cents": unit,
            "value_cents": unit * max(qty, 0), "cost_cents": None, "image_url": p.get("image_url"),
        })
    seen = {}
    for it in items:
        key = "|".join([it["name"], it["set_name"], it["number"], it["condition"]]).lower()
        seen[key] = seen.get(key, 0) + 1
        it["external_key"] = key if seen[key] == 1 else "%s#%d" % (key, seen[key])
    sample_keys = sorted((payload.get("products") or [{}])[0].keys()) if payload.get("products") else []
    return {
        "username": (payload.get("handle") or payload.get("username") or payload.get("user") or payload.get("nickname") or "").lstrip("@"),
        "portfolio_value_cents": total,
        "total_cards": _int(payload.get("total_cards"), 0), "total_sealed": _int(payload.get("total_sealed"), 0),
        "total_graded": _int(payload.get("total_graded"), 0),
        "items": items, "items_value_cents": sum(i["value_cents"] for i in items),
        "top_level_keys": sorted(k for k in payload.keys() if k != "products"), "product_keys": sample_keys,
    }


def _normalize_private(payload):
    """The logged-in portfolio, captured in the owner's browser by the bookmarklet
    (the token that fetched it is NOT in the payload):
      products    /collections/{user}/products  one row per owned product:
                  product_name, catalog_group, card_number, card_condition,
                  grade_id, market_price (dollars, string), quantity, price_override
      summary     /accounts/{user}/collections/summary: total_value, total_cards,
                  total_sealed, total_graded  (Collectr's own numbers)
      history     /collections/{user}/overview historical_values: [{price, insertion_date}]
    """
    items = []
    for p in payload.get("products") or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("product_name") or "").strip()
        if not name:
            continue
        qty = _int(p.get("quantity"), 1)
        override = p.get("price_override")
        unit = _money(override) if override not in (None, "", 0, "0") else _money(p.get("market_price"))
        # grade_id is set on raw cards too (it encodes the grading scale), so it
        # says nothing about whether the card is graded; the summary's
        # total_graded is the only reliable count.
        cond = " ".join(x for x in [p.get("card_condition") or "", p.get("product_sub_type") or ""] if x).strip()
        items.append({
            "name": name, "set_name": str(p.get("catalog_group") or ""), "number": str(p.get("card_number") or ""),
            "condition": cond, "quantity": max(qty, 0), "unit_value_cents": unit, "value_cents": unit * max(qty, 0),
            "cost_cents": None, "image_url": p.get("image_url"),
        })
    seen = {}
    for it in items:
        key = "|".join([it["name"], it["set_name"], it["number"], it["condition"]]).lower()
        seen[key] = seen.get(key, 0) + 1
        it["external_key"] = key if seen[key] == 1 else "%s#%d" % (key, seen[key])
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    cols = [c for c in (payload.get("collections") or []) if isinstance(c, dict)]
    items_value = sum(i["value_cents"] for i in items)
    total = _money(summary.get("total_value"))
    if not total:
        v = payload.get("value")
        if isinstance(v, list) and v and isinstance(v[0], dict):
            total = _money(v[0].get("price"))
        elif isinstance(v, (str, int, float)):
            total = _money(v)
    total = total or items_value
    history = []
    for h in payload.get("history") or []:
        if isinstance(h, dict) and h.get("price") is not None and h.get("insertion_date"):
            history.append({"date": str(h["insertion_date"])[:10], "cents": _money(h["price"])})
    sample_keys = sorted((payload.get("products") or [{}])[0].keys()) if payload.get("products") else []
    return {
        "username": (payload.get("handle") or payload.get("user") or "").lstrip("@"),
        "portfolio_value_cents": total,
        "total_cards": _int(summary.get("total_cards"), 0) or sum(_int(c.get("totalCards"), 0) for c in cols),
        "total_sealed": _int(summary.get("total_sealed"), 0) or sum(_int(c.get("totalSealed"), 0) for c in cols),
        "total_graded": _int(summary.get("total_graded"), 0) or sum(_int(c.get("totalGraded"), 0) for c in cols),
        "items": items, "items_value_cents": items_value, "history": history,
        "top_level_keys": sorted(k for k in payload.keys() if k != "products"), "product_keys": sample_keys,
        "collections": [c.get("name") for c in cols],
    }


# --------------------------------------------------------------------------
# settings + ledger
# --------------------------------------------------------------------------
def _setting(conn, key):
    v = db.scalar(conn, "SELECT value FROM settings WHERE key = ?", (key,))
    return json.loads(v) if v else None


def _put(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


def connected():
    with db.read() as conn:
        return bool(_setting(conn, "collectr_share_url"))


def _account(conn, username):
    aid = _setting(conn, "collectr_account_id")
    if aid:
        a = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (aid,))
        if a:
            return a
    a = db.row(conn, "SELECT * FROM accounts WHERE kind = 'asset' AND institution = 'Collectr' AND is_closed = 0 ORDER BY id LIMIT 1")
    if not a:
        cur = conn.execute("INSERT INTO accounts (name, kind, institution, default_business, manual_value_cents, created_at) VALUES (?, 'asset', 'Collectr', 'personal', 0, ?)",
                           ("Collectr" + (" · %s" % username if username else ""), config.iso_now()))
        a = db.row(conn, "SELECT * FROM accounts WHERE id = ?", (cur.lastrowid,))
    _put(conn, "collectr_account_id", a["id"])
    return a


def apply(conn, norm, now_iso=None, today=None):
    """Write the snapshot: replace the collection rows, set the value, log the day."""
    now_iso = now_iso or config.iso_now()
    today = today or config.today().isoformat()
    a = _account(conn, norm["username"])
    conn.execute("DELETE FROM collectibles WHERE account_id = ?", (a["id"],))
    for it in norm["items"]:
        conn.execute("""INSERT INTO collectibles (account_id, external_key, name, set_name, number, condition, quantity, unit_value_cents, value_cents, cost_cents, imported_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (a["id"], it["external_key"], it["name"], it["set_name"], it["number"], it["condition"], it["quantity"],
                      it["unit_value_cents"], it["value_cents"], it["cost_cents"], now_iso))
    # Collectr's own total wins over the sum of rows: it is what the app shows the owner.
    total = norm["portfolio_value_cents"] or norm["items_value_cents"]
    conn.execute("UPDATE accounts SET manual_value_cents = ? WHERE id = ?", (total, a["id"]))
    conn.execute("""INSERT INTO account_balances (account_id, date, balance_cents, source) VALUES (?, ?, ?, 'manual')
                    ON CONFLICT(account_id, date) DO UPDATE SET balance_cents = excluded.balance_cents""", (a["id"], today, total))
    for h in norm.get("history") or []:
        if h["date"] < today:
            conn.execute("""INSERT INTO account_balances (account_id, date, balance_cents, source) VALUES (?, ?, ?, 'manual')
                            ON CONFLICT(account_id, date) DO UPDATE SET balance_cents = excluded.balance_cents""", (a["id"], h["date"], h["cents"]))
    _put(conn, "collectr_last", {"at": now_iso, "username": norm["username"], "value_cents": total, "items": len(norm["items"]),
                                  "items_value_cents": norm["items_value_cents"], "total_cards": norm["total_cards"],
                                  "total_sealed": norm["total_sealed"], "total_graded": norm["total_graded"],
                                  "product_keys": norm["product_keys"], "top_level_keys": norm["top_level_keys"]})
    return a["id"], total


def run_sync():
    with db.read() as conn:
        url = _setting(conn, "collectr_share_url")
    if not url:
        logger.info("collectr: no share link, nothing to do")
        return {"skipped": "not connected"}
    try:
        payload = fetch(share_id(url))
    except Blocked as e:
        logger.info("collectr: %s", e)
        return {"skipped": str(e)}
    norm = normalize(payload)
    if not norm["items"] and not norm["portfolio_value_cents"]:
        logger.info("collectr: feed is empty (showcaseEnabled=%s); keeping the last snapshot", payload.get("showcaseEnabled"))
        return {"skipped": "empty feed - is portfolio sharing on?"}
    with db.write() as conn:
        aid, total = apply(conn, norm)
    logger.info("collectr: %s items, value %s", len(norm["items"]), money.fmt(total))
    return {"account_id": aid, "items": len(norm["items"]), "value_cents": total}


def ingest(payload):
    """A payload captured in the browser (bookmarklet + paste). -> (account_id, total, norm)."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            raise CollectrError("that is not JSON - click the bookmarklet on app.getcollectr.com, then paste what it copied")
    if not isinstance(payload, dict) or "products" not in payload:
        raise CollectrError("that JSON does not look like a Collectr showcase (no products list)")
    norm = normalize(payload)
    if payload.get("source") != "private" and payload.get("showcaseEnabled") is False and not norm["items"]:
        raise CollectrError("Collectr reports the showcase is OFF for this profile. Sign in to app.getcollectr.com and click the bookmark there: it then reads your own portfolio and needs no sharing.")
    if not norm["items"] and not norm["portfolio_value_cents"]:
        raise CollectrError("the feed came back empty - are you signed in to app.getcollectr.com?")
    with db.write() as conn:
        aid, total = apply(conn, norm)
    return aid, total, norm


def status():
    with db.read() as conn:
        url = _setting(conn, "collectr_share_url")
        last = _setting(conn, "collectr_last")
        job = db.row(conn, "SELECT * FROM jobs WHERE name = 'collectr'")
    handle = None
    if url:
        try:
            handle = share_id(url)
        except CollectrError:
            handle = None
    return {"connected": bool(url), "share_url": url, "handle": handle, "last": last, "job": job}


def redact_id(sid):
    return sid[:4] + "…" + sid[-4:] if len(sid) > 10 else sid


_ID_OK = re.compile(r"^@?[A-Za-z0-9_.\-]{2,128}$")


def validate_id(sid):
    if not _ID_OK.match(sid):
        raise CollectrError("that id does not look right: %r" % sid[:40])
    return sid
