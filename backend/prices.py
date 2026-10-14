"""Daily prices for the instruments held. Three providers behind one
interface, fetched only from the scheduler thread, cached in the prices
table. A request never waits on Yahoo.

  yahoo_chart   equities/ETFs. The chart JSON endpoint from plain httpx, no
                yfinance (which drags pandas, lxml, peewee, curl_cffi in and
                breaks a few times a year). Primary until measured otherwise.
  stooq         equities/ETFs fallback, no key, CSV, daily close.
  coingecko     crypto. ONE batched /simple/price call per run for every coin,
                demo key in settings['coingecko_api_key'] (free, 10k calls/mo).
                ids are CoinGecko ids ("bitcoin"), not tickers - instruments
                carry coingecko_id, chosen from the cached coins list.

A price older than STALE_DAYS is flagged in the UI, never silently used as
current. A 'manual' row wins over anything fetched for the same date.
"""

import csv
import io
import json
import os
import time
from datetime import timedelta

import httpx

import config
import db
import log
import money

logger = log.get("prices")
STALE_DAYS = 3
TIMEOUT = 20.0
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinanceDesk/1.0"}
COINS_CACHE = os.path.join(config.DATA, "coingecko-coins.json")
_call_counts = {"yahoo": 0, "stooq": 0, "coingecko": 0}


class ProviderError(Exception):
    pass


