"""Turn what the bank prints into a name a person would use.

    "SQ *BLUE BOTTLE 0412 SAN FRANCISCO CA"   -> "blue bottle"
    "AMZN Mktp US*2K4L98HJ2 Amzn.com/bill WA" -> "amazon"
    "PAYPAL *ADOBE INC 402-935-7733 CA"       -> "adobe"

The normalized form is what rules match on and what dedupe compares, so it
must be STABLE: the same merchant must normalize the same way whether it came
from SimpleFIN today or a CSV from last year. Keep the table small and boring.
"""

import re

import db

_PREFIXES = (
    "sq *", "sq*", "tst*", "tst *", "paypal *", "paypal*", "pp*", "pos ", "purchase ",
    "debit card purchase ", "recurring payment ", "checkcard ", "check card ", "visa ", "ach ",
    "web pmt ", "online payment ", "pmt ", "payment to ", "ppd id:", "ccd id:",
)
_ALIASES = [
    (re.compile(r"\bamzn\b|\bamazon\b|amzn mktp"), "amazon"),
    (re.compile(r"\bgoogle\s*\*?youtube|youtube premium"), "youtube premium"),
    (re.compile(r"\bgoogle\b.*adsense|adsense"), "google adsense"),
    (re.compile(r"\bapple\.com/bill|\bapple\b"), "apple"),
    (re.compile(r"\buber\s*eats"), "uber eats"),
    (re.compile(r"\buber\b"), "uber"),
    (re.compile(r"\bdoordash"), "doordash"),
    (re.compile(r"\bspotify"), "spotify"),
    (re.compile(r"\bnetflix"), "netflix"),
    (re.compile(r"\badobe"), "adobe"),
    (re.compile(r"\bopenai|chatgpt"), "openai"),
    (re.compile(r"\banthropic|claude\.ai"), "anthropic"),
    (re.compile(r"\btailscale"), "tailscale"),
    (re.compile(r"\bgithub"), "github"),
    (re.compile(r"\btopaz labs"), "topaz labs"),
    (re.compile(r"\bhiggsfield"), "higgsfield"),
    (re.compile(r"\bcostco"), "costco"),
    (re.compile(r"\btarget\b"), "target"),
    (re.compile(r"\bwalmart|wal-mart"), "walmart"),
    (re.compile(r"\bstarbucks"), "starbucks"),
    (re.compile(r"\bchevron|\bshell oil|\bshell service|\bexxon|\b76 \b|\barco\b"), "gas station"),
    (re.compile(r"\bvenmo"), "venmo"),
    (re.compile(r"\bzelle"), "zelle"),
    (re.compile(r"\bcash app|\bsquare cash"), "cash app"),
    (re.compile(r"\bwise\b|transferwise"), "wise"),
]
_NOISE = re.compile(
    r"(\b\d{2,}[\w-]*\b)"                 # numbers: store ids, phone numbers, order refs
    r"|(\b[a-z]{2}\b\s*$)"                # trailing state code
    r"|(\b(inc|llc|ltd|co|corp|com|www|us|usa)\b)"
    r"|([#*.:/\\\-])"
)


def normalize(raw):
    s = (raw or "").lower().strip()
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):].strip()
    for rx, name in _ALIASES:
        if rx.search(s):
            return name
    s = _NOISE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Drop a trailing city word if what is left is 3+ tokens: "blue bottle san francisco" -> keep it,
    # too clever hurts here; leave city names in. Rules can use "contains".
    return s[:60] or (raw or "").lower().strip()[:60]


def display_name(raw):
    """A title-cased version of the normalized name for a new payee."""
    n = normalize(raw)
    return " ".join(w.capitalize() if len(w) > 2 else w.upper() if w in ("tv", "ai") else w for w in n.split()) or "Unknown"


def similarity(a, b):
    """0..1 on normalized tokens. Jaccard, or containment when the smaller
    side has 2+ tokens: a pending "THE DINER SF" and its posted twin
    "THE DINER SAN FRANCISCO CA" are the same charge, and Jaccard alone
    scores that 0.5 - measured on the fixture, which is a real shape."""
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    j = inter / len(ta | tb)
    small = min(len(ta), len(tb))
    c = inter / small if small >= 2 else j
    return max(j, c)


def resolve(conn, raw_payee, raw_description=""):
    """Find or create the payee for a raw string. -> payee id or None.
    Match on the normalized form first (stable across sources), then create."""
    text = raw_payee or raw_description
    if not text.strip():
        return None
    norm = normalize(text)
    r = db.row(conn, "SELECT id FROM payees WHERE normalized = ? LIMIT 1", (norm,))
    if r:
        return r["id"]
    name = display_name(text)
    # Two different raw strings can title-case to the same display name.
    r = db.row(conn, "SELECT id FROM payees WHERE name = ?", (name,))
    if r:
        return r["id"]
    cur = conn.execute("INSERT INTO payees (name, normalized) VALUES (?, ?)", (name, norm))
    return cur.lastrowid
