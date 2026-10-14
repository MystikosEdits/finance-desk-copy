-- Starting categories. Schedule C line numbers are the 2025 form's:
--   8 Advertising, 9 Car and truck, 10 Commissions and fees, 11 Contract labor,
--   13 Depreciation, 15 Insurance, 17 Legal and professional, 18 Office expense,
--   20b Rent (other), 21 Repairs, 22 Supplies, 23 Taxes and licenses,
--   24a Travel, 24b Meals (50%), 25 Utilities, 27a Other expenses.
-- A category's default line is a default: any split can override it.

-- Business expense tree ---------------------------------------------------
INSERT INTO categories (id, parent_id, name, kind, default_business, default_schedule_c_line, default_deductible_pct, sort) VALUES
  (100, NULL, 'Business',                 'expense', 1, NULL,  100, 100),
  (101, 100,  'Contract labor',           'expense', 1, '11',  100, 1),
  (102, 100,  'Software & subscriptions', 'expense', 1, '18',  100, 2),
  (103, 100,  'Equipment & supplies',     'expense', 1, '22',  100, 3),
  (104, 100,  'Advertising & promotion',  'expense', 1, '8',   100, 4),
  (105, 100,  'Internet & phone',         'expense', 1, '25',  100, 5),
  (106, 100,  'Legal & professional',     'expense', 1, '17',  100, 6),
  (107, 100,  'Business meals',           'expense', 1, '24b', 50,  7),
  (108, 100,  'Travel',                   'expense', 1, '24a', 100, 8),
  (109, 100,  'Insurance',                'expense', 1, '15',  100, 9),
  (110, 100,  'Taxes & licenses',         'expense', 1, '23',  100, 10),
  (111, 100,  'Bank & payment fees',      'expense', 1, '10',  100, 11),
  (112, 100,  'Music & stock media',      'expense', 1, '27a', 100, 12),
  (113, 100,  'Education & research',     'expense', 1, '27a', 100, 13),
  (114, 100,  'Office & rent',            'expense', 1, '20b', 100, 14),
  (115, 100,  'Repairs & maintenance',    'expense', 1, '21',  100, 15),
  (116, 100,  'Other business',           'expense', 1, '27a', 100, 16);

-- Business income ----------------------------------------------------------
INSERT INTO categories (id, parent_id, name, kind, default_business, default_schedule_c_line, sort) VALUES
  (200, NULL, 'Business income',      'income', 1, '1', 200),
  (201, 200,  'YouTube AdSense',      'income', 1, '1', 1),
  (202, 200,  'Sponsorships',         'income', 1, '1', 2),
  (203, 200,  'Affiliate',            'income', 1, '1', 3),
  (204, 200,  'Other business income','income', 1, '1', 4);

-- Personal income ----------------------------------------------------------
INSERT INTO categories (id, parent_id, name, kind, sort) VALUES
  (300, NULL, 'Personal income', 'income', 300),
  (301, 300,  'Paycheck',        'income', 1),
  (302, 300,  'Interest',        'income', 2),
  (303, 300,  'Dividends',       'income', 3),
  (304, 300,  'Refunds',         'income', 4),
  (305, 300,  'Gifts received',  'income', 5),
  (306, 300,  'Other income',    'income', 6);

-- Personal expense tree ----------------------------------------------------
INSERT INTO categories (id, parent_id, name, kind, sort) VALUES
  (400, NULL, 'Housing',              'expense', 400),
  (401, 400,  'Rent / mortgage',      'expense', 1),
  (402, 400,  'Utilities',            'expense', 2),
  (403, 400,  'Home & furniture',     'expense', 3),
  (410, NULL, 'Food',                 'expense', 410),
  (411, 410,  'Groceries',            'expense', 1),
  (412, 410,  'Restaurants',          'expense', 2),
  (413, 410,  'Coffee & snacks',      'expense', 3),
  (414, 410,  'Delivery',             'expense', 4),
  (420, NULL, 'Transport',            'expense', 420),
  (421, 420,  'Gas',                  'expense', 1),
  (422, 420,  'Car payment',          'expense', 2),
  (423, 420,  'Car insurance',        'expense', 3),
  (424, 420,  'Parking & tolls',      'expense', 4),
  (425, 420,  'Rideshare & transit',  'expense', 5),
  (426, 420,  'Maintenance',          'expense', 6),
  (430, NULL, 'Shopping',             'expense', 430),
  (431, 430,  'Clothing',             'expense', 1),
  (432, 430,  'Electronics',          'expense', 2),
  (433, 430,  'Amazon & general',     'expense', 3),
  (440, NULL, 'Entertainment',        'expense', 440),
  (441, 440,  'Streaming',            'expense', 1),
  (442, 440,  'Games',                'expense', 2),
  (443, 440,  'Events & going out',   'expense', 3),
  (450, NULL, 'Health',               'expense', 450),
  (451, 450,  'Medical',              'expense', 1),
  (452, 450,  'Pharmacy',             'expense', 2),
  (453, 450,  'Gym & fitness',        'expense', 3),
  (460, NULL, 'Personal',             'expense', 460),
  (461, 460,  'Personal care',        'expense', 1),
  (462, 460,  'Gifts given',          'expense', 2),
  (463, 460,  'Subscriptions',        'expense', 3),
  (464, 460,  'Education',            'expense', 4),
  (465, 460,  'Fees & interest',      'expense', 5),
  (466, 460,  'Taxes',                'expense', 6),
  (467, 460,  'Cash & ATM',           'expense', 7),
  (468, 460,  'Uncategorized',        'expense', 99);

-- Transfers: money moving between own pockets. Never spending, never income.
INSERT INTO categories (id, parent_id, name, kind, sort) VALUES
  (500, NULL, 'Transfer',                  'transfer', 500),
  (501, 500,  'Credit card payment',       'transfer', 1),
  (502, 500,  'Owner draw',                'transfer', 2),   -- LLC -> personal
  (503, 500,  'Owner contribution',        'transfer', 3),   -- personal -> LLC
  (504, 500,  'To investments',            'transfer', 4),
  (505, 500,  'External / unlinked',       'transfer', 5),   -- the other side is somewhere we cannot see
  (506, 500,  'Estimated tax payment',     'transfer', 6);   -- tracked in tax_payments, not as an expense

INSERT INTO settings (key, value) VALUES
  ('backup_key_acknowledged', 'false'),
  ('first_sync_floor_days', '365');
