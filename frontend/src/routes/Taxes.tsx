import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, get, post, put } from "../api/client";
import { Card, Empty, ErrorLine, Field, Money, Page, Stat, fmtDate } from "../components/ui";
import { fmt, parse } from "../lib/money";

type Settings = { year: number; filing_status: string; state: string; state_flat_rate_bp: number; prior_year_tax_cents: number; prior_year_agi_cents: number; w2_wages_cents: number; w2_withholding_cents: number; other_income_cents: number; itemized_cents: number | null; notes: string };
type Quarter = { q: number; due: string; covers_through: string; federal_target_cents: number; federal_cumulative_target_cents: number; federal_paid_cents: number; state_paid_cents: number; federal_cumulative_paid_cents: number };
type Estimate = {
  rates_version: string; rates_verified: boolean; rates_fallback: boolean;
  net_profit_cents: number; business_income_cents: number; business_expenses_cents: number; home_office_cents: number; mileage_cents: number;
  se_tax_cents: number; half_se_deduction_cents: number; agi_cents: number; deduction_cents: number; deduction_kind: string; qbi_deduction_cents: number;
  taxable_income_cents: number; federal_income_tax_cents: number; additional_medicare_cents: number; federal_total_cents: number; state_tax_cents: number; total_tax_cents: number;
  withholding_cents: number; payments_federal_cents: number; payments_state_cents: number; balance_federal_cents: number; balance_state_cents: number; set_aside_cents: number;
  effective_rate: number; marginal_rate_pct: number; year_fraction: number; projected_total_tax_cents: number; unassigned_line_cents: number;
  safe_harbor: { prior_year_target_cents: number | null; current_year_target_cents: number; annual_target_cents: number; required_after_withholding_cents: number; high_agi: boolean };
  quarters: Quarter[];
};
type Payment = { id: number; quarter: number; jurisdiction: string; amount_cents: number; date: string; confirmation: string; txn_id: number | null };

