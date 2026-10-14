import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { post, patch as apiPatch } from "../api/client";
import { useAccounts, useCategories } from "../api/hooks";
import { SCHEDULE_C_LINES, type Rule, type Txn } from "../api/types";
import CategoryPicker from "./CategoryPicker";
import { ErrorLine, Field, Modal } from "./ui";

type Draft = {
  name: string;
  priority: number;
  stop: boolean;
  active: boolean;
  payee_contains: string;
  regex: string;
  sign: "" | "in" | "out";
  account_id: number | null;
  amount_min: string;
  amount_max: string;
  category_id: number | null;
  business: boolean;
  schedule_c_line: string;
  deductible_pct: string;
  mark_reviewed: boolean;
  split_pct: string;
  split_personal_category_id: number | null;
};

const empty: Draft = { name: "", priority: 100, stop: true, active: true, payee_contains: "", regex: "", sign: "", account_id: null, amount_min: "", amount_max: "", category_id: null, business: false, schedule_c_line: "", deductible_pct: "", mark_reviewed: true, split_pct: "", split_personal_category_id: null };

function fromRule(r: Rule): Draft {
  const m = r.match as Record<string, unknown>;
  const a = r.action as Record<string, unknown>;
  const split = (a.split as { pct: number; business?: number; category_id?: number }[] | undefined) ?? [];
  const biz = split.find((s) => s.business);
  const pers = split.find((s) => !s.business);
  return {
    ...empty,
    name: r.name,
    priority: r.priority,
    stop: !!r.stop,
    active: !!r.active,
    payee_contains: (m.payee_contains as string) ?? "",
    regex: (m.regex as string) ?? "",
    sign: (m.sign as "in" | "out") ?? "",
    account_id: (m.account_id as number) ?? null,
    amount_min: m.amount_min_cents !== undefined ? String((m.amount_min_cents as number) / 100) : "",
    amount_max: m.amount_max_cents !== undefined ? String((m.amount_max_cents as number) / 100) : "",
    category_id: (biz?.category_id as number) ?? (a.category_id as number) ?? null,
    business: !!(biz ?? a.business),
    schedule_c_line: (a.schedule_c_line as string) ?? "",
    deductible_pct: a.deductible_pct !== undefined ? String(a.deductible_pct) : "",
    mark_reviewed: a.mark_reviewed !== false,
    split_pct: biz ? String(biz.pct) : "",
    split_personal_category_id: (pers?.category_id as number) ?? null,
  };
}

export function toPayload(d: Draft) {
  const match: Record<string, unknown> = {};
  if (d.payee_contains.trim()) match.payee_contains = d.payee_contains.trim();
  if (d.regex.trim()) match.regex = d.regex.trim();
  if (d.sign) match.sign = d.sign;
  if (d.account_id) match.account_id = d.account_id;
  if (d.amount_min) match.amount_min_cents = Math.round(parseFloat(d.amount_min) * 100);
  if (d.amount_max) match.amount_max_cents = Math.round(parseFloat(d.amount_max) * 100);
  const action: Record<string, unknown> = { mark_reviewed: d.mark_reviewed };
  const pct = parseFloat(d.split_pct);
  if (pct > 0 && pct < 100) {
    action.split = [
      { pct, business: 1, category_id: d.category_id, schedule_c_line: d.schedule_c_line || undefined, deductible_pct: d.deductible_pct ? parseInt(d.deductible_pct, 10) : undefined },
      { pct: 100 - pct, business: 0, category_id: d.split_personal_category_id },
    ];
  } else {
    if (d.category_id) action.category_id = d.category_id;
    action.business = d.business ? 1 : 0;
    if (d.schedule_c_line) action.schedule_c_line = d.schedule_c_line;
    if (d.deductible_pct) action.deductible_pct = parseInt(d.deductible_pct, 10);
  }
  return { name: d.name.trim() || d.payee_contains || d.regex || "rule", priority: d.priority, stop: d.stop ? 1 : 0, active: d.active ? 1 : 0, match, action };
}

