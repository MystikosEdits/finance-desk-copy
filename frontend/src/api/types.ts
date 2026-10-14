export type Account = {
  id: number;
  name: string;
  kind: string;
  institution: string;
  default_business: "personal" | "business" | "ask";
  simplefin_id: string | null;
  balance_sign: number;
  opening_balance_cents: number;
  opening_date: string | null;
  apr_bp: number | null;
  min_payment_cents: number | null;
  due_day: number | null;
  manual_value_cents: number | null;
  is_closed: number;
  sort: number;
  balance_cents: number;
  balance_date: string | null;
  balance_source: string;
  available_cents?: number | null;
  computed_cents: number | null;
  is_liability: boolean;
  txn_count: number;
  new_count: number;
  last_txn_date: string | null;
};

export type Category = {
  id: number;
  parent_id: number | null;
  name: string;
  full_name: string;
  kind: "income" | "expense" | "transfer";
  default_business: number;
  default_schedule_c_line: string | null;
  default_deductible_pct: number;
  icon: string;
  is_archived: number;
  sort: number;
  uses: number;
};

export type Split = {
  id?: number;
  transaction_id?: number;
  amount_cents: number;
  category_id: number | null;
  category_name?: string | null;
  category_kind?: string | null;
  effective_line?: string | null;
  business: number;
  deductible_pct: number;
  schedule_c_line: string | null;
  income_source_id: number | null;
  note: string;
  set_by?: string;
};

export type TransferSuggestion = {
  id: number;
  out_txn_id: number;
  in_txn_id: number;
  status: string;
  out_account_name: string;
  in_account_name: string;
  out_date: string;
  in_date: string;
};

export type Txn = {
  id: number;
  account_id: number;
  account_name: string;
  date: string;
  posted_date: string | null;
  amount_cents: number;
  payee_raw: string;
  description_raw: string;
  memo_raw: string;
  payee_id: number | null;
  payee_name: string | null;
  source: "simplefin" | "csv" | "manual";
  pending: number;
  vanished_at: string | null;
  status: "new" | "reviewed";
  notes: string;
  is_business: number;
  split_count: number;
  transfer_id: number | null;
  has_attachment: number;
  splits: Split[];
  tags: string[];
  transfer_suggestions: TransferSuggestion[];
  attachments?: { id: number; kind: string; original_name: string; bytes: number; added_at: string }[];
};

export type TxnList = { total: number; page: number; per: number; inflow: number; outflow: number; rows: Txn[]; vanished?: Txn[] };

export type Payee = {
  id: number;
  name: string;
  normalized: string;
  default_category_id: number | null;
  default_category_name?: string | null;
  default_business: number | null;
  is_contractor: number;
  contractor_kind: string | null;
  tin_on_file: number;
  w9_on_file: number;
  contact: string;
  txn_count?: number;
  total_cents?: number;
  last_date?: string | null;
};

export type Rule = {
  id: number;
  priority: number;
  name: string;
  active: number;
  stop: number;
  match: Record<string, unknown>;
  action: Record<string, unknown>;
  hits: number;
  last_hit_at: string | null;
};

export type IncomeSource = { id: number; name: string; kind: string; youtube_channel_id: string | null; active: number };

export const SCHEDULE_C_LINES: { line: string; label: string }[] = [
  { line: "8", label: "8 Advertising" },
  { line: "9", label: "9 Car and truck" },
  { line: "10", label: "10 Commissions and fees" },
  { line: "11", label: "11 Contract labor" },
  { line: "13", label: "13 Depreciation" },
  { line: "15", label: "15 Insurance" },
  { line: "16b", label: "16b Interest (other)" },
  { line: "17", label: "17 Legal and professional" },
  { line: "18", label: "18 Office expense" },
  { line: "20b", label: "20b Rent (other)" },
  { line: "21", label: "21 Repairs and maintenance" },
  { line: "22", label: "22 Supplies" },
  { line: "23", label: "23 Taxes and licenses" },
  { line: "24a", label: "24a Travel" },
  { line: "24b", label: "24b Meals" },
  { line: "25", label: "25 Utilities" },
  { line: "26", label: "26 Wages" },
  { line: "27a", label: "27a Other expenses" },
];
