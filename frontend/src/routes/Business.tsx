import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { del, get, patch, post, put } from "../api/client";
import type { IncomeSource } from "../api/types";
import { Card, Empty, ErrorLine, Field, Money, Page, Stat, fmtDate } from "../components/ui";
import { fmt } from "../lib/money";

type ScLine = { line: string; label: string; amount_cents: number; deductible_cents: number; count: number; missing_receipts: number };
type ScheduleC = { year: number; lines: ScLine[]; income_cents: number; expenses_cents: number; expenses_deductible_cents: number; unassigned_cents: number; net_profit_before_adjustments_cents: number; home_office_cents: number; mileage_cents: number; mileage_miles_e2: number; mileage_rate_cents: number; net_profit_cents: number; rates_version: string };
type Pnl = { rows: { key: string; income_cents: number; expenses_cents: number; deductible_cents: number; net_cents: number; name?: string }[]; income_cents: number; expenses_cents: number };
type Contractor = { id: number; name: string; contractor_kind: string | null; w9_on_file: number; tin_on_file: number; contact: string; paid_cents: number; payments: number; first_date: string | null; last_date: string | null; over_threshold: boolean; needs_w9: boolean };
type Contractors = { contractors: Contractor[]; unflagged_contract_labor: { id: number; name: string; paid_cents: number; payments: number }[]; threshold_cents: number };
type Missing = { threshold_cents: number; rows: { id: number; date: string; amount_cents: number; payee_raw: string; payee_name: string | null; account_name: string; business_cents: number; categories: string }[] };
type Mileage = { rows: { id: number; date: string; miles_e2: number; purpose: string; from_place: string; to_place: string; business: number }[]; business_miles_e2: number; rate_cents_per_mile: number; deduction_cents: number };
type HomeOffice = { home_office: { sqft: number; total_sqft: number | null; method: string } | null; deduction_cents: number; simplified_rate_cents_per_sqft: number; simplified_max_sqft: number };
type Assets = { threshold_cents: number; rows: { id: number; date: string; amount_cents: number; payee_name: string | null; payee_raw: string; category_name: string | null; line: string | null; note: string; attachments: number }[] };

const TABS = ["overview", "contractors", "receipts", "mileage", "home office", "assets", "income sources"] as const;

export default function Business() {
  const [params, setParams] = useSearchParams();
  const year = parseInt(params.get("year") ?? String(new Date().getFullYear()), 10);
  const tab = params.get("tab") ?? "overview";
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); p.set(k, v); setParams(p, { replace: true }); };
  const years = [year - 2, year - 1, year, year + 1].filter((y) => y <= new Date().getFullYear() + 1);

  return (
    <Page
      title="Business"
      actions={
        <>
          <select className="inp w-24 num" value={year} onChange={(e) => set("year", e.target.value)}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <div className="flex gap-1">
            {TABS.map((t) => (
              <button key={t} className={`btn sm ${tab === t ? "primary" : "ghost"}`} onClick={() => set("tab", t)}>{t}</button>
            ))}
          </div>
        </>
      }
    >
      {tab === "overview" && <Overview year={year} />}
      {tab === "contractors" && <ContractorsTab year={year} />}
      {tab === "receipts" && <ReceiptsTab year={year} />}
      {tab === "mileage" && <MileageTab year={year} />}
      {tab === "home office" && <HomeOfficeTab year={year} />}
      {tab === "assets" && <AssetsTab year={year} />}
      {tab === "income sources" && <SourcesTab year={year} />}
    </Page>
  );
}

