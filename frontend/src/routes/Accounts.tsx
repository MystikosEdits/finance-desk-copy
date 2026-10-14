import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { del, get, patch, post } from "../api/client";
import { useAccounts } from "../api/hooks";
import type { Account } from "../api/types";
import { Card, Empty, ErrorLine, Field, Modal, Money, Page, fmtDate } from "../components/ui";
import { parse } from "../lib/money";

const KINDS = ["checking", "savings", "credit", "cash", "brokerage", "crypto", "loan", "asset", "liability"];
const KIND_LABEL: Record<string, string> = { checking: "Checking", savings: "Savings", credit: "Credit card", cash: "Cash", brokerage: "Brokerage", crypto: "Crypto", loan: "Loan", asset: "Asset", liability: "Liability" };

export default function Accounts() {
  const qc = useQueryClient();
  const accounts = useAccounts(true);
  const [edit, setEdit] = useState<Partial<Account> | null>(null);
  const [balanceFor, setBalanceFor] = useState<Account | null>(null);
  const [deleting, setDeleting] = useState<Account | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const invalidate = () => { qc.invalidateQueries({ queryKey: ["accounts"] }); qc.invalidateQueries({ queryKey: ["txns"] }); qc.invalidateQueries({ queryKey: ["summary"] }); };

  const save = useMutation({
    mutationFn: (a: Partial<Account>) => (a.id ? patch(`/api/accounts/${a.id}`, a) : post("/api/accounts", a)),
    onSuccess: () => { setEdit(null); invalidate(); },
    onError: setErr,
  });
  const collectr = useMutation({ mutationFn: () => post("/api/collectr/account", { name: "Collectr" }), onSuccess: invalidate, onError: setErr });
  const setBalance = useMutation({
    mutationFn: ({ id, cents }: { id: number; cents: number }) => post(`/api/accounts/${id}/balance`, { balance_cents: cents }),
    onSuccess: () => { setBalanceFor(null); invalidate(); },
    onError: setErr,
  });

  const rows = accounts.data ?? [];
  const open = rows.filter((a) => !a.is_closed);
  // Signed sums. A liability account's balance is negative when money is owed,
  // so a card carrying a credit simply adds to net instead of being "owed".
  const assets = open.filter((a) => !a.is_liability).reduce((s, a) => s + (a.balance_cents ?? 0), 0);
  const liabilities = open.filter((a) => a.is_liability).reduce((s, a) => s + (a.balance_cents ?? 0), 0);
  const groups = [
    { title: "Cash", kinds: ["checking", "savings", "cash"] },
    { title: "Credit cards", kinds: ["credit"] },
    { title: "Investments", kinds: ["brokerage", "crypto"] },
    { title: "Loans & liabilities", kinds: ["loan", "liability"] },
    { title: "Other assets", kinds: ["asset"] },
  ];

  return (
    <Page
      title="Accounts"
      actions={
        <>
          <span className="text-[12px] text-muted num mr-2">
            assets <Money cents={assets} tone={false} /> · liabilities <Money cents={liabilities} tone={false} /> · net <Money cents={assets + liabilities} />
          </span>
          <Link to="/settings?tab=connections" className="btn">Connect Coinbase</Link>
          <button className="btn" onClick={() => collectr.mutate()} disabled={collectr.isPending}>+ Collectr</button>
          <button className="btn primary" onClick={() => setEdit({ kind: "checking", default_business: "personal" })}>+ account</button>
        </>
      }
    >
      <ErrorLine error={err} />
      {rows.length === 0 && (
        <div className="card">
          <Empty>
            No accounts yet. Connect SimpleFIN in <Link to="/settings?tab=connections" className="text-accent">Settings</Link> and they appear on the first sync, or add one by hand.
          </Empty>
        </div>
      )}
      {groups.map((g) => {
        const list = rows.filter((a) => g.kinds.includes(a.kind));
        if (!list.length) return null;
        return (
          <Card key={g.title} title={g.title}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Institution</th>
                  <th>Default</th>
                  <th className="r">Balance</th>
                  <th className="r">Ledger</th>
                  <th>As of</th>
                  <th className="r">Inbox</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.id} className={a.is_closed ? "opacity-50" : ""}>
                    <td>
                      <Link to={`/transactions?tab=all&account=${a.id}`} className="hover:text-accent">{a.name}</Link>
                      {a.simplefin_id ? <span className="chip ok ml-2">synced</span> : <span className="chip ml-2">manual</span>}
                      {a.is_closed ? <span className="chip ml-1">closed</span> : null}
                    </td>
                    <td className="text-muted">{a.institution}</td>
                    <td>{a.default_business === "business" ? <span className="chip biz">business</span> : a.default_business === "ask" ? <span className="chip warn">ask</span> : <span className="chip">personal</span>}</td>
                    <td className="r"><Money cents={a.balance_cents} tone={false} /></td>
                    <td className="r text-muted">
                      {a.computed_cents !== null && a.computed_cents !== undefined ? (
                        <span title="opening balance + all transactions; differs from the bank when history is incomplete" className={a.computed_cents !== a.balance_cents ? "text-warn" : ""}>
                          <Money cents={a.computed_cents} tone={false} />
                        </span>
                      ) : "—"}
                    </td>
                    <td className="text-muted num">{a.balance_date ? fmtDate(a.balance_date) : a.balance_source}</td>
                    <td className="r">{a.new_count > 0 ? <Link to={`/transactions?account=${a.id}`} className="chip warn">{a.new_count}</Link> : <span className="text-dim">0</span>}</td>
                    <td className="r whitespace-nowrap">
                      <button className="btn sm ghost" onClick={() => setBalanceFor(a)}>balance</button>
                      <button className="btn sm ghost" onClick={() => setEdit(a)}>edit</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        );
      })}

      {edit && (
        <Modal title={edit.id ? "Edit account" : "New account"} onClose={() => setEdit(null)}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name"><input className="inp" autoFocus value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Institution"><input className="inp" value={edit.institution ?? ""} onChange={(e) => setEdit({ ...edit, institution: e.target.value })} /></Field>
            <Field label="Kind">
              <select className="inp" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>
                {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </select>
            </Field>
            <Field label="New transactions default to" hint="'business' for the LLC account; 'ask' leaves them untagged">
              <select className="inp" value={edit.default_business ?? "personal"} onChange={(e) => setEdit({ ...edit, default_business: e.target.value as Account["default_business"] })}>
                <option value="personal">personal</option>
                <option value="business">business</option>
                <option value="ask">ask each time</option>
              </select>
            </Field>
            <Field label="Opening balance" hint="what the account held before the first transaction here">
              <input className="inp num" value={edit.opening_balance_cents !== undefined ? (edit.opening_balance_cents / 100).toFixed(2) : "0.00"} onChange={(e) => { const c = parse(e.target.value); if (!Number.isNaN(c)) setEdit({ ...edit, opening_balance_cents: c }); }} />
            </Field>
            <Field label="Opening date"><input className="inp num" type="date" value={edit.opening_date ?? ""} onChange={(e) => setEdit({ ...edit, opening_date: e.target.value || null })} /></Field>
            {(edit.kind === "credit" || edit.kind === "loan" || edit.kind === "liability") && (
              <>
                <Field label="APR %"><input className="inp num" value={edit.apr_bp !== null && edit.apr_bp !== undefined ? (edit.apr_bp / 100).toString() : ""} onChange={(e) => setEdit({ ...edit, apr_bp: e.target.value ? Math.round(parseFloat(e.target.value) * 100) : null })} /></Field>
                <Field label="Min payment $"><input className="inp num" value={edit.min_payment_cents ? (edit.min_payment_cents / 100).toFixed(2) : ""} onChange={(e) => { const c = parse(e.target.value); setEdit({ ...edit, min_payment_cents: Number.isNaN(c) ? null : c }); }} /></Field>
                <Field label="Due day of month"><input className="inp num" value={edit.due_day ?? ""} onChange={(e) => setEdit({ ...edit, due_day: e.target.value ? parseInt(e.target.value, 10) : null })} /></Field>
                <Field label="Balance sign" hint="flip if the bank reports what you owe as a positive number">
                  <select className="inp" value={edit.balance_sign ?? 1} onChange={(e) => setEdit({ ...edit, balance_sign: parseInt(e.target.value, 10) })}>
                    <option value={1}>as reported</option>
                    <option value={-1}>flipped</option>
                  </select>
                </Field>
              </>
            )}
            {(edit.kind === "asset" || edit.kind === "liability") && (
              <Field label="Current value $"><input className="inp num" value={edit.manual_value_cents !== null && edit.manual_value_cents !== undefined ? (edit.manual_value_cents / 100).toFixed(2) : ""} onChange={(e) => { const c = parse(e.target.value); setEdit({ ...edit, manual_value_cents: Number.isNaN(c) ? null : c }); }} /></Field>
            )}
            {edit.id && (
              <Field label="Closed">
                <select className="inp" value={edit.is_closed ?? 0} onChange={(e) => setEdit({ ...edit, is_closed: parseInt(e.target.value, 10) })}>
                  <option value={0}>open</option>
                  <option value={1}>closed</option>
                </select>
              </Field>
            )}
          </div>
          <div className="flex items-center gap-2 mt-4">
            {edit.id && (
              <button className="btn danger sm" onClick={() => { setDeleting(edit as Account); setEdit(null); }}>Delete account…</button>
            )}
            <span className="ml-auto flex gap-2">
              <button className="btn ghost" onClick={() => setEdit(null)}>cancel</button>
              <button className="btn primary" disabled={!edit.name} onClick={() => save.mutate(edit)}>Save</button>
            </span>
          </div>
        </Modal>
      )}
      {deleting && <DeleteModal a={deleting} onClose={() => setDeleting(null)} onDone={() => { setDeleting(null); invalidate(); }} />}
      {balanceFor && (
        <BalanceModal a={balanceFor} onClose={() => setBalanceFor(null)} onSave={(cents) => setBalance.mutate({ id: balanceFor.id, cents })} />
      )}
    </Page>
  );
}

