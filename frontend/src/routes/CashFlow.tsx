import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { get, post, put } from "../api/client";
import { Card, Empty, Money, Page, Stat } from "../components/ui";
import { fmt, parse } from "../lib/money";

type CF = { period: string; income_cents: number; spending_cents: number; n: number };
type BudgetRow = { id: number; name: string; parent_id: number; parent_name: string; budget_cents: number | null; actual_cents: number; prev_cents: number; remaining_cents: number | null };
type Budgets = { month: string; from: string; to: string; rows: BudgetRow[]; budget_total_cents: number; actual_total_cents: number; uncategorized_cents: number };
type Spend = { name: string; id: number | null; cents: number; n: number };

function monthShift(m: string, d: number) {
  const [y, mm] = m.split("-").map(Number);
  const dt = new Date(y, mm - 1 + d, 1);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
}

export default function CashFlow() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? new Date().toISOString().slice(0, 7);
  const [scope, setScope] = useState<"all" | "0" | "1">("0");
  const [months, setMonths] = useState(12);
  const frm = monthShift(month, -(months - 1)) + "-01";
  const to = monthShift(month, 1) + "-01";
  const cf = useQuery({ queryKey: ["cashflow", frm, to, scope], queryFn: () => get<CF[]>(`/api/reports/cashflow?frm=${frm}&to=${to}&business=${scope}`) });
  const budgets = useQuery({ queryKey: ["budgets", month], queryFn: () => get<Budgets>(`/api/budgets?month=${month}`) });
  const spend = useQuery({ queryKey: ["spend-month", month, scope], queryFn: () => get<Spend[]>(`/api/reports/spending?frm=${month}-01&to=${monthShift(month, 1)}-01&by=parent&business=${scope}`) });
  const [edits, setEdits] = useState<Record<number, string>>({});
  const save = useMutation({ mutationFn: ({ id, cents }: { id: number; cents: number | null }) => put(`/api/budgets/${id}/${month}`, { amount_cents: cents }), onSuccess: () => qc.invalidateQueries({ queryKey: ["budgets"] }) });
  const copy = useMutation({ mutationFn: () => post(`/api/budgets/copy?frm=${monthShift(month, -1)}&to=${month}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["budgets"] }) });

  const data = (cf.data ?? []).map((r) => ({ month: r.period.slice(2), income: r.income_cents / 100, spending: r.spending_cents / 100, net: (r.income_cents - r.spending_cents) / 100 }));
  const cur = (cf.data ?? []).find((r) => r.period === month);
  const b = budgets.data;
  const groups = new Map<string, BudgetRow[]>();
  for (const r of b?.rows ?? []) groups.set(r.parent_name, [...(groups.get(r.parent_name) ?? []), r]);

  return (
    <Page
      title="Cash flow"
      actions={<>
        <div className="flex gap-1">{(["0", "1", "all"] as const).map((s) => <button key={s} className={`btn sm ${scope === s ? "primary" : "ghost"}`} onClick={() => setScope(s)}>{s === "0" ? "personal" : s === "1" ? "business" : "both"}</button>)}</div>
        <div className="flex gap-1">{[6, 12, 24].map((n) => <button key={n} className={`btn sm ${months === n ? "primary" : "ghost"}`} onClick={() => setMonths(n)}>{n}m</button>)}</div>
        <div className="flex items-center gap-1">
          <button className="btn sm ghost" onClick={() => setParams({ month: monthShift(month, -1) }, { replace: true })}>←</button>
          <span className="num text-[13px] w-16 text-center">{month}</span>
          <button className="btn sm ghost" onClick={() => setParams({ month: monthShift(month, 1) }, { replace: true })}>→</button>
        </div>
      </>}
    >
      <div className="grid grid-cols-4 gap-3">
        <Stat label={`Income · ${month}`} value={<Money cents={cur?.income_cents ?? 0} />} />
        <Stat label={`Spending · ${month}`} value={<Money cents={-(cur?.spending_cents ?? 0)} />} sub={b ? `budgeted ${fmt(b.budget_total_cents)}` : ""} />
        <Stat label="Net" value={<Money cents={(cur?.income_cents ?? 0) - (cur?.spending_cents ?? 0)} />} tone={cur && cur.income_cents - cur.spending_cents < 0 ? "bad" : undefined} />
        <Stat label="Uncategorized spending" value={<Money cents={-(b?.uncategorized_cents ?? 0)} tone={false} />} sub={b && b.uncategorized_cents > 0 ? "not in any budget line yet" : "everything has a category"} tone={b && b.uncategorized_cents > 0 ? "warn" : undefined} />
      </div>
      <Card title={`Income vs spending · last ${months} months`}>
        <div className="h-64 p-2">
          {data.length ? (
            <ResponsiveContainer>
              <BarChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <CartesianGrid stroke="#1f2a3d" vertical={false} />
                <XAxis dataKey="month" stroke="#5b667a" fontSize={11} />
                <YAxis stroke="#5b667a" fontSize={11} tickFormatter={(v) => `$${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1).replace(/.0$/, "") + "k" : v}`} />
                <Tooltip contentStyle={{ background: "#151d2c", border: "1px solid #2a3850", fontSize: 12 }} formatter={(v) => `$${Number(v).toLocaleString()}`} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="income" fill="#34d399" radius={[3, 3, 0, 0]} isAnimationActive={false} />
                <Bar dataKey="spending" fill="#f87171" radius={[3, 3, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          ) : <Empty>no data yet</Empty>}
        </div>
      </Card>
      <div className="grid grid-cols-3 gap-4">
        <Card title={`Where it went · ${month}`}>
          <table className="tbl">
            <tbody>
              {(spend.data ?? []).map((s) => (
                <tr key={s.name}><td><Link to={s.id ? `/transactions?tab=all&category=${s.id}&frm=${month}-01&to=${monthShift(month, 1)}-01` : "/transactions?tab=all&uncategorized=1"} className="hover:text-accent">{s.name}</Link></td><td className="r num text-muted">{s.n}</td><td className="r"><Money cents={-s.cents} /></td></tr>
              ))}
              {spend.data && spend.data.length === 0 && <tr><td><Empty>nothing this month</Empty></td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title={`Budgets · ${month}`} className="col-span-2" right={<button className="btn sm" onClick={() => copy.mutate()}>copy last month's budgets</button>}>
          <table className="tbl">
            <thead><tr><th>Category</th><th className="r w-28">Budget</th><th className="r">Spent</th><th className="r">Last month</th><th className="w-48">Progress</th><th className="r">Left</th></tr></thead>
            <tbody>
              {[...groups.entries()].map(([parent, rs]) => [
                <tr key={parent + "h"} className="font-medium"><td colSpan={6} className="pt-3 text-muted text-[11px] uppercase tracking-wider">{parent}</td></tr>,
                ...rs.map((r) => {
                  const pct = r.budget_cents ? Math.min(100, (r.actual_cents / r.budget_cents) * 100) : 0;
                  const over = r.budget_cents !== null && r.actual_cents > r.budget_cents;
                  return (
                    <tr key={r.id}>
                      <td className="pl-4">{r.name}</td>
                      <td className="r">
                        <input className="inp num text-right w-28" value={edits[r.id] ?? (r.budget_cents !== null ? (r.budget_cents / 100).toFixed(0) : "")} placeholder="—"
                          onChange={(e) => setEdits({ ...edits, [r.id]: e.target.value })}
                          onBlur={() => { if (edits[r.id] === undefined) return; const c = edits[r.id].trim() === "" ? null : parse(edits[r.id]); if (c === null || !Number.isNaN(c)) save.mutate({ id: r.id, cents: c }); const n = { ...edits }; delete n[r.id]; setEdits(n); }}
                          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
                      </td>
                      <td className="r"><Money cents={-r.actual_cents} tone={false} /></td>
                      <td className="r text-muted"><Money cents={-r.prev_cents} tone={false} /></td>
                      <td>{r.budget_cents !== null && <div className="h-2 rounded bg-line overflow-hidden"><div className={`h-full ${over ? "bg-bad" : pct > 85 ? "bg-warn" : "bg-accent"}`} style={{ width: `${pct}%` }} /></div>}</td>
                      <td className="r">{r.remaining_cents !== null ? <span className={`num ${r.remaining_cents < 0 ? "text-bad" : "text-muted"}`}>{fmt(r.remaining_cents)}</span> : ""}</td>
                    </tr>
                  );
                }),
              ])}
            </tbody>
          </table>
          <div className="px-4 py-2 text-[11px] text-dim">Budgets cover personal spending only; business expenses live on the Business page. Blank = no budget for that line.</div>
        </Card>
      </div>
    </Page>
  );
}