export default function Taxes() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const year = parseInt(params.get("year") ?? String(new Date().getFullYear()), 10);
  const q = useQuery({ queryKey: ["tax", year], queryFn: () => get<{ settings: Settings; estimate: Estimate }>(`/api/tax/${year}`) });
  const payments = useQuery({ queryKey: ["tax-payments", year], queryFn: () => get<Payment[]>(`/api/tax/${year}/payments`) });
  const [s, setS] = useState<Settings | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { if (q.data) setS(q.data.settings); }, [q.data]);
  const inval = () => { qc.invalidateQueries({ queryKey: ["tax"] }); qc.invalidateQueries({ queryKey: ["tax-payments"] }); qc.invalidateQueries({ queryKey: ["summary"] }); };
  const save = useMutation({ mutationFn: (body: Partial<Settings>) => put(`/api/tax/${year}/settings`, body), onSuccess: inval, onError: setErr });
  const addPay = useMutation({ mutationFn: (body: Record<string, unknown>) => post(`/api/tax/${year}/payments`, body), onSuccess: inval, onError: setErr });
  const delPay = useMutation({ mutationFn: (id: number) => del(`/api/tax/${year}/payments/${id}`), onSuccess: inval, onError: setErr });
  const e = q.data?.estimate;
  const [pay, setPay] = useState({ quarter: "1", jurisdiction: "federal", amount: "", date: new Date().toISOString().slice(0, 10), confirmation: "" });
  const years = [year - 2, year - 1, year, year + 1].filter((y) => y <= new Date().getFullYear() + 1);

  return (
    <Page
      title="Taxes"
      actions={
        <>
          <select className="inp w-24 num" value={year} onChange={(ev) => setParams({ year: ev.target.value }, { replace: true })}>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <a className="btn" href={`/api/tax/${year}/export.zip`}>Accountant packet (.zip)</a>
        </>
      }
    >
      <ErrorLine error={err} />
      {e && (
        <div className="card px-4 py-2 text-[12px] flex flex-wrap items-center gap-3">
          <span className="chip warn">estimate</span>
          <span>rates file <span className="num">{e.rates_version}</span>{e.rates_fallback ? " (fallback — no file for this year)" : ""}{!e.rates_verified ? " · not yet verified with the accountant" : ""}</span>
          {e.unassigned_line_cents > 0 && <span className="text-warn">{fmt(e.unassigned_line_cents)} of business expenses have no Schedule C line</span>}
          <span className="ml-auto text-muted">covers {Math.round(e.year_fraction * 100)}% of the year · projected full-year total <Money cents={e.projected_total_tax_cents} tone={false} /></span>
        </div>
      )}
      <div className="grid grid-cols-5 gap-3">
        <Stat label="Set aside now" value={<Money cents={e?.set_aside_cents} tone={false} />} sub="liability so far − withholding − payments" tone={e && e.set_aside_cents > 0 ? "warn" : undefined} />
        <Stat label="Net profit YTD" value={<Money cents={e?.net_profit_cents} />} sub="Schedule C" />
        <Stat label="SE tax" value={<Money cents={e?.se_tax_cents} tone={false} />} sub={e ? `on ${fmt(e.net_profit_cents)} × 92.35%` : ""} />
        <Stat label="Federal income tax" value={<Money cents={e?.federal_income_tax_cents} tone={false} />} sub={e ? `marginal ${e.marginal_rate_pct}% · effective ${(e.effective_rate * 100).toFixed(1)}%` : ""} />
        <Stat label="Total tax" value={<Money cents={e?.total_tax_cents} tone={false} />} sub={e ? `federal ${fmt(e.federal_total_cents)} + state ${fmt(e.state_tax_cents)}` : ""} />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <Card title="The math" className="col-span-1">
          {e ? (
            <table className="tbl">
              <tbody>
                <Row k="Business income" v={e.business_income_cents} />
                <Row k="− deductible expenses" v={-e.business_expenses_cents} />
                <Row k="− home office, mileage" v={-(e.home_office_cents + e.mileage_cents)} />
                <Row k="= Net profit" v={e.net_profit_cents} bold />
                <Row k="SE tax (12.4% + 2.9%)" v={e.se_tax_cents} />
                <Row k="− ½ SE deduction" v={-e.half_se_deduction_cents} />
                <Row k="+ W-2 wages, other income" v={(s?.w2_wages_cents ?? 0) + (s?.other_income_cents ?? 0)} />
                <Row k="= AGI" v={e.agi_cents} bold />
                <Row k={`− ${e.deduction_kind} deduction`} v={-e.deduction_cents} />
                <Row k="− QBI (20%)" v={-e.qbi_deduction_cents} />
                <Row k="= Taxable income" v={e.taxable_income_cents} bold />
                <Row k="Federal income tax" v={e.federal_income_tax_cents} />
                {e.additional_medicare_cents > 0 && <Row k="Additional Medicare 0.9%" v={e.additional_medicare_cents} />}
                <Row k="+ SE tax" v={e.se_tax_cents} />
                <Row k="= Federal total" v={e.federal_total_cents} bold />
                <Row k="State (flat)" v={e.state_tax_cents} />
                <Row k="− withholding" v={-e.withholding_cents} />
                <Row k="− estimated payments" v={-(e.payments_federal_cents + e.payments_state_cents)} />
                <Row k="= Owed so far" v={e.balance_federal_cents + e.balance_state_cents} bold />
              </tbody>
            </table>
          ) : <Empty>…</Empty>}
        </Card>
        <Card title="Quarterly estimates" className="col-span-2" right={e && <span className="text-[11px] text-muted">safe harbor: {e.safe_harbor.prior_year_target_cents !== null ? `${e.safe_harbor.high_agi ? "110" : "100"}% of last year = ${fmt(e.safe_harbor.prior_year_target_cents)}` : "no prior-year tax entered"} · 90% of this year = {fmt(e.safe_harbor.current_year_target_cents)}</span>}>
          <table className="tbl">
            <thead><tr><th>Q</th><th>Due</th><th>Covers through</th><th className="r">Target</th><th className="r">Cumulative target</th><th className="r">Paid</th><th className="r">Cumulative paid</th><th></th></tr></thead>
            <tbody>
              {(e?.quarters ?? []).map((qq) => {
                const behind = qq.federal_cumulative_paid_cents < qq.federal_cumulative_target_cents && new Date(qq.due) < new Date();
                return (
                  <tr key={qq.q} className={behind ? "text-warn" : ""}>
                    <td className="num">Q{qq.q}</td><td className="num">{fmtDate(qq.due)}</td><td className="num text-muted">{fmtDate(qq.covers_through)}</td>
                    <td className="r"><Money cents={qq.federal_target_cents} tone={false} /></td>
                    <td className="r"><Money cents={qq.federal_cumulative_target_cents} tone={false} /></td>
                    <td className="r"><Money cents={qq.federal_paid_cents} tone={false} />{qq.state_paid_cents ? <span className="text-muted text-[11px]"> + {fmt(qq.state_paid_cents)} state</span> : null}</td>
                    <td className="r"><Money cents={qq.federal_cumulative_paid_cents} tone={false} /></td>
                    <td>{behind ? <span className="chip warn">behind</span> : qq.federal_cumulative_paid_cents >= qq.federal_cumulative_target_cents && qq.federal_cumulative_target_cents > 0 ? <span className="chip ok">covered</span> : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="px-4 py-2 text-[11px] text-dim">Targets are the safe-harbor amount less withholding, in four equal parts. Paying the safe harbor avoids the underpayment penalty even if the year ends higher; the difference is due in April.</div>
        </Card>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Card title={`Inputs · ${year}`}>
          {s && (
            <div className="p-4 grid grid-cols-3 gap-3">
              <Field label="Filing status">
                <select className="inp" value={s.filing_status} onChange={(ev) => setS({ ...s, filing_status: ev.target.value })}>
                  <option value="single">single</option><option value="mfj">married filing jointly</option><option value="mfs">married filing separately</option><option value="hoh">head of household</option>
                </select>
              </Field>
              <Field label="State"><input className="inp" value={s.state} onChange={(ev) => setS({ ...s, state: ev.target.value })} placeholder="e.g. CA" /></Field>
              <Field label="State flat rate %" hint="rough; states have brackets"><input className="inp num" value={s.state_flat_rate_bp / 100} onChange={(ev) => setS({ ...s, state_flat_rate_bp: Math.round(parseFloat(ev.target.value || "0") * 100) })} /></Field>
              <MoneyField label="Last year's total tax" hint="1040 line 24, for safe harbor" v={s.prior_year_tax_cents} on={(c) => setS({ ...s, prior_year_tax_cents: c })} />
              <MoneyField label="Last year's AGI" v={s.prior_year_agi_cents} on={(c) => setS({ ...s, prior_year_agi_cents: c })} />
              <MoneyField label="W-2 wages this year" v={s.w2_wages_cents} on={(c) => setS({ ...s, w2_wages_cents: c })} />
              <MoneyField label="W-2 withholding" v={s.w2_withholding_cents} on={(c) => setS({ ...s, w2_withholding_cents: c })} />
              <MoneyField label="Other income" hint="interest, etc." v={s.other_income_cents} on={(c) => setS({ ...s, other_income_cents: c })} />
              <MoneyField label="Itemized deductions" hint="blank = standard" v={s.itemized_cents ?? 0} on={(c) => setS({ ...s, itemized_cents: c || null })} />
              <div className="col-span-3 flex items-end gap-2">
                <Field label="Notes"><input className="inp w-96" value={s.notes} onChange={(ev) => setS({ ...s, notes: ev.target.value })} /></Field>
                <button className="btn primary" onClick={() => save.mutate({ ...s, itemized_cents: s.itemized_cents ?? undefined, ...(s.itemized_cents ? {} : { clear_itemized: true } as unknown as Partial<Settings>) })}>Save</button>
              </div>
            </div>
          )}
        </Card>
        <Card title="Estimated payments made">
          <table className="tbl">
            <thead><tr><th>Date</th><th>Q</th><th>To</th><th className="r">Amount</th><th>Confirmation</th><th></th></tr></thead>
            <tbody>
              {(payments.data ?? []).map((p) => (
                <tr key={p.id}><td className="num text-muted">{fmtDate(p.date)}</td><td className="num">Q{p.quarter}</td><td>{p.jurisdiction}</td><td className="r"><Money cents={p.amount_cents} tone={false} /></td><td className="text-muted num text-[11px]">{p.confirmation}</td>
                  <td className="r"><button className="btn sm ghost" onClick={() => delPay.mutate(p.id)}>delete</button></td></tr>
              ))}
            </tbody>
          </table>
          <div className="p-4 flex flex-wrap items-end gap-2 border-t border-line">
            <Field label="Q"><select className="inp w-16" value={pay.quarter} onChange={(ev) => setPay({ ...pay, quarter: ev.target.value })}>{[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}</select></Field>
            <Field label="To"><select className="inp w-28" value={pay.jurisdiction} onChange={(ev) => setPay({ ...pay, jurisdiction: ev.target.value })}><option value="federal">federal</option><option value="state">state</option></select></Field>
            <Field label="Amount"><input className="inp num w-28" value={pay.amount} onChange={(ev) => setPay({ ...pay, amount: ev.target.value })} /></Field>
            <Field label="Date"><input className="inp num w-36" type="date" value={pay.date} onChange={(ev) => setPay({ ...pay, date: ev.target.value })} /></Field>
            <Field label="Confirmation"><input className="inp w-32" value={pay.confirmation} onChange={(ev) => setPay({ ...pay, confirmation: ev.target.value })} /></Field>
            <button className="btn primary" disabled={Number.isNaN(parse(pay.amount)) || !pay.amount} onClick={() => addPay.mutate({ quarter: parseInt(pay.quarter, 10), jurisdiction: pay.jurisdiction, amount_cents: parse(pay.amount), date: pay.date, confirmation: pay.confirmation })}>Record</button>
          </div>
          <div className="px-4 pb-3 text-[11px] text-dim">Tip: categorise the bank transaction as "Estimated tax payment" too, so it never counts as spending.</div>
        </Card>
      </div>
    </Page>
  );
}

function Row({ k, v, bold }: { k: string; v: number; bold?: boolean }) {
  return (
    <tr className={bold ? "font-medium" : ""}>
      <td className={bold ? "" : "text-muted"}>{k}</td>
      <td className="r"><Money cents={v} tone={false} /></td>
    </tr>
  );
}

function MoneyField({ label, hint, v, on }: { label: string; hint?: string; v: number; on: (c: number) => void }) {
  const [t, setT] = useState((v / 100).toFixed(2));
  useEffect(() => setT((v / 100).toFixed(2)), [v]);
  return (
    <Field label={label} hint={hint}>
      <input className="inp num" value={t} onChange={(ev) => setT(ev.target.value)} onBlur={() => { const c = parse(t); if (!Number.isNaN(c)) on(c); }} />
    </Field>
  );
}