def yahoo_chart(symbol):
    _call_counts["yahoo"] += 1
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s" % symbol
    with httpx.Client(timeout=TIMEOUT, headers=UA) as c:
        r = c.get(url, params={"range": "5d", "interval": "1d"})
    if r.status_code != 200:
        raise ProviderError("yahoo %s: HTTP %d" % (symbol, r.status_code))
    try:
        res = r.json()["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        meta = res.get("meta", {})
    except (KeyError, IndexError, TypeError, ValueError):
        raise ProviderError("yahoo %s: unexpected JSON" % symbol)
    pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if not pairs:
        raise ProviderError("yahoo %s: no closes" % symbol)
    t, close = pairs[-1]
    d = config.now().fromtimestamp(t).date().isoformat()
    # regularMarketPrice is fresher during the session; use it for today
    rmp = meta.get("regularMarketPrice")
    if rmp:
        close = rmp
        d = config.today().isoformat()
    return d, money.to_e8("%.8f" % close), meta.get("shortName") or meta.get("longName") or ""


def stooq(symbol):
    _call_counts["stooq"] += 1
    s = symbol.lower()
    if "." not in s:
        s += ".us"
    url = "https://stooq.com/q/l/"
    with httpx.Client(timeout=TIMEOUT, headers=UA) as c:
        r = c.get(url, params={"s": s, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    if r.status_code != 200:
        raise ProviderError("stooq %s: HTTP %d" % (symbol, r.status_code))
    rows = list(csv.DictReader(io.StringIO(r.text)))
    if not rows or rows[0].get("Close") in (None, "", "N/D"):
        raise ProviderError("stooq %s: no data" % symbol)
    return rows[0]["Date"], money.to_e8(rows[0]["Close"]), ""


def coingecko(ids, api_key=None):
    _call_counts["coingecko"] += 1
    headers = dict(UA)
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    with httpx.Client(timeout=TIMEOUT, headers=headers) as c:
        r = c.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(ids), "vs_currencies": "usd"})
    if r.status_code == 429:
        raise ProviderError("coingecko: rate limited (429)")
    if r.status_code != 200:
        raise ProviderError("coingecko: HTTP %d" % r.status_code)
    data = r.json()
    out = {}
    for cid in ids:
        if cid in data and "usd" in data[cid]:
            out[cid] = money.to_e8("%.8f" % data[cid]["usd"])
    return out


def coins_list(api_key=None, max_age_days=7):
    """CoinGecko's id/symbol/name list, cached on disk."""
    try:
        st = os.stat(COINS_CACHE)
        if time.time() - st.st_mtime < max_age_days * 86400:
            with open(COINS_CACHE, encoding="utf-8") as fh:
                return json.load(fh)
    except OSError:
        pass
    headers = dict(UA)
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    _call_counts["coingecko"] += 1
    with httpx.Client(timeout=TIMEOUT, headers=headers) as c:
        r = c.get("https://api.coingecko.com/api/v3/coins/list")
    if r.status_code != 200:
        raise ProviderError("coingecko coins list: HTTP %d" % r.status_code)
    data = r.json()
    os.makedirs(config.DATA, exist_ok=True)
    with open(COINS_CACHE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return data


def yahoo_search(q):
    _call_counts["yahoo"] += 1
    with httpx.Client(timeout=TIMEOUT, headers=UA) as c:
        r = c.get("https://query1.finance.yahoo.com/v1/finance/search", params={"q": q, "quotesCount": 8, "newsCount": 0})
    if r.status_code != 200:
        raise ProviderError("yahoo search: HTTP %d" % r.status_code)
    out = []
    for qt in r.json().get("quotes", []):
        if qt.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND", "INDEX"):
            out.append({"symbol": qt.get("symbol"), "name": qt.get("shortname") or qt.get("longname") or "",
                        "kind": "etf" if qt.get("quoteType") == "ETF" else "stock", "exchange": qt.get("exchange")})
    return out


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------
def store(conn, instrument_id, d, price_e8, source):
    if db.scalar(conn, "SELECT 1 FROM prices WHERE instrument_id = ? AND date = ? AND source = 'manual'", (instrument_id, d)):
        return False
    conn.execute("""INSERT INTO prices (instrument_id, date, price_e8, source, fetched_at) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(instrument_id, date) DO UPDATE SET price_e8 = excluded.price_e8, source = excluded.source, fetched_at = excluded.fetched_at""",
                 (instrument_id, d, price_e8, source, config.iso_now()))
    return True


def latest(conn, instrument_id, on_or_before=None):
    d = on_or_before or config.today().isoformat()
    r = db.row(conn, "SELECT * FROM prices WHERE instrument_id = ? AND date <= ? ORDER BY date DESC LIMIT 1", (instrument_id, d))
    if not r:
        return None
    age = (config.today() - config.today().fromisoformat(r["date"])).days if not on_or_before else 0
    r["stale"] = age > STALE_DAYS
    r["age_days"] = age
    return r


def refresh_all():
    """The job. Equities one by one (yahoo, then stooq), crypto in one call."""
    with db.read() as conn:
        instruments = db.rows(conn, "SELECT * FROM instruments WHERE price_source <> 'manual'")
        key = db.scalar(conn, "SELECT value FROM settings WHERE key = 'coingecko_api_key'")
    api_key = json.loads(key) if key else None
    results = {"ok": 0, "failed": [], "calls": None}
    crypto = [i for i in instruments if i["kind"] == "crypto" and i["coingecko_id"] and i["price_source"] in ("auto", "coingecko")]
    equities = [i for i in instruments if i["kind"] in ("stock", "etf") and i["price_source"] in ("auto", "yahoo", "stooq")]
    fetched = []
    for inst in equities:
        got = None
        order = ["yahoo", "stooq"] if inst["price_source"] == "auto" else [inst["price_source"]]
        for prov in order:
            try:
                d, p, _ = yahoo_chart(inst["symbol"]) if prov == "yahoo" else stooq(inst["symbol"])
                got = (d, p, prov)
                break
            except (ProviderError, httpx.HTTPError) as e:
                logger.warning("prices: %s via %s failed: %s", inst["symbol"], prov, e)
        if got:
            fetched.append((inst["id"], got[0], got[1], got[2]))
        else:
            results["failed"].append(inst["symbol"])
    if crypto:
        try:
            got = coingecko([i["coingecko_id"] for i in crypto], api_key)
            today = config.today().isoformat()
            for inst in crypto:
                if inst["coingecko_id"] in got:
                    fetched.append((inst["id"], today, got[inst["coingecko_id"]], "coingecko"))
                else:
                    results["failed"].append(inst["symbol"])
        except (ProviderError, httpx.HTTPError) as e:
            logger.warning("prices: coingecko failed: %s", e)
            results["failed"] += [i["symbol"] for i in crypto]
    with db.write() as conn:
        for iid, d, p, src in fetched:
            if store(conn, iid, d, p, src):
                results["ok"] += 1
    results["calls"] = dict(_call_counts)
    logger.info("prices: %s", results)
    try:
        import networth
        networth.snapshot_today()
    except Exception as e:  # noqa: BLE001
        logger.warning("prices: snapshot after refresh failed: %s", e)
    if results["failed"] and not results["ok"]:
        raise RuntimeError("no prices fetched; failed: %s" % ", ".join(results["failed"]))
    return results


def history(conn, instrument_id, frm=None, to=None):
    frm = frm or (config.today() - timedelta(days=365)).isoformat()
    to = to or config.today().isoformat()
    return db.rows(conn, "SELECT date, price_e8, source FROM prices WHERE instrument_id = ? AND date BETWEEN ? AND ? ORDER BY date",
                   (instrument_id, frm, to))