type Footprint = { name: string; synced: boolean; transactions: number; reviewed: number; attachments: number; trades: number; transfers: number };

function DeleteModal({ a, onClose, onDone }: { a: Account; onClose: () => void; onDone: () => void }) {
  const fp = useQuery({ queryKey: ["footprint", a.id], queryFn: () => get<Footprint>(`/api/accounts/${a.id}/footprint`) });
  const [typed, setTyped] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const remove = useMutation({ mutationFn: () => del(`/api/accounts/${a.id}?confirm=${encodeURIComponent(typed)}`), onSuccess: onDone, onError: setErr });
  const f = fp.data;
  return (
    <Modal title={`Delete ${a.name}`} onClose={onClose} width="max-w-md">
      <div className="space-y-3 text-[12.5px]">
        <p>This removes the account and everything in it. There is no undo except restoring last night's backup.</p>
        {f && (
          <ul className="card p-3 space-y-1">
            <li><b className="num">{f.transactions}</b> transactions ({f.reviewed} reviewed), with their splits and tags</li>
            <li><b className="num">{f.attachments}</b> receipts, files included</li>
            <li><b className="num">{f.transfers}</b> transfer links (the other side stays, unlinked)</li>
            {f.trades > 0 && <li><b className="num">{f.trades}</b> trades</li>}
            {f.synced && <li className="text-warn">This account comes from SimpleFIN. It will not be recreated on the next sync; to bring it back later, remove its id from Settings › ignored_simplefin_ids. If you only want it out of the way, mark it closed instead.</li>}
          </ul>
        )}
        <Field label={`Type the account name to confirm: ${a.name}`}>
          <input className="inp" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
        <ErrorLine error={err} />
        <div className="flex justify-end gap-2">
          <button className="btn ghost" onClick={onClose}>cancel</button>
          <button className="btn danger" disabled={typed.trim() !== a.name || remove.isPending} onClick={() => remove.mutate()}>Delete for good</button>
        </div>
      </div>
    </Modal>
  );
}

function BalanceModal({ a, onClose, onSave }: { a: Account; onClose: () => void; onSave: (cents: number) => void }) {
  const [v, setV] = useState(((a.balance_cents ?? 0) / 100).toFixed(2));
  const c = parse(v);
  return (
    <Modal title={`Balance · ${a.name}`} onClose={onClose} width="max-w-sm">
      <Field label="Balance today" hint={a.is_liability ? "what is owed, as a negative number" : "what the account holds"}>
        <input className="inp num" autoFocus value={v} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !Number.isNaN(c)) onSave(c); }} />
      </Field>
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn ghost" onClick={onClose}>cancel</button>
        <button className="btn primary" disabled={Number.isNaN(c)} onClick={() => onSave(c)}>Save</button>
      </div>
    </Modal>
  );
}
