-- Finance Desk schema. Every table is STRICT: an INTEGER column refuses a
-- float, which is the whole point of storing money as integer cents.
-- Dates are ISO 'YYYY-MM-DD' TEXT; timestamps are local ISO-8601 TEXT.
-- *_cents = money; *_e8 = quantity or price scaled by 1e8; *_bp = basis points.

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL              -- JSON. Never a secret: this table is backed up to OneDrive.
) STRICT;

CREATE TABLE accounts (
  id                    INTEGER PRIMARY KEY,
  name                  TEXT NOT NULL,
  kind                  TEXT NOT NULL CHECK (kind IN ('checking','savings','credit','cash','brokerage','crypto','loan','asset','liability')),
  institution           TEXT NOT NULL DEFAULT '',
  currency              TEXT NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
  default_business      TEXT NOT NULL DEFAULT 'personal' CHECK (default_business IN ('personal','business','ask')),
  simplefin_id          TEXT UNIQUE,                 -- NULL for manual / CSV-only accounts
  balance_sign          INTEGER NOT NULL DEFAULT 1 CHECK (balance_sign IN (1,-1)),  -- some banks report card balances positive
  opening_balance_cents INTEGER NOT NULL DEFAULT 0,
  opening_date          TEXT,
  apr_bp                INTEGER,                     -- debts only
  min_payment_cents     INTEGER,
  due_day               INTEGER,
  manual_value_cents    INTEGER,                     -- asset/liability accounts valued by hand (car, rig, a loan balance)
  is_closed             INTEGER NOT NULL DEFAULT 0,
  sort                  INTEGER NOT NULL DEFAULT 0,
  created_at            TEXT NOT NULL
) STRICT;

CREATE TABLE account_balances (
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  date            TEXT NOT NULL,
  balance_cents   INTEGER NOT NULL,
  available_cents INTEGER,
  source          TEXT NOT NULL CHECK (source IN ('simplefin','manual','computed')),
  PRIMARY KEY (account_id, date)
) STRICT;

CREATE TABLE categories (
  id                      INTEGER PRIMARY KEY,
  parent_id               INTEGER REFERENCES categories(id),
  name                    TEXT NOT NULL,
  kind                    TEXT NOT NULL CHECK (kind IN ('income','expense','transfer')),
  default_business        INTEGER NOT NULL DEFAULT 0,
  default_schedule_c_line TEXT,
  default_deductible_pct  INTEGER NOT NULL DEFAULT 100,
  icon                    TEXT NOT NULL DEFAULT '',
  is_archived             INTEGER NOT NULL DEFAULT 0,
  sort                    INTEGER NOT NULL DEFAULT 0,
  UNIQUE (parent_id, name)
) STRICT;

