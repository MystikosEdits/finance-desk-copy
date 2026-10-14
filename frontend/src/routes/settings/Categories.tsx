import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { patch, post } from "../../api/client";
import { useCategories } from "../../api/hooks";
import { SCHEDULE_C_LINES, type Category } from "../../api/types";
import { Card, ErrorLine, Field, Modal } from "../../components/ui";

export default function Categories() {
  const qc = useQueryClient();
  const cats = useCategories(true);
  const [edit, setEdit] = useState<Partial<Category> | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const inval = () => qc.invalidateQueries({ queryKey: ["categories"] });
  const save = useMutation({
    mutationFn: (c: Partial<Category>) => (c.id ? patch(`/api/categories/${c.id}`, { ...c, clear_line: !c.default_schedule_c_line }) : post("/api/categories", c)),
    onSuccess: () => { setEdit(null); inval(); },
    onError: setErr,
  });
  const all = cats.data ?? [];
  const parents = all.filter((c) => c.parent_id === null && (showArchived || !c.is_archived));

  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <div className="flex items-center gap-3">
        <button className="btn primary" onClick={() => setEdit({ kind: "expense", default_business: 0, default_deductible_pct: 100 })}>+ category</button>
        <label className="text-[12px] flex items-center gap-1"><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> show archived</label>
        <span className="text-[12px] text-muted">A category's business flag and Schedule C line are defaults for new splits; any split can override them.</span>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {parents.map((p) => (
          <Card key={p.id} title={<span className="flex items-center gap-2">{p.name} <span className="chip">{p.kind}</span>{p.default_business ? <span className="chip biz">biz</span> : null}</span>} right={<button className="btn sm ghost" onClick={() => setEdit(p)}>edit</button>}>
            <table className="tbl">
              <tbody>
                {all.filter((c) => c.parent_id === p.id && (showArchived || !c.is_archived)).map((c) => (
                  <tr key={c.id} className={c.is_archived ? "opacity-50" : ""}>
                    <td>{c.name}</td>
                    <td className="text-muted num text-[11px]">{c.default_schedule_c_line ? `line ${c.default_schedule_c_line}` : ""}{c.default_deductible_pct !== 100 ? ` · ${c.default_deductible_pct}%` : ""}</td>
                    <td className="r text-muted num text-[11px]">{c.uses} uses</td>
                    <td className="r"><button className="btn sm ghost" onClick={() => setEdit(c)}>edit</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}
      </div>
      {edit && (
        <Modal title={edit.id ? "Edit category" : "New category"} onClose={() => setEdit(null)}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name"><input className="inp" autoFocus value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Parent">
              <select className="inp" value={edit.parent_id ?? ""} onChange={(e) => setEdit({ ...edit, parent_id: e.target.value ? parseInt(e.target.value, 10) : null })}>
                <option value="">(top level)</option>
                {all.filter((c) => c.parent_id === null && c.id !== edit.id).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="Kind">
              <select className="inp" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value as Category["kind"] })}>
                <option value="expense">expense</option><option value="income">income</option><option value="transfer">transfer</option>
              </select>
            </Field>
            <Field label="Default business">
              <select className="inp" value={edit.default_business ?? 0} onChange={(e) => setEdit({ ...edit, default_business: parseInt(e.target.value, 10) })}>
                <option value={0}>personal</option><option value={1}>business</option>
              </select>
            </Field>
            <Field label="Default Schedule C line">
              <select className="inp" value={edit.default_schedule_c_line ?? ""} onChange={(e) => setEdit({ ...edit, default_schedule_c_line: e.target.value || null })}>
                <option value="">—</option>
                {SCHEDULE_C_LINES.map((l) => <option key={l.line} value={l.line}>{l.label}</option>)}
                <option value="1">1 Gross receipts (income)</option>
              </select>
            </Field>
            <Field label="Deductible %" hint="50 for meals"><input className="inp num" value={edit.default_deductible_pct ?? 100} onChange={(e) => setEdit({ ...edit, default_deductible_pct: parseInt(e.target.value || "100", 10) })} /></Field>
            {edit.id && (
              <Field label="Archived">
                <select className="inp" value={edit.is_archived ?? 0} onChange={(e) => setEdit({ ...edit, is_archived: parseInt(e.target.value, 10) })}>
                  <option value={0}>active</option><option value={1}>archived</option>
                </select>
              </Field>
            )}
          </div>
          <div className="flex justify-end gap-2 mt-4">
            <button className="btn ghost" onClick={() => setEdit(null)}>cancel</button>
            <button className="btn primary" disabled={!edit.name} onClick={() => save.mutate(edit)}>Save</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
