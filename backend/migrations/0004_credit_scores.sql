-- Credit score log. No bureau offers a personal API, so the score is typed in
-- when it is checked (Credit Karma, Experian, the card app...). One row per
-- reading; the trend comes from the rows.
CREATE TABLE credit_scores (
  id      INTEGER PRIMARY KEY,
  date    TEXT NOT NULL,
  score   INTEGER NOT NULL CHECK (score BETWEEN 300 AND 850),
  source  TEXT NOT NULL DEFAULT '',        -- 'Credit Karma (TransUnion)', 'Experian', ...
  model   TEXT NOT NULL DEFAULT '',        -- 'VantageScore 3.0', 'FICO 8'
  notes   TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX ix_credit_scores_date ON credit_scores(date);

-- A place for car parts and the like: everyone's categories grow.
INSERT INTO categories (parent_id, name, kind, sort) VALUES (420, 'Car parts', 'expense', 7);
