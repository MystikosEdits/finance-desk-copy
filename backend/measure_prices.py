"""Measure the price providers from THIS machine. Run it on the rig:

    .venv\\Scripts\\python.exe backend\\measure_prices.py [SYMBOL ...]

Prints success/failure and latency per provider. Paste the result into the
README's MEASUREMENTS section - the choice of primary provider is a
measurement, not an opinion."""

import sys
import time

import prices

symbols = sys.argv[1:] or ["VTI", "NVDA", "AAPL"]
checks = []
for s in symbols:
    checks.append(("yahoo %s" % s, lambda s=s: prices.yahoo_chart(s)))
    checks.append(("stooq %s" % s, lambda s=s: prices.stooq(s)))
checks.append(("coingecko bitcoin,ethereum (no key)", lambda: prices.coingecko(["bitcoin", "ethereum"])))
checks.append(("yahoo search 'vanguard'", lambda: prices.yahoo_search("vanguard")[:2]))

for name, fn in checks:
    t = time.time()
    try:
        r = fn()
        print("%-36s ok    %5.2fs  %s" % (name, time.time() - t, str(r)[:80]))
    except Exception as e:  # noqa: BLE001
        print("%-36s FAIL  %5.2fs  %s" % (name, time.time() - t, str(e)[:100]))
print("calls:", prices._call_counts)
