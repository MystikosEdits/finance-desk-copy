"""Money is integer cents. Quantities and prices are integers scaled by 1e8.

This is the ONLY module that imports Decimal. Everything else passes ints
around, and the STRICT tables refuse a float outright - so the one place a
"12.30" string can become a number is here, and it is done with Decimal so
that "0.1 + 0.2" never happens to somebody's tax return.
"""

from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation

_CENT = Decimal("0.01")
_E8 = Decimal("0.00000001")
E8 = 10 ** 8


def parse(text):
    """'-12.30' -> -1230. Accepts '$1,234.56', '(12.30)' as negative, blanks as 0.

    Raises ValueError on anything else - a CSV column that is not money should
    fail the import, not become zero.
    """
    if text is None:
        return 0
    if isinstance(text, int):
        return text
    s = str(text).strip()
    if not s:
        return 0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s.startswith("+"):
        s = s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("not an amount: %r" % (text,))
    if neg:
        d = -d
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def fmt(cents, sign=True):
    """-1230 -> '-$12.30'."""
    neg = cents < 0
    c = abs(int(cents))
    body = "$%s.%02d" % (format(c // 100, ","), c % 100)
    if neg and sign:
        return "-" + body
    return body


def to_e8(text):
    """'0.00234' -> 234000. For quantities and prices."""
    if text is None:
        return 0
    if isinstance(text, int):
        return text
    s = str(text).strip().replace(",", "").replace("$", "")
    if not s:
        return 0
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("not a number: %r" % (text,))
    return int((d * E8).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def e8_str(v, places=8):
    d = (Decimal(int(v)) / E8).quantize(Decimal(1).scaleb(-places))
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def mul_e8_to_cents(qty_e8, price_e8):
    """qty x price, both 1e8-scaled, -> cents. Rounded half-even once."""
    d = Decimal(int(qty_e8)) * Decimal(int(price_e8)) / (Decimal(E8) * Decimal(E8))
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def split_by_pct(total_cents, pcts):
    """Split a total into parts by percentage; the remainder lands on the
    LAST part so the parts always sum to the total. [40, 60] of 1001 -> [400, 601]."""
    if not pcts:
        return []
    parts = []
    acc = 0
    for p in pcts[:-1]:
        c = int((Decimal(total_cents) * Decimal(p) / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN))
        parts.append(c)
        acc += c
    parts.append(total_cents - acc)
    return parts
