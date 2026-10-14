"""SimpleFIN Bridge client. Talks HTTP, returns plain dicts, touches no database.

THE PROTOCOL, AS MEASURED AGAINST THE SPEC
------------------------------------------
- A setup token is base64 of a claim URL. POST to it once, with no body, and
  the response body is the access URL: https://user:pass@bridge.simplefin.org/simplefin
  The token is spent by that POST. Claim twice and you get a 403 and nothing.
- GET {access_url}/accounts?start-date=<epoch>&end-date=<epoch>&pending=1
  Dates on the wire are UNIX EPOCH SECONDS, not ISO. end-date is exclusive.
- Amounts are strings ("-12.30"). `posted` is epoch seconds and is 0 on a
  pending row. `transacted_at` is optional epoch seconds.
- The credentials live IN the URL. Never log it; log the host.
"""

import base64
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx

import money

TIMEOUT = 60.0


class SimpleFinError(Exception):
    pass


def split_access_url(access_url):
    """-> (base_url_without_creds, username, password)."""
    u = urlsplit(access_url.strip())
    if not u.username or not u.password:
        raise SimpleFinError("access URL has no credentials in it")
    host = u.hostname + (":%d" % u.port if u.port else "")
    base = urlunsplit((u.scheme, host, u.path.rstrip("/"), "", ""))
    return base, u.username, u.password


def redact(access_url):
    try:
        base, user, _ = split_access_url(access_url)
        return base + " (user %s...)" % user[:4]
    except SimpleFinError:
        return "(unparseable access url)"


def claim(setup_token):
    """Spend a setup token. -> access URL. Raises SimpleFinError."""
    token = setup_token.strip()
    try:
        claim_url = base64.b64decode(token + "=" * (-len(token) % 4)).decode("utf-8").strip()
    except Exception:
        raise SimpleFinError("that does not look like a SimpleFIN setup token")
    if not claim_url.startswith("https://"):
        raise SimpleFinError("decoded token is not an https URL")
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(claim_url)
    if r.status_code == 403:
        raise SimpleFinError("token refused (403): already claimed, or expired. Make a new one at bridge.simplefin.org")
    if r.status_code != 200:
        raise SimpleFinError("claim failed: HTTP %d" % r.status_code)
    access = r.text.strip()
    split_access_url(access)  # validates
    return access


def fetch(access_url, start, end=None, pending=True, balances_only=False):
    """GET /accounts. start/end are datetimes (local); -> the raw JSON dict.
    Raises SimpleFinError on 402/403 with a message a person can act on."""
    base, user, pw = split_access_url(access_url)
    params = {"start-date": int(start.timestamp())}
    if end:
        params["end-date"] = int((end + timedelta(days=1)).timestamp())  # exclusive
    if pending:
        params["pending"] = 1
    if balances_only:
        params["balances-only"] = 1
    with httpx.Client(timeout=TIMEOUT, auth=(user, pw)) as c:
        r = c.get(base + "/accounts", params=params)
    if r.status_code == 402:
        raise SimpleFinError("SimpleFIN says payment required (402): the Bridge subscription lapsed")
    if r.status_code == 403:
        raise SimpleFinError("SimpleFIN refused the credentials (403): re-claim a token in Settings")
    if r.status_code != 200:
        raise SimpleFinError("SimpleFIN returned HTTP %d" % r.status_code)
    try:
        return r.json()
    except ValueError:
        raise SimpleFinError("SimpleFIN returned something that is not JSON")


def _date(epoch):
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch)).date().isoformat()


def normalize(payload):
    """Raw JSON -> list of accounts, each with normalized transactions.

    Every row gets: external_id, date, posted_date, amount_cents, payee_raw,
    description_raw, memo_raw, pending. Duplicated ids within one account
    (it happens) collapse to the first. Also returns errlist as strings."""
    out = []
    for a in payload.get("accounts", []) or []:
        org = a.get("org") or {}
        seen = set()
        txns = []
        for t in a.get("transactions", []) or []:
            tid = str(t.get("id") or "")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            posted = _date(t.get("posted"))
            transacted = _date(t.get("transacted_at"))
            pending = bool(t.get("pending")) or not posted
            txns.append({
                "external_id": tid,
                "date": transacted or posted or datetime.now().date().isoformat(),
                "posted_date": posted,
                "amount_cents": money.parse(t.get("amount")),
                "payee_raw": (t.get("payee") or "").strip(),
                "description_raw": (t.get("description") or "").strip(),
                "memo_raw": (t.get("memo") or "").strip(),
                "pending": 1 if pending else 0,
            })
        out.append({
            "simplefin_id": str(a.get("id")),
            "name": a.get("name") or "Account",
            "institution": org.get("name") or org.get("domain") or "",
            "currency": a.get("currency") or "USD",
            "balance_cents": money.parse(a.get("balance")),
            "available_cents": money.parse(a.get("available-balance")) if a.get("available-balance") not in (None, "") else None,
            "balance_date": _date(a.get("balance-date")) or datetime.now().date().isoformat(),
            "transactions": txns,
        })
    errs = []
    for e in payload.get("errlist", []) or payload.get("errors", []) or []:
        errs.append(e if isinstance(e, str) else "%s: %s" % (e.get("code", ""), e.get("msg", e)))
    return out, errs
