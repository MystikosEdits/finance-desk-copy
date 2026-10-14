-- A collection tracked in Collectr (trading cards etc.). Collectr has no API,
-- so the collection is a snapshot imported from its CSV export; the account
-- (kind 'asset', institution 'Collectr') carries the total as its manual value
-- and a balance row per import day gives the value history.
CREATE TABLE collectibles (
  id               INTEGER PRIMARY KEY,
  account_id       INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  external_key     TEXT NOT NULL,          -- name|set|number|condition, for diffing between imports
  name             TEXT NOT NULL,
  set_name         TEXT NOT NULL DEFAULT '',
  number           TEXT NOT NULL DEFAULT '',
  condition        TEXT NOT NULL DEFAULT '',
  quantity         INTEGER NOT NULL DEFAULT 1,
  unit_value_cents INTEGER NOT NULL DEFAULT 0,
  value_cents      INTEGER NOT NULL DEFAULT 0,
  cost_cents       INTEGER,                -- what was paid, when the export says
  imported_at      TEXT NOT NULL
) STRICT;
CREATE INDEX ix_collectibles_account ON collectibles(account_id);
