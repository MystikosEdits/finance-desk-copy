import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api/client";
import { Card, Empty, Money, Page } from "../components/ui";
import { fmt } from "../lib/money";

type Spend = { name: string; id: number | null; cents: number; n: number };
type Income = { name: string; id: number | null; business: number; cents: number; n: number };
type Debt = { id: number; name: string; kind: string; owed_cents: number; apr_bp: number | null; min_payment_cents: number | null; due_day: number | null; months_to_payoff: number | null; interest_to_payoff_cents: number | null };

export default function Reports() {
  const year = new Date().getFullYear();
  const [frm, setFrm] = useState(`${year}-01-01`);
  const [to, setTo] = useState(new Date().toISOString().slice(0, 10));
  const [by, setBy] = useState<"category" | "parent" | "payee">("parent");
  const [scope, setScope] = useState<"all" | "0" | "1">("all");
  const spend = useQuery({ queryKey: ["rep-spend", frm, to, by, scope], queryFn: () => get<Spend[]>(`/api/reports/spending?frm=${frm}&to=${to}&by=${by}&business=${scope}&limit=60`) });
  const income = useQuery({ queryKey: ["rep-income", frm, to], queryFn: () => get<Income[]>(`/api/reports/income?frm=${frm}&to=${to}`) });
  const debts = useQuery({ queryKey: ["rep-debts"], queryFn: () => get<Debt[]>("/api/reports/debts") });
  const total = (spend.data ?? []).reduce((s, r) => s + r.cents, 0);
  const incTotal = (income.data ?? []).reduce((s, r) => s + r.cents, 0);

  return (
    <Page title="Reports" actions={<>
      <input className="inp num w-36" type="date" value={frm} onChange={(e) => setFrm(e.target.value)} />
      <input className="inp num w-36" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
      <div className="flex gap-1">{(["all", "0", "1"] as const).map((s) => <button key={s} className={`btn sm ${scope === s ? "primary" : "ghost"}`} onClick={() => setScope(s)}>{s === "all" ? "both" : s === "0" ? "personal" : "business"}</button>)}</div>
      <div className="flex gap-1">{(["parent", "category", "payee"] as const).map((b) => <button key={b} className={`btn sm ${by === b ? "primary" : "ghost"}`} onClick={() => setBy(b)}>{b === "parent" ? "group" : b}</button>)}</div>
      <a className="btn sm" href={`/api/reports/export.csv?frm=${frm}&to=${to}${scope !== "all" ? `&business=${scope}` : ""}`}>Export CSV</a>
    </>}>
      <div className="grid grid-cols-3 gap-4">
        <Card title={`Spending · ${fmt(total)}`} className="col-span-1">
          <table className="tbl">
            <tbody>
              {(spend.data ?? []).map((r) => (
                <tr key={`${r.id}-${r.name}`}>
                  <td className="min-w-0"><Link to={by === "payee" ? `/transactions?tab=all&payee=${r.id ?? ""}&frm=${frm}&to=${to}` : `/transactions?tab=all&category=${r.id ?? ""}&frm=${frm}&to=${to}`} className="hover:text-accent">{r.name}</Link></td>
                  <td className="w-40"><div className="h-1.5 rounded bg-line overflow-hidden"><div className="h-full bg-biz" style={{ width: `${total ? (r.cents / total) * 100 : 0}%` }} /></div></td>
                  <td className="r num text-muted">{total ? ((r.cents / total) * 100).toFixed(0) : 0}%</td>
                  <td className="r"><Money cents={-r.cents} tone={false} /></td>
                </tr>
              ))}
              {spend.data && spend.data.length === 0 && <tr><td><Empty>nothing in range</Empty></td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title={`Income · ${fmt(incTotal)}`}>
          <table className="tbl">
            <tbody>
              {(income.data ?? []).map((r, i) => <tr key={i}><td>{r.name}{r.business ? <span className="chip biz ml-1">biz</span> : null}</td><td className="r num text-muted">{r.n}</td><td className="r"><Money cents={r.cents} /></td></tr>)}
              {income.data && income.data.length === 0 && <tr><td><Empty>no income in range</Empty></td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title="Debts">
          <table className="tbl">
            <thead><tr><th>Account</th><th className="r">Owed</th><th className="r">APR</th><th className="r">Min</th><th className="r">Payoff</th></tr></thead>
            <tbody>
              {(debts.data ?? []).map((d) => (
                <tr key={d.id}><td>{d.name}</td><td className="r"><Money cents={-d.owed_cents} tone={false} /></td><td className="r num text-muted">{d.apr_bp !== null ? `${(d.apr_bp / 100).toFixed(2)}%` : "—"}</td><td className="r num text-muted">{d.min_payment_cents ? fmt(d.min_payment_cents) : "—"}</td>
                  <td className="r text-[11px]">{d.months_to_payoff !== null ? <span>{d.months_to_payoff} mo <span className="text-muted">· {fmt(d.interest_to_payoff_cents ?? 0)} interest</span></span> : d.owed_cents ? <span className="text-dim">set APR + min on the account</span> : <span className="text-dim">paid off</span>}</td></tr>
              ))}
              {debts.data && debts.data.length === 0 && <tr><td><Empty>no debt accounts</Empty></td></tr>}
            </tbody>
          </table>
        </Card>
      </div>
    </Page>
  );
}