function Overview({ year }: { year: number }) {
  const sc = useQuery({ queryKey: ["schedule-c", year], queryFn: () => get<ScheduleC>(`/api/business/schedule-c?year=${year}`) });
  const pnl = useQuery({ queryKey: ["pnl", year], queryFn: () => get<Pnl>(`/api/business/pnl?year=${year}&by=month`) });
  const s = sc.data;
  const chart = (pnl.data?.rows ?? []).map((r) => ({ month: r.key.slice(5), income: r.income_cents / 100, expenses: r.expenses_cents / 100 }));
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-5 gap-3">
        <Stat label="Gross receipts" value={<Money cents={s?.income_cents} tone={false} />} />
        <Stat label="Expenses" value={<Money cents={s ? -s.expenses_cents : undefined} tone={false} />} sub={s ? `${fmt(s.expenses_deductible_cents)} deductible` : ""} />
        <Stat label="Home office + mileage" value={<Money cents={s ? -(s.home_office_cents + s.mileage_cents) : undefined} tone={false} />} sub={s ? `${(s.mileage_miles_e2 / 100).toFixed(0)} mi @ ${s.mileage_rate_cents}¢` : ""} />
        <Stat label="Net profit" value={<Money cents={s?.net_profit_cents} />} sub="Schedule C line 31, estimated" tone={s && s.net_profit_cents < 0 ? "bad" : undefined} />
        <Stat label="Needs a line" value={<Money cents={s ? -s.unassigned_cents : undefined} tone={false} />} sub={s && s.unassigned_cents > 0 ? "business splits without a Schedule C line" : "all assigned"} tone={s && s.unassigned_cents > 0 ? "warn" : undefined} />
      </div>
      <div className="grid grid-cols-5 gap-4">
        <Card title="By month" className="col-span-2">
          <div className="h-64 p-2">
            {chart.length ? (
              <ResponsiveContainer>
                <BarChart data={chart} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid stroke="#1f2a3d" vertical={false} />
                  <XAxis dataKey="month" stroke="#5b667a" fontSize={11} />
                  <YAxis stroke="#5b667a" fontSize={11} tickFormatter={(v) => `$${v >= 1000 ? (v / 1000).toFixed(1).replace(/.0$/, "") + "k" : v}`} />
                  <Tooltip contentStyle={{ background: "#151d2c", border: "1px solid #2a3850", fontSize: 12 }} formatter={(v) => `$${Number(v).toLocaleString()}`} />
                  <Bar dataKey="income" fill="#34d399" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                  <Bar dataKey="expenses" fill="#a78bfa" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            ) : <Empty>no business activity in {year}</Empty>}
          </div>
        </Card>
        <Card title={`Schedule C · ${year}`} className="col-span-3" right={<span className="text-[11px] text-muted">rates {s?.rates_version} · estimate</span>}>
          <table className="tbl">
            <thead><tr><th className="w-14">Line</th><th>Label</th><th className="r">Amount</th><th className="r">Deductible</th><th className="r">Splits</th><th className="r">No receipt</th></tr></thead>
            <tbody>
              {(s?.lines ?? []).map((L) => (
                <tr key={L.line} className={L.line === "unassigned" ? "text-warn" : ""}>
                  <td className="num">{L.line === "unassigned" ? "—" : L.line}</td>
                  <td>{L.line === "unassigned" ? <Link to={`/transactions?tab=all&business=1&uncategorized=1`} className="hover:text-accent">Unassigned line — fix these</Link> : L.label}</td>
                  <td className="r"><Money cents={L.amount_cents} tone={false} /></td>
                  <td className="r"><Money cents={L.deductible_cents} tone={false} /></td>
                  <td className="r num text-muted">{L.count}</td>
                  <td className="r num">{L.line !== "1" && L.missing_receipts > 0 ? <span className="text-warn">{L.missing_receipts}</span> : <span className="text-dim">0</span>}</td>
                </tr>
              ))}
              {s && s.home_office_cents > 0 && <tr><td className="num">30</td><td>Home office (simplified)</td><td className="r"><Money cents={s.home_office_cents} tone={false} /></td><td className="r"><Money cents={s.home_office_cents} tone={false} /></td><td></td><td></td></tr>}
              {s && s.mileage_cents > 0 && <tr><td className="num">9</td><td>Standard mileage</td><td className="r"><Money cents={s.mileage_cents} tone={false} /></td><td className="r"><Money cents={s.mileage_cents} tone={false} /></td><td></td><td></td></tr>}
            </tbody>
          </table>
          {s && s.lines.length === 0 && <Empty>No business splits in {year}. Tag transactions as business in the inbox.</Empty>}
        </Card>
      </div>
    </div>
  );
}

