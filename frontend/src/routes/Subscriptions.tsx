import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get, patch, post } from "../api/client";
import { Card, Empty, ErrorLine, Field, Modal, Money, Page, Stat, fmtDate } from "../components/ui";
import { fmt, parse } from "../lib/money";

type Rec = { id: number; payee_id: number | null; name: string; cadence: string; expected_cents: number; next_date: string | null; is_subscription: number; business: number; active: number; status: string; last_seen_date: string | null; category_name: string | null; missed: boolean; monthly_equivalent_cents: number };
type List = { rows: Rec[]; monthly_total_cents: number; monthly_business_cents: number; suggested: number; missed: number };

export default function Subscriptions() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["recurring"], queryFn: () => get<List>("/api/recurring") });
  const [err, setErr] = useState<unknown>(null);
  const [adding, setAdding] = useState(false);
  const inval = () => { qc.invalidateQueries({ queryKey: ["recurring"] }); qc.invalidateQueries({ queryKey: ["summary"] }); };
  const detect = useMutation({ mutationFn: () => post("/api/recurring/detect"), onSuccess: inval, onError: setErr });
  const act = useMutation({ mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => patch(`/api/recurring/${id}`, body), onSuccess: inval, onError: setErr });
  const d = q.data;
  const suggested = (d?.rows ?? []).filter((r) => r.status === "suggested");
  const confirmed = (d?.rows ?? []).filter((r) => r.status === "confirmed");

  return (
    <Page title="Subscriptions" actions={<>
      <button className="btn sm" onClick={() => detect.mutate()} disabled={detect.isPending}>Scan for recurring charges</button>
      <button className="btn sm primary" onClick={() => setAdding(true)}>+ add by hand</button>
    </>}>
      <ErrorLine error={err} />
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Monthly, all confirmed" value={<Money cents={-(d?.monthly_total_cents ?? 0)} tone={false} />} sub="yearly and quarterly spread per month" />
        <Stat label="Of which business" value={<Money cents={-(d?.monthly_business_cents ?? 0)} tone={false} />} sub={d ? `${fmt((d.monthly_total_cents - d.monthly_business_cents))} personal` : ""} />
        <Stat label="Per year" value={<Money cents={-((d?.monthly_total_cents ?? 0) * 12)} tone={false} />} />
        <Stat label="Missed" value={<span className="num">{d?.missed ?? 0}</span>} sub="confirmed charges past their date" tone={d && d.missed ? "warn" : undefined} />
      </div>
      {suggested.length > 0 && (
        <Card title={`Found in the ledger · ${suggested.length}`}>
          <table className="tbl">
            <thead><tr><th>Name</th><th>Cadence</th><th className="r">Amount</th><th>Next</th><th>Last seen</th><th>Category</th><th></th></tr></thead>
            <tbody>
              {suggested.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}{r.business ? <span className="chip biz ml-1">biz</span> : null}</td><td className="text-muted">{r.cadence}</td><td className="r"><Money cents={-r.expected_cents} tone={false} /></td>
                  <td className="num text-muted">{fmtDate(r.next_date)}</td><td className="num text-muted">{fmtDate(r.last_seen_date)}</td><td className="text-muted">{r.category_name ?? "—"}</td>
                  <td className="r whitespace-nowrap">
                    <button className="btn sm primary" onClick={() => act.mutate({ id: r.id, body: { status: "confirmed" } })}>yes, recurring</button>
                    <button className="btn sm ghost" onClick={() => act.mutate({ id: r.id, body: { status: "dismissed" } })}>no</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <Card title="Confirmed">
        <table className="tbl">
          <thead><tr><th>Name</th><th>Cadence</th><th className="r">Amount</th><th className="r">Per month</th><th>Next</th><th>Last seen</th><th>Type</th><th></th></tr></thead>
          <tbody>
            {confirmed.map((r) => (
              <tr key={r.id} className={r.active ? "" : "opacity-50"}>
                <td>{r.payee_id ? <Link to={`/transactions?tab=all&payee=${r.payee_id}`} className="hover:text-accent">{r.name}</Link> : r.name}{r.business ? <span className="chip biz ml-1">biz</span> : null}{r.missed ? <span className="chip warn ml-1">missed</span> : null}{!r.active ? <span className="chip ml-1">paused</span> : null}</td>
                <td className="text-muted">{r.cadence}</td><td className="r"><Money cents={-r.expected_cents} tone={false} /></td><td className="r text-muted"><Money cents={-r.monthly_equivalent_cents} tone={false} /></td>
                <td className="num text-muted">{fmtDate(r.next_date)}</td><td className="num text-muted">{fmtDate(r.last_seen_date)}</td>
                <td><button className="chip" onClick={() => act.mutate({ id: r.id, body: { is_subscription: r.is_subscription ? 0 : 1 } })}>{r.is_subscription ? "subscription" : "bill"}</button></td>
                <td className="r whitespace-nowrap">
                  <button className="btn sm ghost" onClick={() => act.mutate({ id: r.id, body: { business: r.business ? 0 : 1 } })}>{r.business ? "→ personal" : "→ business"}</button>
                  <button className="btn sm ghost" onClick={() => act.mutate({ id: r.id, body: { active: r.active ? 0 : 1 } })}>{r.active ? "cancelled" : "resume"}</button>
                  <button className="btn sm ghost" onClick={() => act.mutate({ id: r.id, body: { status: "dismissed" } })}>remove</button>
                </td>
              </tr>
            ))}
            {d && confirmed.length === 0 && <tr><td colSpan={8}><Empty>Nothing confirmed yet. Scan the ledger, then confirm what is real.</Empty></td></tr>}
          </tbody>
        </table>
      </Card>
      {adding && <AddModal onClose={() => setAdding(false)} onDone={() => { setAdding(false); inval(); }} />}
    </Page>
  );
}

function AddModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [d, setD] = useState({ name: "", cadence: "monthly", amount: "", next_date: new Date().toISOString().slice(0, 10), business: 0 });
  const [err, setErr] = useState<unknown>(null);
  const save = useMutation({ mutationFn: () => post("/api/recurring", { name: d.name, cadence: d.cadence, expected_cents: Math.abs(parse(d.amount)), next_date: d.next_date, business: d.business }), onSuccess: onDone, onError: setErr });
  return (
    <Modal title="Add a recurring charge" onClose={onClose} width="max-w-md">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name"><input className="inp" autoFocus value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></Field>
        <Field label="Cadence"><select className="inp" value={d.cadence} onChange={(e) => setD({ ...d, cadence: e.target.value })}>{["weekly", "biweekly", "monthly", "quarterly", "yearly"].map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>
        <Field label="Amount"><input className="inp num" value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} /></Field>
        <Field label="Next date"><input className="inp num" type="date" value={d.next_date} onChange={(e) => setD({ ...d, next_date: e.target.value })} /></Field>
        <Field label="Business"><select className="inp" value={d.business} onChange={(e) => setD({ ...d, business: parseInt(e.target.value, 10) })}><option value={0}>personal</option><option value={1}>business</option></select></Field>
      </div>
      <ErrorLine error={err} />
      <div className="flex justify-end gap-2 mt-4"><button className="btn ghost" onClick={onClose}>cancel</button><button className="btn primary" disabled={!d.name || Number.isNaN(parse(d.amount)) || !d.amount} onClick={() => save.mutate()}>Add</button></div>
    </Modal>
  );
}
