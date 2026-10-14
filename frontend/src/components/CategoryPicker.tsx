import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post } from "../api/client";
import { useCategories } from "../api/hooks";
import type { Category } from "../api/types";

/** Searchable category list. Type to filter, ↑↓ to move, Enter to pick, Esc to close. */
export default function CategoryPicker({
  value,
  onPick,
  onClose,
  autoFocus = true,
  kinds,
}: {
  value: number | null;
  onPick: (c: Category | null) => void;
  onClose?: () => void;
  autoFocus?: boolean;
  kinds?: ("income" | "expense" | "transfer")[];
}) {
  const cats = useCategories();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [parentId, setParentId] = useState<number | "">("");
  const parents = useMemo(() => (cats.data ?? []).filter((c) => c.parent_id === null && (!kinds || kinds.includes(c.kind))), [cats.data, kinds]);
  const create = useMutation({
    mutationFn: () => post<Category>("/api/categories", { name: q.trim(), kind: parentId !== "" ? parents.find((p) => p.id === parentId)?.kind ?? "expense" : "expense", parent_id: parentId === "" ? null : parentId,
      default_business: parentId !== "" ? parents.find((p) => p.id === parentId)?.default_business ?? 0 : 0 }),
    onSuccess: (c) => { qc.invalidateQueries({ queryKey: ["categories"] }); onPick({ ...c, full_name: c.name, uses: 0 }); },
  });
  const [hi, setHi] = useState(0);
  const ref = useRef<HTMLInputElement>(null);

  const list = useMemo(() => {
    const all = (cats.data ?? []).filter((c) => !kinds || kinds.includes(c.kind));
    const needle = q.trim().toLowerCase();
    const leaves = all.filter((c) => c.parent_id !== null || !all.some((x) => x.parent_id === c.id));
    if (!needle) return leaves;
    const words = needle.split(/\s+/);
    return leaves
      .filter((c) => words.every((w) => c.full_name.toLowerCase().includes(w)))
      .sort((a, b) => b.uses - a.uses);
  }, [cats.data, q, kinds]);

  useEffect(() => setHi(0), [q]);
  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  function key(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHi((h) => Math.min(h + 1, list.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHi((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (list[hi]) onPick(list[hi]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose?.();
    }
    e.stopPropagation();
  }

  return (
    <div className="w-72 card shadow-xl overflow-hidden" onKeyDown={(e) => e.stopPropagation()}>
      <input ref={ref} className="inp rounded-none border-0 border-b border-line" placeholder="category…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={key} />
      <ul className="max-h-72 overflow-auto">
        {list.map((c, i) => (
          <li
            key={c.id}
            className={`px-3 py-1.5 cursor-pointer flex items-center justify-between text-[12.5px] ${i === hi ? "bg-panel-2" : ""} ${c.id === value ? "text-accent" : ""}`}
            onMouseEnter={() => setHi(i)}
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(c);
            }}
          >
            <span>
              {c.parent_id !== null && <span className="text-dim">{c.full_name.split(" › ")[0]} › </span>}
              {c.name}
            </span>
            <span className="flex items-center gap-1">
              {c.default_business === 1 && <span className="chip biz">biz</span>}
              {c.default_schedule_c_line && <span className="num text-dim text-[10px]">{c.default_schedule_c_line}</span>}
              {c.kind === "transfer" && <span className="chip">xfer</span>}
            </span>
          </li>
        ))}
        {list.length === 0 && q.trim() && (
          <li className="px-3 py-2 text-[12px] space-y-2">
            <div className="text-muted">no match</div>
            <div className="flex items-center gap-1">
              <span>create <b>"{q.trim()}"</b> in</span>
              <select className="inp w-36" value={parentId} onChange={(e) => setParentId(e.target.value ? parseInt(e.target.value, 10) : "")} onKeyDown={(e) => e.stopPropagation()}>
                <option value="">(top level)</option>
                {parents.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button className="btn sm primary" onMouseDown={(e) => { e.preventDefault(); create.mutate(); }} disabled={create.isPending}>create</button>
            </div>
          </li>
        )}
        {list.length === 0 && !q.trim() && <li className="px-3 py-3 text-muted text-[12px]">type to search</li>}
      </ul>
      {value !== null && (
        <button className="btn ghost sm w-full justify-start rounded-none border-t border-line" onMouseDown={(e) => { e.preventDefault(); onPick(null); }}>
          clear category
        </button>
      )}
    </div>
  );
}