CREATE TABLE income_sources (
  id                 INTEGER PRIMARY KEY,
  name               TEXT NOT NULL UNIQUE,
  kind               TEXT NOT NULL CHECK (kind IN ('adsense','sponsorship','affiliate','other')),
  youtube_channel_id TEXT,
  active             INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE TABLE payees (
  id                  INTEGER PRIMARY KEY,
  name                TEXT NOT NULL UNIQUE,
  normalized          TEXT NOT NULL,
  default_category_id INTEGER REFERENCES categories(id),
  default_business    INTEGER,                        -- NULL = no opinion
  is_contractor       INTEGER NOT NULL DEFAULT 0,
  contractor_kind     TEXT,                           -- 'editor','voice','other'
  tin_on_file         INTEGER NOT NULL DEFAULT 0,
  w9_on_file          INTEGER NOT NULL DEFAULT 0,
  contact             TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX ix_payees_norm ON payees(normalized);

CREATE TABLE imports (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('csv','broker_csv','simplefin')),
  filename    TEXT,
  sha256      TEXT,
  account_id  INTEGER REFERENCES accounts(id),
  profile_id  INTEGER,
  inserted    INTEGER NOT NULL DEFAULT 0,
  skipped     INTEGER NOT NULL DEFAULT 0,
  rekeyed     INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  error       TEXT,
  undone_at   TEXT
) STRICT;
-- The same file twice is refused outright.
CREATE UNIQUE INDEX ux_imports_sha ON imports(sha256) WHERE sha256 IS NOT NULL AND undone_at IS NULL;

CREATE TABLE csv_profiles (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  account_id  INTEGER REFERENCES accounts(id),
  column_map  TEXT NOT NULL,          -- JSON: {"date":"Transaction Date","amount":"Amount"} or {"debit":"Debit","credit":"Credit"}, "payee", "memo"
  date_format TEXT NOT NULL DEFAULT '%m/%d/%Y',
  negate      INTEGER NOT NULL DEFAULT 0,   -- 1 when the bank writes purchases as positive numbers
  skip_rows   INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE transactions (
  id              INTEGER PRIMARY KEY,
  account_id      INTEGER NOT NULL REFERENCES accounts(id),
  date            TEXT NOT NULL,          -- transacted_at if the feed had it, else posted. THE tax date.
  posted_date     TEXT,                   -- NULL while pending
  amount_cents    INTEGER NOT NULL,       -- signed from the account's view: negative = money out
  payee_raw       TEXT NOT NULL DEFAULT '',
  description_raw TEXT NOT NULL DEFAULT '',
  memo_raw        TEXT NOT NULL DEFAULT '',
  payee_id        INTEGER REFERENCES payees(id),
  source          TEXT NOT NULL CHECK (source IN ('simplefin','csv','manual')),
  external_id     TEXT,                   -- feed id | sha256 of the csv row | NULL for manual
  import_id       INTEGER REFERENCES imports(id),
  pending         INTEGER NOT NULL DEFAULT 0,
  vanished_at     TEXT,                   -- a pending row the feed stopped sending
  status          TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','reviewed')),
  notes           TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (account_id, external_id)
) STRICT;
CREATE INDEX ix_txn_acct_date ON transactions(account_id, date);
CREATE INDEX ix_txn_date ON transactions(date);
CREATE INDEX ix_txn_payee ON transactions(payee_id);
CREATE INDEX ix_txn_inbox ON transactions(date) WHERE status = 'new';
CREATE INDEX ix_txn_pending ON transactions(account_id, amount_cents) WHERE pending = 1;

-- Every transaction has at least one split, and the splits sum to the
-- transaction. That is enforced in db.set_splits (the one write path) and
-- checked nightly, not by a trigger: a trigger cannot see a whole list at once.
CREATE TABLE splits (
  id               INTEGER PRIMARY KEY,
  transaction_id   INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  amount_cents     INTEGER NOT NULL,
  category_id      INTEGER REFERENCES categories(id),
  business         INTEGER NOT NULL DEFAULT 0 CHECK (business IN (0,1)),
  deductible_pct   INTEGER NOT NULL DEFAULT 100 CHECK (deductible_pct BETWEEN 0 AND 100),
  schedule_c_line  TEXT,                  -- NULL = inherit the category's default
  income_source_id INTEGER REFERENCES income_sources(id),
  note             TEXT NOT NULL DEFAULT '',
  set_by           TEXT NOT NULL DEFAULT 'import'   -- 'import' | 'rule:<id>' | 'user'. Rules never overwrite 'user'.
) STRICT;
CREATE INDEX ix_splits_txn ON splits(transaction_id);
CREATE INDEX ix_splits_cat ON splits(category_id);
CREATE INDEX ix_splits_biz ON splits(business) WHERE business = 1;

CREATE TABLE tags (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
) STRICT;
CREATE TABLE transaction_tags (
  transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  tag_id         INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (transaction_id, tag_id)
) STRICT;

-- A rejected pair is remembered so the next sync does not suggest it again.
CREATE TABLE transfers (
  id         INTEGER PRIMARY KEY,
  out_txn_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  in_txn_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  status     TEXT NOT NULL CHECK (status IN ('auto','suggested','confirmed','rejected')),
  created_at TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX ux_transfers_out ON transfers(out_txn_id) WHERE status <> 'rejected';
CREATE UNIQUE INDEX ux_transfers_in  ON transfers(in_txn_id)  WHERE status <> 'rejected';
CREATE INDEX ix_transfers_pair ON transfers(out_txn_id, in_txn_id);

CREATE TABLE rules (
  id          INTEGER PRIMARY KEY,
  priority    INTEGER NOT NULL DEFAULT 100,
  name        TEXT NOT NULL,
  active      INTEGER NOT NULL DEFAULT 1,
  stop        INTEGER NOT NULL DEFAULT 1,      -- stop after this rule matches
  match_json  TEXT NOT NULL,                   -- present keys are AND'ed
  action_json TEXT NOT NULL,
  hits        INTEGER NOT NULL DEFAULT 0,
  last_hit_at TEXT
) STRICT;

CREATE TABLE attachments (
  id             INTEGER PRIMARY KEY,
  transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
  payee_id       INTEGER REFERENCES payees(id),
  kind           TEXT NOT NULL CHECK (kind IN ('receipt','invoice','w9','statement','other')),
  path           TEXT NOT NULL,                -- relative to data/attachments
  original_name  TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  bytes          INTEGER NOT NULL,
  added_at       TEXT NOT NULL
) STRICT;
CREATE INDEX ix_att_txn ON attachments(transaction_id);
CREATE INDEX ix_att_payee ON attachments(payee_id);

CREATE TABLE recurring (
  id               INTEGER PRIMARY KEY,
  payee_id         INTEGER REFERENCES payees(id),
  name             TEXT NOT NULL,
  cadence          TEXT NOT NULL CHECK (cadence IN ('weekly','biweekly','monthly','quarterly','yearly')),
  expected_cents   INTEGER NOT NULL,
  tolerance_pct    INTEGER NOT NULL DEFAULT 10,
  next_date        TEXT,
  is_subscription  INTEGER NOT NULL DEFAULT 1,
  business         INTEGER NOT NULL DEFAULT 0,
  active           INTEGER NOT NULL DEFAULT 1,
  status           TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested','confirmed','dismissed')),
  last_seen_txn_id INTEGER,
  last_seen_date   TEXT
) STRICT;

CREATE TABLE budgets (
  category_id  INTEGER NOT NULL REFERENCES categories(id),
  month        TEXT NOT NULL,                  -- 'YYYY-MM'
  amount_cents INTEGER NOT NULL,
  PRIMARY KEY (category_id, month)
) STRICT;

CREATE TABLE instruments (
  id           INTEGER PRIMARY KEY,
  symbol       TEXT NOT NULL,
  name         TEXT NOT NULL DEFAULT '',
  kind         TEXT NOT NULL CHECK (kind IN ('stock','etf','crypto','cash','other')),
  price_source TEXT NOT NULL DEFAULT 'auto' CHECK (price_source IN ('auto','yahoo','stooq','coingecko','manual')),
  coingecko_id TEXT,
  UNIQUE (symbol, kind)
) STRICT;

CREATE TABLE trades (
  id            INTEGER PRIMARY KEY,
  account_id    INTEGER NOT NULL REFERENCES accounts(id),
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  date          TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('buy','sell','transfer_in','transfer_out','dividend','interest','fee','split')),
  qty_e8        INTEGER NOT NULL DEFAULT 0,
  price_e8      INTEGER NOT NULL DEFAULT 0,
  fee_cents     INTEGER NOT NULL DEFAULT 0,
  total_cents   INTEGER NOT NULL,              -- cash effect, signed (buy = negative)
  cost_basis_cents INTEGER,                    -- transfer_in: carried basis. NULL = qty x price
  cash_txn_id   INTEGER REFERENCES transactions(id),
  import_id     INTEGER REFERENCES imports(id),
  external_id   TEXT,
  note          TEXT NOT NULL DEFAULT '',
  UNIQUE (account_id, external_id)
) STRICT;
CREATE INDEX ix_trades_pos ON trades(account_id, instrument_id, date);

-- FIFO by default; stored because "specific lot" is a user INPUT.
CREATE TABLE sell_allocations (
  sell_trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
  buy_trade_id  INTEGER NOT NULL REFERENCES trades(id),
  qty_e8        INTEGER NOT NULL,
  cost_cents    INTEGER NOT NULL,
  PRIMARY KEY (sell_trade_id, buy_trade_id)
) STRICT;

CREATE TABLE prices (
  instrument_id INTEGER NOT NULL REFERENCES instruments(id),
  date          TEXT NOT NULL,
  price_e8      INTEGER NOT NULL,
  source        TEXT NOT NULL,                 -- 'manual' wins over anything fetched
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (instrument_id, date)
) STRICT;

CREATE TABLE net_worth_snapshots (
  date              TEXT PRIMARY KEY,
  assets_cents      INTEGER NOT NULL,
  liabilities_cents INTEGER NOT NULL,
  detail_json       TEXT NOT NULL
) STRICT;

CREATE TABLE mileage_log (
  id         INTEGER PRIMARY KEY,
  date       TEXT NOT NULL,
  miles_e2   INTEGER NOT NULL,                 -- hundredths of a mile
  purpose    TEXT NOT NULL,
  from_place TEXT NOT NULL DEFAULT '',
  to_place   TEXT NOT NULL DEFAULT '',
  business   INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE TABLE home_office (
  year       INTEGER PRIMARY KEY,
  sqft       INTEGER NOT NULL,
  total_sqft INTEGER,
  method     TEXT NOT NULL DEFAULT 'simplified' CHECK (method IN ('simplified','actual','none'))
) STRICT;

CREATE TABLE tax_years (
  year                    INTEGER PRIMARY KEY,
  filing_status           TEXT NOT NULL DEFAULT 'single' CHECK (filing_status IN ('single','mfj','mfs','hoh')),
  state                   TEXT NOT NULL DEFAULT '',
  state_flat_rate_bp      INTEGER NOT NULL DEFAULT 0,
  prior_year_tax_cents    INTEGER NOT NULL DEFAULT 0,
  prior_year_agi_cents    INTEGER NOT NULL DEFAULT 0,
  w2_wages_cents          INTEGER NOT NULL DEFAULT 0,
  w2_withholding_cents    INTEGER NOT NULL DEFAULT 0,
  other_income_cents      INTEGER NOT NULL DEFAULT 0,
  itemized_cents          INTEGER,
  notes                   TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE tax_payments (
  id           INTEGER PRIMARY KEY,
  year         INTEGER NOT NULL,
  quarter      INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  jurisdiction TEXT NOT NULL CHECK (jurisdiction IN ('federal','state')),
  amount_cents INTEGER NOT NULL,
  date         TEXT NOT NULL,
  txn_id       INTEGER REFERENCES transactions(id),
  confirmation TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE jobs (
  name             TEXT PRIMARY KEY,
  last_started_at  TEXT,
  last_finished_at TEXT,
  last_ok          INTEGER,
  last_error       TEXT,
  next_due_at      TEXT,
  running          INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE sync_log (
  id           INTEGER PRIMARY KEY,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  ok           INTEGER,
  accounts     INTEGER,
  inserted     INTEGER,
  updated      INTEGER,
  rekeyed      INTEGER,
  vanished     INTEGER,
  errlist_json TEXT,
  error        TEXT
) STRICT;

CREATE TABLE audit_log (
  id          INTEGER PRIMARY KEY,
  at          TEXT NOT NULL,
  actor       TEXT NOT NULL,
  entity      TEXT NOT NULL,
  entity_id   INTEGER NOT NULL,
  action      TEXT NOT NULL,
  before_json TEXT,
  after_json  TEXT
) STRICT;
CREATE INDEX ix_audit_entity ON audit_log(entity, entity_id);

-- The row the UI reads. Derived flags are derived, never stored.
CREATE VIEW v_transactions AS
  SELECT t.*,
         EXISTS (SELECT 1 FROM splits s WHERE s.transaction_id = t.id AND s.business = 1) AS is_business,
         (SELECT COUNT(*) FROM splits s WHERE s.transaction_id = t.id) AS split_count,
         (SELECT tr.id FROM transfers tr
            WHERE (tr.out_txn_id = t.id OR tr.in_txn_id = t.id) AND tr.status <> 'rejected'
            LIMIT 1) AS transfer_id,
         EXISTS (SELECT 1 FROM attachments a WHERE a.transaction_id = t.id) AS has_attachment,
         (SELECT p.name FROM payees p WHERE p.id = t.payee_id) AS payee_name,
         (SELECT a.name FROM accounts a WHERE a.id = t.account_id) AS account_name
  FROM transactions t;