export default function RuleModal({ txn, rule, onClose }: { txn?: Txn; rule?: Rule; onClose: () => void }) {
  const qc = useQueryClient();
  const cats = useCategories();
  const accounts = useAccounts();
  const [d, setD] = useState<Draft>(rule ? fromRule(rule) : empty);
  const [pick, setPick] = useState<"biz" | "pers" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [applyNow, setApplyNow] = useState(true);

  useEffect(() => {
    if (!txn || rule) return;
    post<{ name: string; match_json: Record<string, unknown>; action_json: Record<string, unknown> }>(`/api/rules/from-transaction/${txn.id}`)
      .then((s) => {
        const a = s.action_json;
        setD((x) => ({
          ...x,
          name: s.name,
          payee_contains: (s.match_json.payee_contains as string) ?? "",
          sign: (s.match_json.sign as "in" | "out") ?? "",
          category_id: (a.category_id as number) ?? null,
          business: !!a.business,
          schedule_c_line: (a.schedule_c_line as string) ?? "",
        }));
      })
      .catch(setErr);
  }, [txn, rule]);

  const payload = toPayload(d);
  const preview = useQuery({
    queryKey: ["rule-preview", JSON.stringify(payload.match)],
    queryFn: () => post<{ count: number; rows: Txn[] }>("/api/rules/preview", { match: payload.match, limit: 5 }),
    enabled: Object.keys(payload.match).length > 0,
  });

  const save = useMutation({
    mutationFn: async () => {
      const saved = rule ? await apiPatch<Rule>(`/api/rules/${rule.id}`, payload) : await post<Rule>("/api/rules", payload);
      if (applyNow) await post(`/api/rules/${saved.id}/apply?scope=new`);
      return saved;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["rules"] });
      qc.invalidateQueries({ queryKey: ["txns"] });
      onClose();
    },
    onError: setErr,
  });

  const catName = (id: number | null) => (id ? cats.data?.find((c) => c.id === id)?.full_name : null);
  const pct = parseFloat(d.split_pct);
  const splitting = pct > 0 && pct < 100;

  return (
    <Modal title={rule ? "Edit rule" : "New rule"} onClose={onClose} width="max-w-3xl">
      <div className="grid grid-cols-2 gap-5" onKeyDown={(e) => e.stopPropagation()}>
        <div className="space-y-3">
          <div className="text-[11px] uppercase tracking-wider text-muted">When</div>
          <Field label="Text contains" hint="payee, description or memo; case-insensitive">
            <input className="inp" value={d.payee_contains} onChange={(e) => setD({ ...d, payee_contains: e.target.value })} autoFocus />
          </Field>
          <Field label="Regex (optional)">
            <input className="inp num" value={d.regex} onChange={(e) => setD({ ...d, regex: e.target.value })} />
          </Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label="Direction">
              <select className="inp" value={d.sign} onChange={(e) => setD({ ...d, sign: e.target.value as Draft["sign"] })}>
                <option value="">any</option>
                <option value="out">money out</option>
                <option value="in">money in</option>
              </select>
            </Field>
            <Field label="Min $">
              <input className="inp num" value={d.amount_min} onChange={(e) => setD({ ...d, amount_min: e.target.value })} />
            </Field>
            <Field label="Max $">
              <input className="inp num" value={d.amount_max} onChange={(e) => setD({ ...d, amount_max: e.target.value })} />
            </Field>
          </div>
          <Field label="Account">
            <select className="inp" value={d.account_id ?? ""} onChange={(e) => setD({ ...d, account_id: e.target.value ? parseInt(e.target.value, 10) : null })}>
              <option value="">any</option>
              {(accounts.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="text-[12px] text-muted">
            {preview.data ? (
              <>
                matches <b className="text-fg">{preview.data.count}</b> existing rows
                <ul className="mt-1 space-y-0.5">
                  {preview.data.rows.map((r) => (
                    <li key={r.id} className="truncate">
                      <span className="num text-dim">{r.date}</span> {r.payee_raw || r.description_raw} <span className="num">{(r.amount_cents / 100).toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              "add a condition to preview"
            )}
          </div>
        </div>
        <div className="space-y-3">
          <div className="text-[11px] uppercase tracking-wider text-muted">Then</div>
          <Field label="Name">
            <input className="inp" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder={d.payee_contains || "rule"} />
          </Field>
          <Field label={splitting ? "Business category" : "Category"}>
            <div className="relative">
              <button className="btn w-full justify-start" onClick={() => setPick(pick === "biz" ? null : "biz")}>
                {catName(d.category_id) ?? <span className="muted">choose…</span>}
              </button>
              {pick === "biz" && (
                <div className="absolute z-20 top-full mt-1">
                  <CategoryPicker
                    value={d.category_id}
                    onClose={() => setPick(null)}
                    onPick={(c) => {
                      setD({ ...d, category_id: c?.id ?? null, business: c ? !!c.default_business : d.business, deductible_pct: c && c.default_deductible_pct !== 100 ? String(c.default_deductible_pct) : "" });
                      setPick(null);
                    }}
                  />
                </div>
              )}
            </div>
          </Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label="Business">
              <button className={`chip ${d.business || splitting ? "biz" : ""}`} onClick={() => setD({ ...d, business: !d.business })} disabled={splitting}>
                {d.business || splitting ? "business" : "personal"}
              </button>
            </Field>
            <Field label="Sched C line">
              <select className="inp" value={d.schedule_c_line} onChange={(e) => setD({ ...d, schedule_c_line: e.target.value })} disabled={!(d.business || splitting)}>
                <option value="">category default</option>
                {SCHEDULE_C_LINES.map((l) => (
                  <option key={l.line} value={l.line}>
                    {l.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Deductible %">
              <input className="inp num" value={d.deductible_pct} placeholder="100" onChange={(e) => setD({ ...d, deductible_pct: e.target.value.replace(/[^\d]/g, "") })} disabled={!(d.business || splitting)} />
            </Field>
          </div>
          <Field label="Business % (splits the rest as personal)" hint="e.g. 40 for a phone bill that is 40% business">
            <input className="inp num w-24" value={d.split_pct} onChange={(e) => setD({ ...d, split_pct: e.target.value.replace(/[^\d.]/g, "") })} />
          </Field>
          {splitting && (
            <Field label="Personal category">
              <div className="relative">
                <button className="btn w-full justify-start" onClick={() => setPick(pick === "pers" ? null : "pers")}>
                  {catName(d.split_personal_category_id) ?? <span className="muted">choose…</span>}
                </button>
                {pick === "pers" && (
                  <div className="absolute z-20 top-full mt-1">
                    <CategoryPicker value={d.split_personal_category_id} onClose={() => setPick(null)} onPick={(c) => { setD({ ...d, split_personal_category_id: c?.id ?? null }); setPick(null); }} />
                  </div>
                )}
              </div>
            </Field>
          )}
          <div className="flex flex-wrap gap-4 text-[12px]">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={d.mark_reviewed} onChange={(e) => setD({ ...d, mark_reviewed: e.target.checked })} /> mark reviewed
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={d.stop} onChange={(e) => setD({ ...d, stop: e.target.checked })} /> stop after this rule
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={applyNow} onChange={(e) => setApplyNow(e.target.checked)} /> apply to inbox now
            </label>
            <label className="flex items-center gap-1.5">
              priority <input className="inp num w-16" value={d.priority} onChange={(e) => setD({ ...d, priority: parseInt(e.target.value || "100", 10) })} />
            </label>
          </div>
        </div>
      </div>
      <ErrorLine error={err} />
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn ghost" onClick={onClose}>
          cancel
        </button>
        <button className="btn primary" onClick={() => save.mutate()} disabled={save.isPending || Object.keys(payload.match).length === 0}>
          {rule ? "Save rule" : "Create rule"}
        </button>
      </div>
    </Modal>
  );
}
