import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { del, post } from "../../api/client";
import { useCategories, useRules } from "../../api/hooks";
import type { Rule } from "../../api/types";
import RuleModal from "../../components/RuleModal";
import { Card, ErrorLine, fmtWhen } from "../../components/ui";

export default function Rules() {
  const qc = useQueryClient();
  const rules = useRules();
  const cats = useCategories();
  const [edit, setEdit] = useState<Rule | "new" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const inval = () => { qc.invalidateQueries({ queryKey: ["rules"] }); qc.invalidateQueries({ queryKey: ["txns"] }); };
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/rules/${id}`), onSuccess: inval, onError: setErr });
  const apply = useMutation({ mutationFn: ({ id, scope }: { id: number; scope: string }) => post<{ changed: number }>(`/api/rules/${id}/apply?scope=${scope}`), onSuccess: inval, onError: setErr });
  const applyAll = useMutation({ mutationFn: () => post<{ changed: number }>("/api/rules/apply-all?scope=new"), onSuccess: inval, onError: setErr });
  const catName = (id: unknown) => (typeof id === "number" ? cats.data?.find((c) => c.id === id)?.full_name ?? "?" : null);

  const describeMatch = (m: Record<string, unknown>) =>
    [m.payee_contains && `contains "${m.payee_contains}"`, m.regex && `/${m.regex}/`, m.sign === "out" ? "money out" : m.sign === "in" ? "money in" : null,
     m.amount_min_cents !== undefined && `≥ $${(m.amount_min_cents as number) / 100}`, m.amount_max_cents !== undefined && `≤ $${(m.amount_max_cents as number) / 100}`, m.account_id && `account #${m.account_id}`]
      .filter(Boolean).join(" · ");
  const describeAction = (a: Record<string, unknown>) => {
    const split = a.split as { pct: number; business?: number; category_id?: number }[] | undefined;
    if (split) return split.map((s) => `${s.pct}% ${s.business ? "biz" : "personal"}${s.category_id ? ` ${catName(s.category_id)}` : ""}`).join(" + ");
    return [catName(a.category_id), a.business ? "business" : "personal", a.schedule_c_line && `line ${a.schedule_c_line}`, a.deductible_pct !== undefined && `${a.deductible_pct}%`, a.mark_reviewed && "reviewed"].filter(Boolean).join(" · ");
  };

  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <div className="flex items-center gap-3">
        <button className="btn primary" onClick={() => setEdit("new")}>+ rule</button>
        <button className="btn" onClick={() => applyAll.mutate()} disabled={applyAll.isPending}>Run all rules on the inbox</button>
        {applyAll.data && <span className="text-[12px] text-muted">changed {applyAll.data.changed}</span>}
        <span className="text-[12px] text-muted">Rules run in priority order on new rows. They never overwrite a split you set by hand.</span>
      </div>
      <Card>
        <table className="tbl">
          <thead><tr><th className="w-12">Pri</th><th>Name</th><th>When</th><th>Then</th><th className="r">Hits</th><th>Last</th><th></th></tr></thead>
          <tbody>
            {(rules.data ?? []).map((r) => (
              <tr key={r.id} className={r.active ? "" : "opacity-50"}>
                <td className="num text-muted">{r.priority}</td>
                <td>{r.name}{!r.stop && <span className="chip ml-1">continue</span>}</td>
                <td className="text-[12px]">{describeMatch(r.match)}</td>
                <td className="text-[12px]">{describeAction(r.action)}</td>
                <td className="r num">{r.hits}</td>
                <td className="text-muted">{fmtWhen(r.last_hit_at)}</td>
                <td className="r whitespace-nowrap">
                  <button className="btn sm ghost" onClick={() => apply.mutate({ id: r.id, scope: "new" })}>run on inbox</button>
                  <button className="btn sm ghost" onClick={() => { if (confirm("Run on ALL transactions? Rows you categorised by hand are still left alone.")) apply.mutate({ id: r.id, scope: "all" }); }}>run on all</button>
                  <button className="btn sm ghost" onClick={() => setEdit(r)}>edit</button>
                  <button className="btn sm ghost" onClick={() => { if (confirm(`Delete rule "${r.name}"?`)) remove.mutate(r.id); }}>delete</button>
                </td>
              </tr>
            ))}
            {(rules.data ?? []).length === 0 && <tr><td colSpan={7} className="text-muted text-center py-6">No rules yet. Press <kbd>r</kbd> on any transaction to make one from it.</td></tr>}
          </tbody>
        </table>
      </Card>
      {edit && <RuleModal rule={edit === "new" ? undefined : edit} onClose={() => setEdit(null)} />}
    </div>
  );
}
