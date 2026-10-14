import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, del, get, patch, post, put, qs } from "../api/client";
import { useAccounts, useCategories } from "../api/hooks";
import type { Split, Txn, TxnList } from "../api/types";
import CategoryPicker from "../components/CategoryPicker";
import RuleModal from "../components/RuleModal";
import SplitEditor from "../components/SplitEditor";
import { Empty, ErrorLine, Modal, Money, Page, fmtDate } from "../components/ui";
import { inboxReducer } from "../lib/inbox";
import { fmt } from "../lib/money";

type Tab = "inbox" | "all";

function isTyping() {
  const el = document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || (el as HTMLElement).isContentEditable);
}

export default function Transactions() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab: Tab = (params.get("tab") as Tab) || "inbox";
  const filters = {
    account: params.get("account") ?? "",
    frm: params.get("frm") ?? "",
    to: params.get("to") ?? "",
    q: params.get("q") ?? "",
    business: params.get("business") ?? "",
    uncategorized: params.get("uncategorized") ?? "",
    category: params.get("category") ?? "",
    payee: params.get("payee") ?? "",
    page: params.get("page") ?? "1",
    sort: params.get("sort") ?? "date",
  };
  const setFilter = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    if (k !== "page") p.delete("page");
    setParams(p, { replace: true });
  };

  const accounts = useAccounts();
  const cats = useCategories();
  const key = ["txns", tab, JSON.stringify(filters)];
  const list = useQuery({
    queryKey: key,
    queryFn: () =>
      tab === "inbox"
        ? get<TxnList>(`/api/transactions/inbox${qs({ account: filters.account })}`)
        : get<TxnList>(`/api/transactions${qs({ ...filters, per: 100, uncategorized: filters.uncategorized ? 1 : undefined })}`),
  });
  const rows = list.data?.rows ?? [];

  const [st, dispatch] = useReducer(inboxReducer, { index: 0, count: 0, mode: "list" });
  useEffect(() => dispatch({ type: "count", count: rows.length }), [rows.length]);
  const current = rows[st.index];
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [err, setErr] = useState<unknown>(null);
  const [quickCat, setQuickCat] = useState(false);
  const tableRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = tableRef.current?.querySelector<HTMLElement>(`[data-row="${current?.id}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [current?.id]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["txns"] });
    qc.invalidateQueries({ queryKey: ["accounts"] });
  };

  const saveSplits = useMutation({
    mutationFn: ({ id, splits, review }: { id: number; splits: Split[]; review: boolean }) => put<Txn>(`/api/transactions/${id}/splits`, { splits, review }),
    onSuccess: (_d, v) => {
      if (v.review && tab === "inbox") dispatch({ type: "reviewed" });
      else dispatch({ type: "close" });
      invalidate();
    },
  });
  const review = useMutation({
    mutationFn: (id: number) => post(`/api/transactions/${id}/review`),
    onSuccess: () => {
      if (tab === "inbox") dispatch({ type: "reviewed" });
      invalidate();
    },
    onError: setErr,
  });
  const unreview = useMutation({ mutationFn: (id: number) => post(`/api/transactions/${id}/unreview`), onSuccess: invalidate, onError: setErr });
  const toggleBiz = useMutation({
    mutationFn: (t: Txn) => post("/api/transactions/bulk", { ids: [t.id], action: t.is_business ? "personal" : "business" }),
    onSuccess: invalidate,
    onError: setErr,
  });
  const quickCategorize = useMutation({
    mutationFn: ({ id, category_id }: { id: number; category_id: number | null }) => post("/api/transactions/bulk", { ids: [id], action: "categorize", category_id }),
    onSuccess: () => {
      setQuickCat(false);
      dispatch({ type: "close" });
      invalidate();
    },
    onError: setErr,
  });
  const patchTxn = useMutation({ mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => patch<Txn>(`/api/transactions/${id}`, body), onSuccess: invalidate, onError: setErr });
  const bulk = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<{ changed: number }>("/api/transactions/bulk", body),
    onSuccess: () => {
      setSelected(new Set());
      invalidate();
    },
    onError: setErr,
  });
  const transferAct = useMutation({
    mutationFn: ({ id, act }: { id: number; act: "confirm" | "reject" }) => post(`/api/transfers/${id}/${act}`),
    onSuccess: invalidate,
    onError: setErr,
  });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/transactions/${id}`), onSuccess: invalidate, onError: setErr });
  const upload = useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append("file", file);
      form.append("transaction_id", String(id));
      form.append("kind", "receipt");
      return api("/api/attachments", { form });
    },
    onSuccess: () => { dispatch({ type: "close" }); invalidate(); },
    onError: setErr,
  });
  const removeAttachment = useMutation({ mutationFn: (id: number) => del(`/api/attachments/${id}`), onSuccess: invalidate, onError: setErr });
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (st.mode === "attach") fileRef.current?.click();
  }, [st.mode]);

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if (isTyping()) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (st.mode !== "list") {
        if (e.key === "Escape") {
          setQuickCat(false);
          dispatch({ type: "close" });
        }
        return;
      }
      if (!current && e.key !== "Escape") return;
      switch (e.key) {
        case "Enter":
          e.preventDefault();
          if (current) review.mutate(current.id);
          return;
        case "b":
          if (current) toggleBiz.mutate(current);
          return;
        case "c":
          e.preventDefault();
          setQuickCat(true);
          break;
        case "x":
          if (current) setSelected((s) => { const n = new Set(s); if (n.has(current.id)) n.delete(current.id); else n.add(current.id); return n; });
          return;
        case "u":
          if (current && current.status === "reviewed") unreview.mutate(current.id);
          return;
        case "Escape":
          setSelected(new Set());
          return;
      }
      dispatch({ type: "key", key: e.key });
      // A mode key (c/s/r/n) mounts a modal whose input takes focus before the
      // character is inserted, so without this the "c" lands in the search box.
      if (e.key.length === 1 || ["ArrowDown", "ArrowUp"].includes(e.key)) e.preventDefault();
    },
    [st.mode, current, review, toggleBiz, unreview],
  );
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  const catName = (id: number | null) => (id ? cats.data?.find((c) => c.id === id)?.name ?? "?" : null);
  const total = list.data?.total ?? 0;

  return (
    <Page
      title={
        <span className="flex items-center gap-3">
          <button className={`btn sm ${tab === "inbox" ? "primary" : "ghost"}`} onClick={() => setFilter("tab", "inbox")}>
            Inbox {tab === "inbox" && list.data ? `· ${total}` : ""}
          </button>
          <button className={`btn sm ${tab === "all" ? "primary" : "ghost"}`} onClick={() => setFilter("tab", "all")}>
            All
          </button>
        </span>
      }
      actions={
        <span className="text-[11px] text-muted hidden xl:inline">
          <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>c</kbd> category · <kbd>b</kbd> business · <kbd>s</kbd> split · <kbd>r</kbd> rule · <kbd>n</kbd> note · <kbd>x</kbd> select · <kbd>⏎</kbd> reviewed
        </span>
      }
    >
      <ErrorLine error={err} />
      <div className="flex flex-wrap items-center gap-2">
        <select className="inp w-44" value={filters.account} onChange={(e) => setFilter("account", e.target.value)}>
          <option value="">all accounts</option>
          {(accounts.data ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
        {tab === "all" && (
          <>
            <input className="inp w-56" placeholder="search payee, memo, notes…" value={filters.q} onChange={(e) => setFilter("q", e.target.value)} />
            <input className="inp w-36 num" type="date" value={filters.frm} onChange={(e) => setFilter("frm", e.target.value)} />
            <input className="inp w-36 num" type="date" value={filters.to} onChange={(e) => setFilter("to", e.target.value)} />
            <select className="inp w-32" value={filters.business} onChange={(e) => setFilter("business", e.target.value)}>
              <option value="">biz + personal</option>
              <option value="1">business</option>
              <option value="0">personal</option>
            </select>
            <label className="text-[12px] flex items-center gap-1">
              <input type="checkbox" checked={!!filters.uncategorized} onChange={(e) => setFilter("uncategorized", e.target.checked ? "1" : "")} /> uncategorized
            </label>
            <select className="inp w-32" value={filters.sort} onChange={(e) => setFilter("sort", e.target.value)}>
              <option value="date">newest</option>
              <option value="date_asc">oldest</option>
              <option value="amount">largest out</option>
              <option value="amount_desc">largest in</option>
            </select>
            {filters.category && (
              <button className="chip" onClick={() => setFilter("category", "")}>
                category: {catName(parseInt(filters.category, 10))} ×
              </button>
            )}
            {filters.payee && (
              <button className="chip" onClick={() => setFilter("payee", "")}>
                payee filter ×
              </button>
            )}
          </>
        )}
        <span className="ml-auto text-[12px] text-muted num">
          {list.data && (
            <>
              {total} rows · in <Money cents={list.data.inflow} tone={false} /> · out <Money cents={list.data.outflow} tone={false} />
            </>
          )}
        </span>
      </div>

      {selected.size > 0 && (
        <div className="card px-3 py-2 flex items-center gap-2 text-[12px]">
          <b>{selected.size} selected</b>
          <BulkCategory onPick={(id) => bulk.mutate({ ids: [...selected], action: "categorize", category_id: id })} />
          <button className="btn sm" onClick={() => bulk.mutate({ ids: [...selected], action: "business" })}>business</button>
          <button className="btn sm" onClick={() => bulk.mutate({ ids: [...selected], action: "personal" })}>personal</button>
          <button className="btn sm" onClick={() => bulk.mutate({ ids: [...selected], action: "rules" })}>run rules</button>
          <button className="btn sm primary" onClick={() => bulk.mutate({ ids: [...selected], action: "review" })}>mark reviewed</button>
          <button className="btn sm ghost ml-auto" onClick={() => setSelected(new Set())}>clear</button>
        </div>
      )}

      <div className="flex gap-4 items-start">
        <div className="card flex-1 min-w-0 overflow-auto max-h-[calc(100vh-190px)]" ref={tableRef}>
          {list.isLoading ? (
            <Empty>loading…</Empty>
          ) : rows.length === 0 ? (
            <Empty>{tab === "inbox" ? "Inbox zero. Nothing to review." : "No transactions match."}</Empty>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th className="w-6"></th>
                  <th className="w-20">Date</th>
                  <th className="w-32">Account</th>
                  <th>Payee</th>
                  <th className="w-48">Category</th>
                  <th className="r w-28">Amount</th>
                  <th className="w-28"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t, i) => (
                  <tr key={t.id} data-row={t.id} className={i === st.index ? "sel" : ""} onClick={() => dispatch({ type: "select", index: i })}>
                    <td>
                      <input type="checkbox" checked={selected.has(t.id)} onChange={() => setSelected((s) => { const n = new Set(s); if (n.has(t.id)) n.delete(t.id); else n.add(t.id); return n; })} onClick={(e) => e.stopPropagation()} />
                    </td>
                    <td className="num text-muted whitespace-nowrap">{fmtDate(t.date)}</td>
                    <td className="text-muted truncate max-w-32">{t.account_name}</td>
                    <td className="min-w-0">
                      <div className="truncate">{t.payee_name || t.payee_raw || t.description_raw || <span className="text-dim">(no description)</span>}</div>
                      {(t.payee_name && t.payee_raw && t.payee_name.toLowerCase() !== t.payee_raw.toLowerCase()) || t.memo_raw ? (
                        <div className="text-[11px] text-dim truncate">{t.payee_raw || t.description_raw}{t.memo_raw ? ` · ${t.memo_raw}` : ""}</div>
                      ) : null}
                    </td>
                    <td>
                      <SplitSummary t={t} />
                    </td>
                    <td className="r">
                      <Money cents={t.amount_cents} />
                    </td>
                    <td>
                      <span className="flex gap-1 flex-wrap">
                        {t.pending ? <span className="chip">pending</span> : null}
                        {t.vanished_at ? <span className="chip warn">vanished</span> : null}
                        {t.transfer_id ? <span className="chip info">transfer</span> : null}
                        {t.transfer_suggestions.length > 0 ? <span className="chip warn">transfer?</span> : null}
                        {t.has_attachment ? <span className="chip ok">receipt</span> : null}
                        {t.status === "reviewed" && tab === "all" ? <span className="chip ok">✓</span> : null}
                        {t.tags.map((g) => (
                          <span key={g} className="chip">{g}</span>
                        ))}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {tab === "all" && list.data && list.data.total > list.data.per && (
            <div className="flex items-center justify-between px-3 py-2 text-[12px] border-t border-line">
              <button className="btn sm" disabled={list.data.page <= 1} onClick={() => setFilter("page", String(list.data!.page - 1))}>
                ← prev
              </button>
              <span className="muted num">
                page {list.data.page} of {Math.ceil(list.data.total / list.data.per)}
              </span>
              <button className="btn sm" disabled={list.data.page * list.data.per >= list.data.total} onClick={() => setFilter("page", String(list.data!.page + 1))}>
                next →
              </button>
            </div>
          )}
        </div>

        {current && (
          <Detail
            t={current}
            onSplits={(splits, review) => saveSplits.mutateAsync({ id: current.id, splits, review }).then(() => undefined)}
            onPatch={(body) => patchTxn.mutate({ id: current.id, body })}
            onReview={() => review.mutate(current.id)}
            onUnreview={() => unreview.mutate(current.id)}
            onBiz={() => toggleBiz.mutate(current)}
            onTransfer={(id, act) => transferAct.mutate({ id, act })}
            onDelete={() => remove.mutate(current.id)}
            openMode={(m) => dispatch({ type: "key", key: m })}
            onFilterPayee={() => { setFilter("tab", "all"); setFilter("payee", String(current.payee_id ?? "")); }}
            onRemoveAttachment={(id) => removeAttachment.mutate(id)}
          />
        )}
      </div>

      <input
        ref={fileRef}
        type="file"
        className="hidden"
        accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.txt,.eml,.html"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f && current) upload.mutate({ id: current.id, file: f });
          else dispatch({ type: "close" });
        }}
      />
      {st.mode === "split" && current && (
        <Modal title={<span>Split · {current.payee_name || current.payee_raw} · <Money cents={current.amount_cents} /></span>} onClose={() => dispatch({ type: "close" })} width="max-w-5xl">
          <SplitEditor txn={current} onCancel={() => dispatch({ type: "close" })} onSave={(splits, review) => saveSplits.mutateAsync({ id: current.id, splits, review }).then(() => undefined)} />
        </Modal>
      )}
      {(st.mode === "categorize" || quickCat) && current && (
        <div className="fixed inset-0 z-40 flex items-start justify-center pt-24" onMouseDown={() => { setQuickCat(false); dispatch({ type: "close" }); }}>
          <div onMouseDown={(e) => e.stopPropagation()}>
            <CategoryPicker value={current.splits[0]?.category_id ?? null} onClose={() => { setQuickCat(false); dispatch({ type: "close" }); }} onPick={(c) => quickCategorize.mutate({ id: current.id, category_id: c?.id ?? null })} />
          </div>
        </div>
      )}
      {st.mode === "rule" && current && <RuleModal txn={current} onClose={() => dispatch({ type: "close" })} />}
      {st.mode === "note" && current && (
        <NoteModal t={current} onClose={() => dispatch({ type: "close" })} onSave={(notes, tags) => { patchTxn.mutate({ id: current.id, body: { notes, tags } }); dispatch({ type: "close" }); }} />
      )}
    </Page>
  );
}

function SplitSummary({ t }: { t: Txn }) {
  if (t.splits.length === 0) return <span className="text-dim">—</span>;
  if (t.splits.length === 1) {
    const s = t.splits[0];
    return (
      <span className="flex items-center gap-1">
        <span className={s.category_name ? "" : "text-dim"}>{s.category_name ?? "uncategorized"}</span>
        {s.business ? <span className="chip biz">biz{s.effective_line ? ` ${s.effective_line}` : ""}</span> : null}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1">
      <span>{t.splits.length} splits</span>
      {t.is_business ? <span className="chip biz">part biz</span> : null}
    </span>
  );
}

function BulkCategory({ onPick }: { onPick: (id: number | null) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative">
      <button className="btn sm" onClick={() => setOpen(!open)}>
        categorize…
      </button>
      {open && (
        <div className="absolute z-30 top-full mt-1 left-0">
          <CategoryPicker value={null} onClose={() => setOpen(false)} onPick={(c) => { onPick(c?.id ?? null); setOpen(false); }} />
        </div>
      )}
    </span>
  );
}

function NoteModal({ t, onClose, onSave }: { t: Txn; onClose: () => void; onSave: (notes: string, tags: string[]) => void }) {
  const [notes, setNotes] = useState(t.notes);
  const [tags, setTags] = useState(t.tags.join(", "));
  return (
    <Modal title="Note + tags" onClose={onClose}>
      <div className="space-y-3" onKeyDown={(e) => e.stopPropagation()}>
        <textarea className="inp h-24" autoFocus value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="what was this for?" />
        <input className="inp" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tags, comma separated" />
        <div className="flex justify-end gap-2">
          <button className="btn ghost" onClick={onClose}>cancel</button>
          <button className="btn primary" onClick={() => onSave(notes, tags.split(",").map((s) => s.trim()).filter(Boolean))}>Save</button>
        </div>
      </div>
    </Modal>
  );
}

function Detail({ t, onSplits, onPatch, onReview, onUnreview, onBiz, onTransfer, onDelete, openMode, onFilterPayee, onRemoveAttachment }: {
  t: Txn;
  onSplits: (s: Split[], review: boolean) => Promise<void>;
  onPatch: (body: Record<string, unknown>) => void;
  onReview: () => void;
  onUnreview: () => void;
  onBiz: () => void;
  onTransfer: (id: number, act: "confirm" | "reject") => void;
  onDelete: () => void;
  openMode: (m: string) => void;
  onFilterPayee: () => void;
  onRemoveAttachment: (id: number) => void;
}) {
  const full = useQuery({ queryKey: ["txn", t.id, t.has_attachment], queryFn: () => get<Txn>(`/api/transactions/${t.id}`), enabled: !!t.has_attachment });
  const [payee, setPayee] = useState(t.payee_name ?? "");
  useEffect(() => setPayee(t.payee_name ?? ""), [t.id, t.payee_name]);
  const [editingPayee, setEditingPayee] = useState(false);
  void onSplits;
  return (
    <aside className="card w-[400px] shrink-0 sticky top-5 max-h-[calc(100vh-190px)] overflow-auto">
      <div className="px-4 py-3 border-b border-line">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {editingPayee ? (
              <input
                className="inp"
                autoFocus
                value={payee}
                onChange={(e) => setPayee(e.target.value)}
                onKeyDown={(e) => {
                  e.stopPropagation();
                  if (e.key === "Enter") { onPatch({ payee_name: payee }); setEditingPayee(false); }
                  if (e.key === "Escape") setEditingPayee(false);
                }}
                onBlur={() => setEditingPayee(false)}
              />
            ) : (
              <button className="font-medium text-left hover:text-accent truncate max-w-full" onClick={() => setEditingPayee(true)} title="rename payee">
                {t.payee_name || t.payee_raw || "(no payee)"}
              </button>
            )}
            <div className="text-[11px] text-dim truncate">{t.payee_raw || t.description_raw}</div>
            {t.memo_raw && <div className="text-[11px] text-dim truncate">{t.memo_raw}</div>}
          </div>
          <div className="text-right shrink-0">
            <div className="text-lg"><Money cents={t.amount_cents} /></div>
            <div className="text-[11px] text-muted num">{fmtDate(t.date)}{t.pending ? " · pending" : t.posted_date && t.posted_date !== t.date ? ` · posted ${fmtDate(t.posted_date)}` : ""}</div>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
          <span className="chip">{t.account_name}</span>
          <span className="chip">{t.source}</span>
          {t.status === "reviewed" ? <span className="chip ok">reviewed</span> : <span className="chip warn">new</span>}
          {t.is_business ? <span className="chip biz">business</span> : null}
          <button className="chip hover:text-fg" onClick={onFilterPayee} title="all transactions with this payee">history</button>
        </div>
      </div>

      {t.transfer_suggestions.length > 0 && (
        <div className="px-4 py-3 border-b border-line bg-panel-2">
          {t.transfer_suggestions.map((s) => (
            <div key={s.id} className="text-[12px] space-y-1">
              <div>
                Looks like a transfer <b>{s.out_account_name}</b> → <b>{s.in_account_name}</b> ({fmtDate(s.out_date)} / {fmtDate(s.in_date)}).
              </div>
              <div className="flex gap-2">
                <button className="btn sm primary" onClick={() => onTransfer(s.id, "confirm")}>Yes, transfer</button>
                <button className="btn sm" onClick={() => onTransfer(s.id, "reject")}>No</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {t.vanished_at && (
        <div className="px-4 py-2 border-b border-line text-[12px] text-warn">
          The bank stopped sending this pending charge. If it re-posted as another row, delete this one; a hold that never posted is removed automatically after a week.
        </div>
      )}

      <div className="px-4 py-3 border-b border-line space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-muted">Splits</span>
          <span className="flex gap-1">
            <button className="btn sm" onClick={() => openMode("c")}>category <kbd>c</kbd></button>
            <button className="btn sm" onClick={onBiz}>{t.is_business ? "→ personal" : "→ business"} <kbd>b</kbd></button>
            <button className="btn sm" onClick={() => openMode("s")}>split <kbd>s</kbd></button>
          </span>
        </div>
        {t.splits.map((s, i) => (
          <div key={s.id ?? i} className="flex items-center justify-between text-[12px]">
            <span className="flex items-center gap-1 min-w-0">
              <span className={`truncate ${s.category_name ? "" : "text-dim"}`}>{s.category_name ?? "uncategorized"}</span>
              {s.business ? <span className="chip biz">biz{s.effective_line ? ` · ${s.effective_line}` : ""}{s.deductible_pct !== 100 ? ` · ${s.deductible_pct}%` : ""}</span> : null}
              {s.note && <span className="text-dim truncate">· {s.note}</span>}
              {s.set_by && s.set_by !== "user" && <span className="text-[10px] text-dim">{s.set_by}</span>}
            </span>
            <Money cents={s.amount_cents} tone={false} />
          </div>
        ))}
        {!!t.is_business && t.splits.some((s) => s.business && !s.effective_line) && <div className="text-[11px] text-warn">business split with no Schedule C line</div>}
      </div>

      <div className="px-4 py-3 border-b border-line text-[12px] space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-muted">Notes + tags</span>
          <button className="btn sm" onClick={() => openMode("n")}>edit <kbd>n</kbd></button>
        </div>
        {t.notes ? <div className="whitespace-pre-wrap">{t.notes}</div> : <div className="text-dim">no note</div>}
        {t.tags.length > 0 && <div className="flex gap-1">{t.tags.map((g) => <span key={g} className="chip">{g}</span>)}</div>}
      </div>

      <div className="px-4 py-3 border-b border-line text-[12px] space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-wider text-muted">Receipts</span>
          <button className="btn sm" onClick={() => openMode("a")}>attach <kbd>a</kbd></button>
        </div>
        {(full.data?.attachments ?? []).map((a) => (
          <div key={a.id} className="flex items-center justify-between gap-2">
            <a className="truncate hover:text-accent" href={`/api/attachments/${a.id}`} target="_blank" rel="noreferrer">{a.original_name}</a>
            <span className="flex items-center gap-2 shrink-0">
              <span className="text-dim num">{(a.bytes / 1024).toFixed(0)} KB</span>
              <button className="btn ghost sm" onClick={() => onRemoveAttachment(a.id)}>×</button>
            </span>
          </div>
        ))}
        {!t.has_attachment && <div className={t.is_business && t.amount_cents <= -7500 ? "text-warn" : "text-dim"}>{t.is_business && t.amount_cents <= -7500 ? "business expense over $75 with no receipt" : "no receipt"}</div>}
      </div>

      <div className="px-4 py-3 flex flex-wrap gap-2">
        {t.status === "new" ? (
          <button className="btn primary" onClick={onReview}>Mark reviewed <kbd>⏎</kbd></button>
        ) : (
          <button className="btn" onClick={onUnreview}>Back to inbox <kbd>u</kbd></button>
        )}
        <button className="btn" onClick={() => openMode("r")}>Make a rule <kbd>r</kbd></button>
        {t.source !== "simplefin" && (
          <button className="btn danger ml-auto" onClick={() => { if (confirm(`Delete this ${t.source} transaction of ${fmt(t.amount_cents)}?`)) onDelete(); }}>delete</button>
        )}
      </div>
    </aside>
  );
}
