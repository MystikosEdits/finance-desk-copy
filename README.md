# Finance Desk

A self-hosted personal + business finance desk. Every transaction across every
account, business tagging per line item with Schedule C lines, receipts,
contractor payouts and 1099 tracking, subscriptions, budgets, investments
with live prices, net worth history, estimated-tax set-aside, and a year-end
packet for the accountant.

Built for Windows: it runs as the Scheduled Task `FinanceDesk` on one PC
(the "server" below), on port 8020, and is meant to be reached only over
Tailscale. Banks connect through SimpleFIN Bridge or CSV import.

## Where things are

```
C:\Users\you\Documents\finance-desk\      your checkout = the running app
  backend\      FastAPI. app.py is the server; api\*.py are the routes; the rest are jobs and pure logic.
  frontend\     Vite + React + TypeScript + Tailwind. dist\ is built by deploy.ps1, never committed.
  data\         EVERYTHING the app writes. Gitignored. finance.db, users.json, secret.key,
                backup.key, secrets\ (DPAPI blobs), attachments\, backups\, logs\app.log
  config.json   host/port/schedule. No secrets in it. Set onedrive_backup_dir to a synced
                folder (e.g. C:\Users\you\OneDrive\FinanceDesk-Backup) for off-machine backups.
  deploy.ps1    venv + node + build + task + firewall + restart. Idempotent.
  restart.ps1   after ANY backend change. Editing Python does nothing until this runs.
  extension\    optional Chrome extension for Collectr (see below).
```

Needs Python and Node on the server. **Data exists only on the server**;
nothing about your money is ever in this repo.

## Run it

```powershell
# on the server, after every pull
cd C:\Users\you\Documents\finance-desk
git pull
.\deploy.ps1            # or .\restart.ps1 when only backend .py files changed

# dev on another machine
.\.venv\Scripts\python.exe backend\app.py      # API on 8020 (setup mode binds 127.0.0.1)
cd frontend; npm run dev                        # UI on 5173, proxies /api to 8020
cd backend; ..\.venv\Scripts\python.exe -m pytest -q
cd frontend; npm test; npm run build
.\.venv\Scripts\python.exe backend\app.py --check   # integrity + backup verify
```

For a local look with auth off, set `host` to `127.0.0.1` and `auth_enabled`
to `false` in config.json, then `git checkout -- config.json` afterwards.

## First account

There is no account after a fresh deploy. While no account exists the server
binds `127.0.0.1` whatever `config.json` says, so `/setup` is only reachable
from the server itself. From another machine:

```
ssh -L 8020:127.0.0.1:8020 you@your-server
```

then open `http://localhost:8020/setup`, make the owner account, and run
`.\restart.ps1` on the server so it re-binds to `0.0.0.0` behind the firewall
rule. `/setup` closes itself permanently the moment an account exists.

`python backend\auth.py passwd <name>` changes a password (it prompts; getpass
blocks forever without a real console, so run it in a terminal, not from a
Claude session). `logout-everyone` rotates the cookie secret.

## The pages

| Page | What it is |
|---|---|
| Dashboard | net worth, this month in/out, inbox count, tax set-aside, top spending, business month, upcoming subscriptions, job health |
| Transactions | the **inbox** (new rows, oldest first) and **all** with filters. Keyboard: `j`/`k` move, `c` category, `b` business toggle, `s` split, `r` rule, `n` note, `a` receipt, `x` select, `Enter` reviewed, `u` back to inbox |
| Accounts | balances (bank vs ledger), defaults per account (`personal` / `business` / `ask`), manual balances for assets and debts |
| Cash flow | income vs spending by month, where it went, monthly budgets (personal only) |
| Subscriptions | recurring charges found in the ledger; confirm or dismiss; missed flags; monthly and yearly totals |
| Business | Schedule C by line, P&L by month, contractors vs the 1099-NEC threshold, missing receipts, mileage, home office, assets over the de minimis threshold, income by source (AdSense per channel) |
| Taxes | the estimate with its working, quarterly targets vs payments, inputs (filing status, W-2, prior-year tax for safe harbor), the accountant packet ZIP |
| Investments | holdings with live prices, trades, realized gains (FIFO or specific lots), broker CSV import, instruments and manual prices |
| Net worth | nightly snapshots, live breakdown by account and holding |
| Reports | spending by group/category/payee over any range, income, debts with payoff, CSV export |
| Settings | backups and jobs, SimpleFIN, CSV import and profiles, rules, categories, payees (contractor flags) |

## The jobs (scheduler.py, one thread)

