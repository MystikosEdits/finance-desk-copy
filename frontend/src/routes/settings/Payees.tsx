import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { patch, post } from "../../api/client";
import { useCategories, usePayees } from "../../api/hooks";
import type { Payee } from "../../api/types";
import CategoryPicker from "../../components/CategoryPicker";
import { Card, ErrorLine, Field, Modal, Money, fmtDate } from "../../components/ui";

export default function Payees() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const payees = usePayees(q);
  const cats = useCategories();
  const [edit, setEdit] = useState<Payee | null>(null);
  const [mergeInto, setMergeInto] = useState("");
  const [pick, setPick] = useState(false);
  const [applyExisting, setApplyExisting] = useState(true);
  const [err, setErr] = useState<unknown>(null);
  const inval = () => { qc.invalidateQueries({ queryKey: ["payees"] }); qc.invalidateQueries({ queryKey: ["txns"] }); };
  const save = useMutation({
    mutationFn: (p: Payee) => patch<Payee & { applied: number }>(`/api/payees/${p.id}`, {
      name: p.name, default_category_id: p.default_category_id, clear_default_category: !p.default_category_id,
      default_business: p.default_business, clear_default_business: p.default_business === null,
      is_contractor: p.is_contractor, contractor_kind: p.contractor_kind, tin_on_file: p.tin_on_file, w9_on_file: p.w9_on_file, contact: p.contact,
      apply_to_existing: applyExisting,
    }),
    onSuccess: () => { setEdit(null); inval(); },
    onError: setErr,
  });
  const merge = useMutation({ mutationFn: ({ from, into }: { from: number; into: number }) => post(`/api/payees/${from}/merge-into/${into}`), onSuccess: () => { setEdit(null); setMergeInto(""); inval(); }, onError: setErr });
  const catName = (id: number | null) => (id ? cats.data?.find((c) => c.id === id)?.full_name ?? "?" : null);

  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <div className="flex items-center gap-3">
        <input className="inp w-64" placeholder="search payees…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="text-[12px] text-muted">A payee's default category and business flag apply to its future transactions, and to existing untouched ones if you tick the box.</span>
      </div>
      <Card>
        <table className="tbl">
          <thead><tr><th>Payee</th><th>Default category</th><th>Default</th><th>Contractor</th><th className="r">Txns</th><th className="r">Total</th><th>Last</th><th></th></tr></thead>
          <tbody>
            {(payees.data ?? []).map((p) => (
              <tr key={p.id}>
                <td><Link to={`/transactions?tab=all&payee=${p.id}`} className="hover:text-accent">{p.name}</Link><div className="text-[10px] text-dim num">{p.normalized}</div></td>
                <td className="text-muted">{p.default_category_name ?? "—"}</td>
                <td>{p.default_business === 1 ? <span className="chip biz">business</span> : p.default_business === 0 ? <span className="chip">personal</span> : <span className="text-dim">—</span>}</td>
                <td>{p.is_contractor ? <span className="chip info">{p.contractor_kind || "contractor"}{!p.w9_on_file ? " · no W-9" : ""}</span> : null}</td>
                <td className="r num">{p.txn_count}</td>
                <td className="r"><Money cents={p.total_cents ?? 0} /></td>
                <td className="text-muted num">{p.last_date ? fmtDate(p.last_date) : ""}</td>
                <td className="r"><button className="btn sm ghost" onClick={() => setEdit(p)}>edit</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {edit && (
        <Modal title={`Payee · ${edit.name}`} onClose={() => setEdit(null)}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name"><input className="inp" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Default category">
              <div className="relative">
                <button className="btn w-full justify-start" onClick={() => setPick(!pick)}>{catName(edit.default_category_id) ?? <span className="muted">none</span>}</button>
                {pick && <div className="absolute z-20 top-full mt-1"><CategoryPicker value={edit.default_category_id} onClose={() => setPick(false)} onPick={(c) => { setEdit({ ...edit, default_category_id: c?.id ?? null, default_business: c ? c.default_business : edit.default_business }); setPick(false); }} /></div>}
              </div>
            </Field>
            <Field label="Default business">
              <select className="inp" value={edit.default_business === null ? "" : edit.default_business} onChange={(e) => setEdit({ ...edit, default_business: e.target.value === "" ? null : parseInt(e.target.value, 10) })}>
                <option value="">no opinion</option><option value={1}>business</option><option value={0}>personal</option>
              </select>
            </Field>
            <Field label="Contractor" hint="editors, voice actor - anyone you pay for work (1099 tracking)">
              <select className="inp" value={edit.is_contractor ? edit.contractor_kind || "other" : ""} onChange={(e) => setEdit({ ...edit, is_contractor: e.target.value ? 1 : 0, contractor_kind: e.target.value || null })}>
                <option value="">no</option><option value="editor">editor</option><option value="voice">voice</option><option value="other">other</option>
              </select>
            </Field>
            {!!edit.is_contractor && (
              <>
                <Field label="W-9 on file"><select className="inp" value={edit.w9_on_file} onChange={(e) => setEdit({ ...edit, w9_on_file: parseInt(e.target.value, 10) })}><option value={0}>no</option><option value={1}>yes</option></select></Field>
                <Field label="TIN on file"><select className="inp" value={edit.tin_on_file} onChange={(e) => setEdit({ ...edit, tin_on_file: parseInt(e.target.value, 10) })}><option value={0}>no</option><option value={1}>yes</option></select></Field>
                <Field label="Contact"><input className="inp" value={edit.contact} onChange={(e) => setEdit({ ...edit, contact: e.target.value })} /></Field>
              </>
            )}
          </div>
          <label className="flex items-center gap-1.5 text-[12px] mt-3"><input type="checkbox" checked={applyExisting} onChange={(e) => setApplyExisting(e.target.checked)} /> apply the defaults to this payee's existing transactions that nobody has touched</label>
          <div className="flex items-center gap-2 mt-4">
            <select className="inp w-48" value={mergeInto} onChange={(e) => setMergeInto(e.target.value)}>
              <option value="">merge into…</option>
              {(payees.data ?? []).filter((p) => p.id !== edit.id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button className="btn danger sm" disabled={!mergeInto} onClick={() => { if (confirm(`Move all of "${edit.name}" into the other payee and delete it?`)) merge.mutate({ from: edit.id, into: parseInt(mergeInto, 10) }); }}>merge</button>
            <span className="ml-auto flex gap-2">
              <button className="btn ghost" onClick={() => setEdit(null)}>cancel</button>
              <button className="btn primary" onClick={() => save.mutate(edit)}>Save</button>
            </span>
          </div>
        </Modal>
      )}
    </div>
  );
}
