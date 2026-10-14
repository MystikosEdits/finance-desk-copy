import { useState } from "react";
import { useCategories, useIncomeSources } from "../api/hooks";
import { SCHEDULE_C_LINES, type Split, type Txn } from "../api/types";
import { fmt, parse } from "../lib/money";
import CategoryPicker from "./CategoryPicker";
import { Money } from "./ui";

type Draft = { amount: string; category_id: number | null; business: boolean; deductible_pct: string; schedule_c_line: string; income_source_id: number | null; note: string };

function toDraft(s: Split): Draft {
  return {
    amount: (s.amount_cents / 100).toFixed(2),
    category_id: s.category_id,
    business: !!s.business,
    deductible_pct: String(s.deductible_pct ?? 100),
    schedule_c_line: s.schedule_c_line ?? "",
    income_source_id: s.income_source_id,
    note: s.note ?? "",
  };
}

export default function SplitEditor({ txn, onSave, onCancel }: { txn: Txn; onSave: (splits: Split[], review: boolean) => Promise<void>; onCancel: () => void }) {
  const cats = useCategories();
  const sources = useIncomeSources();
  const [rows, setRows] = useState<Draft[]>(() => (txn.splits.length ? txn.splits.map(toDraft) : [toDraft({ amount_cents: txn.amount_cents, category_id: null, business: 0, deductible_pct: 100, schedule_c_line: null, income_source_id: null, note: "" })]));
  const [picking, setPicking] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const cents = rows.map((r) => parse(r.amount));
  const sum = cents.reduce((a, b) => a + (Number.isNaN(b) ? 0 : b), 0);
  const remaining = txn.amount_cents - sum;
  const catById = new Map((cats.data ?? []).map((c) => [c.id, c]));

  function update(i: number, patch: Partial<Draft>) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }
  function addRow() {
    setRows((rs) => [...rs, { amount: (remaining / 100).toFixed(2), category_id: null, business: false, deductible_pct: "100", schedule_c_line: "", income_source_id: null, note: "" }]);
  }
  function splitPct(pct: number) {
    const a = Math.round((txn.amount_cents * pct) / 100);
    const b = txn.amount_cents - a;
    setRows((rs) => [
      { ...(rs[0] ?? toDraft({ amount_cents: a, category_id: null, business: 1, deductible_pct: 100, schedule_c_line: null, income_source_id: null, note: "" })), amount: (a / 100).toFixed(2), business: true },
      { ...(rs[1] ?? { amount: "", category_id: null, business: false, deductible_pct: "100", schedule_c_line: "", income_source_id: null, note: "" }), amount: (b / 100).toFixed(2), business: false },
    ]);
  }
  function remove(i: number) {
    setRows((rs) => rs.filter((_, j) => j !== i));
  }

  async function save(review: boolean) {
    setErr(null);
    if (cents.some((c) => Number.isNaN(c))) return setErr("an amount is not a number");
    if (remaining !== 0) return setErr(`splits must add up to ${fmt(txn.amount_cents)} (off by ${fmt(remaining)})`);
    setBusy(true);
    try {
      await onSave(
        rows.map((r, i) => ({
          amount_cents: cents[i],
          category_id: r.category_id,
          business: r.business ? 1 : 0,
          deductible_pct: r.deductible_pct === "" ? (r.category_id ? catById.get(r.category_id)?.default_deductible_pct ?? 100 : 100) : parseInt(r.deductible_pct, 10),
          schedule_c_line: r.schedule_c_line || null,
          income_source_id: r.income_source_id,
          note: r.note,
        })),
        review,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3" onKeyDown={(e) => e.stopPropagation()}>
      <div className="flex items-center justify-between text-[12px]">
        <div>
          <span className="muted">transaction</span> <Money cents={txn.amount_cents} /> <span className="muted">· remaining</span>{" "}
          <span className={`num ${remaining === 0 ? "text-accent" : "text-warn"}`}>{fmt(remaining)}</span>
        </div>
        <div className="flex gap-1">
          <button className="btn sm ghost" onClick={() => splitPct(50)}>50/50</button>
          <button className="btn sm ghost" onClick={() => splitPct(40)}>40 biz</button>
          <button className="btn sm ghost" onClick={() => splitPct(80)}>80 biz</button>
          <button className="btn sm" onClick={addRow}>+ line</button>
        </div>
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th className="r w-28">Amount</th>
            <th>Category</th>
            <th className="w-16">Biz</th>
            <th className="w-36">Sched C line</th>
            <th className="w-16">Ded %</th>
            <th className="w-32">Source</th>
            <th>Note</th>
            <th className="w-8"></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const cat = r.category_id ? catById.get(r.category_id) : undefined;
            const effLine = r.schedule_c_line || cat?.default_schedule_c_line || "";
            return (
              <tr key={i}>
                <td className="r">
                  <input className="inp num text-right" value={r.amount} onChange={(e) => update(i, { amount: e.target.value })} />
                </td>
                <td className="relative">
                  <button className="btn sm w-full justify-start" onClick={() => setPicking(picking === i ? null : i)}>
                    {cat ? cat.full_name : <span className="muted">choose…</span>}
                  </button>
                  {picking === i && (
                    <div className="absolute z-20 left-0 top-full mt-1">
                      <CategoryPicker
                        value={r.category_id}
                        onClose={() => setPicking(null)}
                        onPick={(c) => {
                          update(i, {
                            category_id: c?.id ?? null,
                            business: c ? !!c.default_business : r.business,
                            deductible_pct: c ? String(c.default_deductible_pct) : r.deductible_pct,
                          });
                          setPicking(null);
                        }}
                      />
                    </div>
                  )}
                </td>
                <td>
                  <button className={`chip ${r.business ? "biz" : ""}`} onClick={() => update(i, { business: !r.business })}>
                    {r.business ? "biz" : "personal"}
                  </button>
                </td>
                <td>
                  <select className="inp" value={r.schedule_c_line} onChange={(e) => update(i, { schedule_c_line: e.target.value })} disabled={!r.business}>
                    <option value="">{cat?.default_schedule_c_line ? `default (${cat.default_schedule_c_line})` : "—"}</option>
                    {SCHEDULE_C_LINES.map((l) => (
                      <option key={l.line} value={l.line}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                  {r.business && !effLine && <span className="text-[10px] text-warn">no line</span>}
                </td>
                <td>
                  <input className="inp num" value={r.deductible_pct} onChange={(e) => update(i, { deductible_pct: e.target.value.replace(/[^\d]/g, "") })} disabled={!r.business} />
                </td>
                <td>
                  <select className="inp" value={r.income_source_id ?? ""} onChange={(e) => update(i, { income_source_id: e.target.value ? parseInt(e.target.value, 10) : null })} disabled={txn.amount_cents <= 0}>
                    <option value="">—</option>
                    {(sources.data ?? []).map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input className="inp" value={r.note} onChange={(e) => update(i, { note: e.target.value })} placeholder="" />
                </td>
                <td>
                  {rows.length > 1 && (
                    <button className="btn ghost sm" onClick={() => remove(i)} title="remove">
                      ×
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {err && <div className="text-bad text-[12px]">{err}</div>}
      <div className="flex items-center justify-end gap-2">
        <button className="btn ghost" onClick={onCancel}>
          cancel
        </button>
        <button className="btn" onClick={() => save(false)} disabled={busy}>
          Save
        </button>
        <button className="btn primary" onClick={() => save(true)} disabled={busy}>
          Save + reviewed <kbd>⏎</kbd>
        </button>
      </div>
    </div>
  );
}