| Job | When | What |
|---|---|---|
| sync | 06:30, 14:30, 22:30 | SimpleFIN pull, reconcile, rules, transfer detection |
| integrity | 03:00 | `PRAGMA integrity_check`, splits sum to their transaction, orphans |
| backup | 03:30 | local copy + rotation + encrypted copy to OneDrive |
| prices | 04:00 | every held instrument; then a net worth snapshot |
| networth | 04:20 | snapshot (again, in case prices failed) |
| recurring | 05:00 | subscription detection |

Any job whose due time passed while the task was not running runs once at
boot (the task is logon-triggered, like Reaction Desk's). "Run now" buttons
in Settings run a job immediately under the same per-job guard.

## Backups — read this once

- Nightly 03:30: `sqlite3 backup API` -> `data\backups\finance-YYYY-MM-DD.db`
  (14 daily + 12 monthly), then the same file encrypted with `data\backup.key`
  -> `OneDrive\FinanceDesk-Backup\`, plus `users.json` and every receipt.
- **The key is the backup.** `deploy.ps1` prints it exactly once when created.
  It goes in the password manager. Settings nags until you confirm.
- **Copying `finance.db` is not a backup.** WAL mode keeps recent writes in
  `finance.db-wal`; a file copy misses them or corrupts the copy. Only the
  backup API is a backup, and that is what `backup.py` uses.
- Verify (Settings > Verify, or `python backend\backup.py verify`) decrypts the
  newest OneDrive copy into a temp dir with the key, runs `integrity_check`,
  and compares per-table row counts with the live database. It proves the copy
  AND the key together.
- Restore on any machine:
  `python backend\backup.py restore finance-2026-09-07.db.fernet --key <key> --to C:\somewhere`
  then put the `.db` at `data\finance.db` and `users.json.fernet` (restored the
  same way) at `data\users.json`. Re-claim a SimpleFIN token; the DPAPI blob
  is deliberately not portable.
- OneDrive upload cannot be proven from the server. Look at the tray icon monthly.

## Money, and where it may be touched

- Money is integer cents (`*_cents`); quantities and prices are integers
  scaled by 1e8 (`*_e8`). STRICT tables refuse a float. `money.py` is the only
  place `Decimal` appears.
- Splits are written only by `db.set_splits`, which refuses a list that does
  not sum to the transaction. Every split carries `set_by`; rules never touch
  a `user` split.
- Every write goes through `db.write()` (process lock + `BEGIN IMMEDIATE`).
  Slow HTTP work happens before the lock.
- Business is a flag on the split, never on the transaction; a 40/60 phone
  bill is two splits. `is_business` on a transaction is a view.
- A brokerage account with trades is valued from holdings x price; one
  without is valued from its balance. That rule is what stops double counting.

## Prices

Providers, in `prices.py`, fetched only from the scheduler thread:
Yahoo's chart JSON (plain httpx, no yfinance) for stocks and ETFs, Stooq as
the fallback, CoinGecko batched for crypto (free demo key in Settings ->
`coingecko_api_key`; instruments carry the CoinGecko id, not the ticker). A
price older than three days is flagged stale, never silently used. Measured
on 2026-09-07 - see MEASUREMENTS below.

## Taxes

`taxcalc.py` is pure and tested against hand-worked numbers. Rates live in
`backend\rates\<year>.json` and every file says `verified: false` until the
accountant has looked at it; the UI labels every figure an estimate. Modelled:
net profit, SE tax with the wage base, half-SE deduction, QBI 20% (no
phase-out), standard vs itemized, federal brackets, additional Medicare,
a flat state rate, safe-harbor quarterly targets, set-aside. Not modelled on
purpose: credits, capital gains rates, AMT, NIIT, depreciation schedules -
those are the accountant's, and the packet gives them what they need.

## Collectr (the collection)

Collectr's API is behind an AWS bot challenge; only a real browser passes it,
and this app does not try to get around that. So the collection is read in
your own signed-in Chrome and handed to the desk:

- **Bookmark:** Investments > collectibles > drag "Grab Collectr" to the
  bookmarks bar; click it on app.getcollectr.com. It reads the private
  portfolio feed with the session token (the token never leaves the tab),
  then opens `/collectr-drop#<json>`, which imports it. React 19 rewrites
  `javascript:` hrefs to a throw, so the link is set on the DOM node after
  render; the script itself comes from `/api/collectr/bookmarklet`.
- **Extension (automatic):** `extension/` is an unpacked Chrome extension
  that does the same thing on a schedule (default every 24 h while Chrome is
  open) and on click. Install: chrome://extensions > Developer mode > Load
  unpacked > pick the `extension` folder. Its popup holds the desk address,
  the handle and the interval, and shows the last result.
- The daily server job still tries a direct read and steps aside quietly
  when blocked.

## Traps

Each of these is measured, or copied from the one that cost Reaction Desk a
session. Do not re-derive them.

| Trap | What to do |
|---|---|
| Edited Python does nothing until the task restarts | `.\restart.ps1`. The sidebar shows STALE SERVER when a backend file is newer than the process. |
| `Out-File -Encoding utf8` writes a BOM; `json.load` chokes | `config.py` reads `utf-8-sig`. `deploy.ps1` writes config with `[IO.File]::WriteAllText`. |
| Over SSH `$env:USERDOMAIN` is `WORKGROUP`, which maps to no account | `deploy.ps1` uses `$env:COMPUTERNAME\$env:USERNAME` for the task principal. |
| `schtasks /Create` cannot set an unlimited run time | The task is made with `Register-ScheduledTask` and `ExecutionTimeLimit 0`. A task without that is killed after 72 h. |
| A PowerShell one-liner that kills processes matching its own command line kills itself | Filter on `Name -eq 'python.exe'`, not on the command line alone. |
| The task is logon-triggered; after an unattended reboot nothing runs until you log in | `scheduler.py` catches up at boot. `/health` reports `last_sync` / `last_backup`; the sidebar goes red past 36 h. |
| `database is locked` between the scheduler thread and a request | Every write goes through `db.write()`, WAL, `busy_timeout=5000`. |
| pydantic v2: `int = None` is NOT optional | `int \| None = None`, always. |
| FastAPI 422 `detail` is a list | `client.ts` flattens it; never `"" + detail` in the UI. |
| `X-Requested-With: FinanceDesk` is required on every non-GET `/api` call | The CSRF lock. `client.ts` adds it; curl must too. |
| Tailwind utility names are a shared namespace | A table class named `.grid` became `display: grid`. Custom classes must not collide (`.tbl`). |
| A numeric `&&` in JSX renders `0` | `!!flag && ...` |
| A mode key (`c`, `s`, `r`, `n`) mounts a modal whose input takes focus before the character lands | The inbox handler cancels the keystroke, or the "c" lands in the search box. |
| DPAPI blobs open only for the Windows user who wrote them | The task must run as the Windows user who set it up. If SimpleFIN suddenly cannot decrypt, re-claim a token in Settings; do not debug. |
| A SimpleFIN setup token claims exactly once; dates on the wire are epoch seconds; pending rows have `posted: 0` | `simplefin.py` handles all three; the access URL is written before `/claim` returns. |
| A pending charge can come back posted under a new id, sometimes with a tip added | `sync.reconcile` rekeys it and keeps the row's splits, notes, receipt. The `rekeyed` column in the sync log says whether your bank does this. |
| A CSV over SimpleFIN's window double-counts | Deterministic row ids + a fuzzy pass against what the feed already pulled; the same file twice is refused; undo while rows are unreviewed. |
| Yahoo changes or blocks its endpoint a few times a year | Provider interface, Stooq fallback, stale flag. Nothing waits on Yahoo inside a request. |
| CoinGecko ids are not tickers; the demo key is 10k calls a month | `coingecko_id` on the instrument from the cached coins list; ONE batched call per run. |
| Lot dates just over a year apart are long-term | 366 days; a test that assumed otherwise was wrong, the engine was right. |

## Decisions

- **Own venv.** Reaction Desk pins numpy etc. in the global site-packages of
  the same interpreter; this app must neither bump nor be bumped by them.
- **Firewall scoped to `100.64.0.0/10`.** Reaction Desk's rule allows any
  address and relies on Tailscale being the only route; bank data gets the
  tighter rule. Never enable Funnel. No port forwards.
- **Node on the server, `dist/` never committed.** A committed build with a
  plausible mtime is the stale-artifact trap. Node is build-time only.
- **Stdlib `sqlite3`, no ORM.** One process, one writer; STRICT tables are the
  protection that matters.
- **Secrets are never in the database**, because the database goes to OneDrive.
- **USD only** in v1. Crypto is priced in USD; that is not a currency problem.

## MEASUREMENTS

- 2026-09-07, price providers (`backend\measure_prices.py`):
  Yahoo chart JSON ok for VTI (0.44 s) and NVDA (0.29 s), Yahoo search ok
  (0.37 s), CoinGecko `simple/price` ok without a key (0.36 s), **Stooq 404
  for both symbols** (0.8 s) - and 404 from a second machine too, in every URL
  form, so the `q/l/` CSV endpoint is gone. Yahoo is primary; the Stooq code
  stays in case it returns, and the working fallback is the stale flag plus
  a manual price in Investments > Instruments.