function ContractorsTab({ year }: { year: number }) {
  const q = useQuery({ queryKey: ["contractors", year], queryFn: () => get<Contractors>(`/api/business/contractors?year=${year}`) });
  const qc = useQueryClient();
  const flag = useMutation({ mutationFn: (id: number) => patch(`/api/payees/${id}`, { is_contractor: 1, contractor_kind: "other" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["contractors"] }) });
  const w9 = useMutation({ mutationFn: ({ id, v }: { id: number; v: number }) => patch(`/api/payees/${id}`, { w9_on_file: v }), onSuccess: () => qc.invalidateQueries({ queryKey: ["contractors"] }) });
  const d = q.data;
  return (
    <div className="space-y-4">
      <Card title={`Contractors · 1099-NEC threshold ${fmt(d?.threshold_cents ?? 0)} for ${year} payments`}>
        <table className="tbl">
          <thead><tr><th>Name</th><th>Kind</th><th className="r">Paid {year}</th><th className="r">Payments</th><th>Period</th><th>1099</th><th>W-9</th><th>Contact</th></tr></thead>
          <tbody>
            {(d?.contractors ?? []).map((c) => (
              <tr key={c.id}>
                <td><Link to={`/transactions?tab=all&payee=${c.id}`} className="hover:text-accent">{c.name}</Link></td>
                <td className="text-muted">{c.contractor_kind}</td>
                <td className="r"><Money cents={c.paid_cents} tone={false} /></td>
                <td className="r num text-muted">{c.payments}</td>
                <td className="text-muted num text-[11px]">{c.first_date ? `${fmtDate(c.first_date)} – ${fmtDate(c.last_date)}` : "—"}</td>
                <td>{c.over_threshold ? <span className="chip warn">needed</span> : <span className="chip">under</span>}</td>
                <td>
                  <button className={`chip ${c.w9_on_file ? "ok" : c.needs_w9 ? "bad" : ""}`} onClick={() => w9.mutate({ id: c.id, v: c.w9_on_file ? 0 : 1 })}>
                    {c.w9_on_file ? "on file" : "missing"}
                  </button>
                </td>
                <td className="text-muted text-[12px]">{c.contact}</td>
              </tr>
            ))}
            {d && d.contractors.length === 0 && <tr><td colSpan={8}><Empty>No payees flagged as contractors. Flag your editors and voice actor in Settings › Payees.</Empty></td></tr>}
          </tbody>
        </table>
      </Card>
      {d && d.unflagged_contract_labor.length > 0 && (
        <Card title="Paid on the Contract labor line but not flagged as a contractor">
          <table className="tbl">
            <tbody>
              {d.unflagged_contract_labor.map((u) => (
                <tr key={u.id}><td>{u.name}</td><td className="r"><Money cents={u.paid_cents} tone={false} /></td><td className="r num text-muted">{u.payments} payments</td>
                  <td className="r"><button className="btn sm" onClick={() => flag.mutate(u.id)}>flag as contractor</button></td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function ReceiptsTab({ year }: { year: number }) {
  const q = useQuery({ queryKey: ["missing-receipts", year], queryFn: () => get<Missing>(`/api/attachments/missing?year=${year}`) });
  return (
    <Card title={`Business expenses of ${fmt(q.data?.threshold_cents ?? 7500)} or more with no receipt`} right={<span className="text-[11px] text-muted">press <kbd>a</kbd> on a transaction to attach one</span>}>
      <table className="tbl">
        <thead><tr><th>Date</th><th>Payee</th><th>Account</th><th>Categories</th><th className="r">Business</th><th></th></tr></thead>
        <tbody>
          {(q.data?.rows ?? []).map((r) => (
            <tr key={r.id}>
              <td className="num text-muted">{fmtDate(r.date)}</td>
              <td>{r.payee_name || r.payee_raw}</td>
              <td className="text-muted">{r.account_name}</td>
              <td className="text-muted">{r.categories}</td>
              <td className="r"><Money cents={-r.business_cents} /></td>
              <td className="r"><Link to={`/transactions?tab=all&q=${encodeURIComponent(r.payee_raw.slice(0, 20))}`} className="btn sm">open</Link></td>
            </tr>
          ))}
          {q.data && q.data.rows.length === 0 && <tr><td colSpan={6}><Empty>Every business expense over the threshold has a receipt.</Empty></td></tr>}
        </tbody>
      </table>
    </Card>
  );
}

function MileageTab({ year }: { year: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["mileage", year], queryFn: () => get<Mileage>(`/api/business/mileage?year=${year}`) });
  const [d, setD] = useState({ date: new Date().toISOString().slice(0, 10), miles: "", purpose: "", from_place: "", to_place: "" });
  const [err, setErr] = useState<unknown>(null);
  const add = useMutation({ mutationFn: () => post("/api/business/mileage", { ...d, miles: parseFloat(d.miles) }), onSuccess: () => { setD({ ...d, miles: "", purpose: "" }); qc.invalidateQueries({ queryKey: ["mileage"] }); qc.invalidateQueries({ queryKey: ["schedule-c"] }); }, onError: setErr });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/business/mileage/${id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["mileage"] }); qc.invalidateQueries({ queryKey: ["schedule-c"] }); } });
  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Business miles" value={<span className="num">{((q.data?.business_miles_e2 ?? 0) / 100).toFixed(1)}</span>} />
        <Stat label="Rate" value={<span className="num">{q.data?.rate_cents_per_mile ?? "—"}¢ / mi</span>} sub="IRS standard mileage rate from the rates file" />
        <Stat label="Deduction" value={<Money cents={q.data?.deduction_cents} tone={false} />} sub="Schedule C line 9" />
      </div>
      <Card title="Log a trip">
        <div className="p-4 flex flex-wrap items-end gap-3">
          <Field label="Date"><input className="inp num w-36" type="date" value={d.date} onChange={(e) => setD({ ...d, date: e.target.value })} /></Field>
          <Field label="Miles"><input className="inp num w-24" value={d.miles} onChange={(e) => setD({ ...d, miles: e.target.value })} /></Field>
          <Field label="Purpose"><input className="inp w-64" value={d.purpose} onChange={(e) => setD({ ...d, purpose: e.target.value })} placeholder="shoot, meeting, pickup…" /></Field>
          <Field label="From"><input className="inp w-40" value={d.from_place} onChange={(e) => setD({ ...d, from_place: e.target.value })} /></Field>
          <Field label="To"><input className="inp w-40" value={d.to_place} onChange={(e) => setD({ ...d, to_place: e.target.value })} /></Field>
          <button className="btn primary" disabled={!d.miles || !d.purpose} onClick={() => add.mutate()}>Add</button>
        </div>
      </Card>
      <Card title={`Trips · ${year}`}>
        <table className="tbl">
          <thead><tr><th>Date</th><th className="r">Miles</th><th>Purpose</th><th>Route</th><th></th></tr></thead>
          <tbody>
            {(q.data?.rows ?? []).map((r) => (
              <tr key={r.id}><td className="num text-muted">{fmtDate(r.date)}</td><td className="r num">{(r.miles_e2 / 100).toFixed(1)}</td><td>{r.purpose}</td><td className="text-muted">{[r.from_place, r.to_place].filter(Boolean).join(" → ")}</td>
                <td className="r"><button className="btn sm ghost" onClick={() => remove.mutate(r.id)}>delete</button></td></tr>
            ))}
            {q.data && q.data.rows.length === 0 && <tr><td colSpan={5}><Empty>No trips logged for {year}.</Empty></td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function HomeOfficeTab({ year }: { year: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["home-office", year], queryFn: () => get<HomeOffice>(`/api/business/home-office/${year}`) });
  const [sqft, setSqft] = useState("");
  const [total, setTotal] = useState("");
  const [method, setMethod] = useState("simplified");
  const save = useMutation({ mutationFn: () => put(`/api/business/home-office/${year}`, { sqft: parseInt(sqft || "0", 10), total_sqft: total ? parseInt(total, 10) : null, method }), onSuccess: () => { qc.invalidateQueries({ queryKey: ["home-office"] }); qc.invalidateQueries({ queryKey: ["schedule-c"] }); } });
  const ho = q.data?.home_office;
  return (
    <div className="grid grid-cols-2 gap-4">
      <Card title={`Home office · ${year}`}>
        <div className="p-4 space-y-3">
          {ho && <div className="text-[12px]"><span className="chip ok">{ho.method}</span> {ho.sqft} sqft{ho.total_sqft ? ` of ${ho.total_sqft}` : ""} → deduction <Money cents={q.data?.deduction_cents} tone={false} /></div>}
          <div className="grid grid-cols-3 gap-3">
            <Field label="Office sqft"><input className="inp num" value={sqft} onChange={(e) => setSqft(e.target.value.replace(/\D/g, ""))} placeholder={ho ? String(ho.sqft) : "e.g. 120"} /></Field>
            <Field label="Total home sqft" hint="for the actual method"><input className="inp num" value={total} onChange={(e) => setTotal(e.target.value.replace(/\D/g, ""))} placeholder={ho?.total_sqft ? String(ho.total_sqft) : ""} /></Field>
            <Field label="Method">
              <select className="inp" value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="simplified">simplified ($5/sqft, max 300)</option>
                <option value="actual">actual (accountant computes)</option>
                <option value="none">none</option>
              </select>
            </Field>
          </div>
          <button className="btn primary" disabled={!sqft} onClick={() => save.mutate()}>Save</button>
        </div>
      </Card>
      <Card title="How this is used">
        <div className="p-4 text-[12.5px] text-muted space-y-2">
          <p>The simplified method is {q.data ? `$${q.data.simplified_rate_cents_per_sqft / 100} per square foot up to ${q.data.simplified_max_sqft} sqft` : "$5 per square foot up to 300 sqft"}, and needs the space to be used regularly and exclusively for the business.</p>
          <p>The actual method deducts a share of rent, utilities and insurance by the office's share of the home. The app records the square footage so the accountant has the ratio; it does not compute the actual-method number.</p>
        </div>
      </Card>
    </div>
  );
}

function AssetsTab({ year }: { year: number }) {
  const q = useQuery({ queryKey: ["assets", year], queryFn: () => get<Assets>(`/api/business/assets?year=${year}`) });
  return (
    <Card title={`Business purchases of ${fmt(q.data?.threshold_cents ?? 250000)} or more · ${year}`} right={<span className="text-[11px] text-muted">de minimis safe harbor: expense or depreciate — the accountant's call</span>}>
      <table className="tbl">
        <thead><tr><th>Date</th><th>Payee</th><th>Category</th><th>Line</th><th className="r">Amount</th><th>Receipt</th><th>Note</th></tr></thead>
        <tbody>
          {(q.data?.rows ?? []).map((r) => (
            <tr key={r.id}><td className="num text-muted">{fmtDate(r.date)}</td><td>{r.payee_name || r.payee_raw}</td><td className="text-muted">{r.category_name ?? "—"}</td><td className="num">{r.line ?? "—"}</td>
              <td className="r"><Money cents={r.amount_cents} /></td><td>{r.attachments ? <span className="chip ok">yes</span> : <span className="chip warn">missing</span>}</td><td className="text-muted">{r.note}</td></tr>
          ))}
          {q.data && q.data.rows.length === 0 && <tr><td colSpan={7}><Empty>Nothing over the threshold in {year}.</Empty></td></tr>}
        </tbody>
      </table>
    </Card>
  );
}

function SourcesTab({ year }: { year: number }) {
  const qc = useQueryClient();
  const sources = useQuery({ queryKey: ["income-sources"], queryFn: () => get<IncomeSource[]>("/api/business/income-sources") });
  const by = useQuery({ queryKey: ["income-by-source", year], queryFn: () => get<{ rows: { name: string; kind: string | null; cents: number; n: number }[] }>(`/api/business/income-by-source?year=${year}`) });
  const [name, setName] = useState("");
  const [kind, setKind] = useState("adsense");
  const add = useMutation({ mutationFn: () => post("/api/business/income-sources", { name, kind }), onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["income-sources"] }); } });
  return (
    <div className="grid grid-cols-2 gap-4">
      <Card title={`Income by source · ${year}`}>
        <table className="tbl">
          <thead><tr><th>Source</th><th>Kind</th><th className="r">Amount</th><th className="r">Deposits</th></tr></thead>
          <tbody>
            {(by.data?.rows ?? []).map((r) => <tr key={r.name}><td>{r.name}</td><td className="text-muted">{r.kind ?? ""}</td><td className="r"><Money cents={r.cents} /></td><td className="r num text-muted">{r.n}</td></tr>)}
            {by.data && by.data.rows.length === 0 && <tr><td colSpan={4}><Empty>No business income in {year} yet.</Empty></td></tr>}
          </tbody>
        </table>
        <div className="px-4 py-2 text-[11px] text-dim">One AdSense deposit covers all channels. Open the deposit, press <kbd>s</kbd>, and give each channel its share as a split with that source.</div>
      </Card>
      <Card title="Sources">
        <table className="tbl">
          <tbody>
            {(sources.data ?? []).map((s) => <tr key={s.id}><td>{s.name}</td><td className="text-muted">{s.kind}</td><td>{s.active ? "" : <span className="chip">inactive</span>}</td></tr>)}
          </tbody>
        </table>
        <div className="p-4 flex items-end gap-2">
          <Field label="New source"><input className="inp w-48" value={name} onChange={(e) => setName(e.target.value)} placeholder="My channel" /></Field>
          <Field label="Kind">
            <select className="inp" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="adsense">AdSense channel</option><option value="sponsorship">sponsorship</option><option value="affiliate">affiliate</option><option value="other">other</option>
            </select>
          </Field>
          <button className="btn primary" disabled={!name.trim()} onClick={() => add.mutate()}>Add</button>
        </div>
      </Card>
    </div>
  );
}
