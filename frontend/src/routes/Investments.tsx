import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, del, get, post } from "../api/client";
import { useAccounts } from "../api/hooks";
import { Card, Empty, ErrorLine, Field, Modal, Money, Page, Stat, fmtDate, fmtWhen } from "../components/ui";
import { e8, fmt } from "../lib/money";

type Instrument = { id: number; symbol: string; name: string; kind: string; price_source: string; coingecko_id: string | null; trades: number; price: { price_e8: number; date: string; source: string; stale: boolean } | null };
type Position = { account_id: number; account_name: string; instrument_id: number; symbol: string; name: string; kind: string; qty_e8: number; cost_cents: number; price_e8: number; price_date: string | null; price_source: string | null; price_stale: boolean; value_cents: number; unrealized_cents: number | null; avg_cost_e8: number; weight: number; lots: { trade_id: number; date: string; qty_e8: number; cost_cents: number }[] };
type Holdings = { positions: Position[]; total_value_cents: number; securities_value_cents: number; collectibles_cents: number; total_cost_cents: number; unrealized_cents: number; by_kind: Record<string, number>; warnings: { trade_id: number; msg: string }[]; income_ytd_cents: number };
type Trade = { id: number; account_id: number; account_name: string; instrument_id: number; symbol: string; date: string; kind: string; qty_e8: number; price_e8: number; fee_cents: number; total_cents: number; note: string };
type Realized = { year: number; rows: { sell_trade_id: number; date: string; symbol: string; qty_e8: number; proceeds_cents: number; cost_cents: number; gain_cents: number; long_term: boolean }[]; short_term_cents: number; long_term_cents: number; income_cents: number };
type Search = { equities: { symbol: string; name: string; kind: string; exchange: string }[]; crypto: { coingecko_id: string; symbol: string; name: string }[]; equities_error?: string; crypto_error?: string };

const KIND_COLORS: Record<string, string> = { stock: "#60a5fa", etf: "#34d399", crypto: "#fbbf24", cash: "#8b96a8", other: "#a78bfa", collectibles: "#f472b6" };

export default function Investments() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "holdings";
  const holdings = useQuery({ queryKey: ["holdings"], queryFn: () => get<Holdings>("/api/holdings") });
  const instruments = useQuery({ queryKey: ["instruments"], queryFn: () => get<Instrument[]>("/api/instruments") });
  const [err, setErr] = useState<unknown>(null);
  const [addInst, setAddInst] = useState(false);
  const [addTrade, setAddTrade] = useState<Partial<Trade> | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const inval = () => { qc.invalidateQueries({ queryKey: ["holdings"] }); qc.invalidateQueries({ queryKey: ["instruments"] }); qc.invalidateQueries({ queryKey: ["trades"] }); qc.invalidateQueries({ queryKey: ["realized"] }); qc.invalidateQueries({ queryKey: ["networth"] }); };
  const refresh = useMutation({ mutationFn: () => post("/api/prices/refresh"), onSuccess: () => setTimeout(inval, 4000), onError: setErr });
  const h = holdings.data;

  return (
    <Page
      title="Investments"
      actions={
        <>
          <div className="flex gap-1 mr-2">
            {["holdings", "trades", "realized", "instruments", "collectibles"].map((t) => (
              <button key={t} className={`btn sm ${tab === t ? "primary" : "ghost"}`} onClick={() => setParams({ tab: t }, { replace: true })}>{t}</button>
            ))}
          </div>
          <button className="btn sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>Refresh prices</button>
          <button className="btn sm" onClick={() => setImportOpen(true)}>Import broker CSV</button>
          <button className="btn sm" onClick={() => setAddInst(true)}>+ instrument</button>
          <button className="btn sm primary" onClick={() => setAddTrade({ kind: "buy", date: new Date().toISOString().slice(0, 10) })}>+ trade</button>
        </>
      }
    >
      <ErrorLine error={err} />
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Portfolio value" value={<Money cents={h?.total_value_cents} tone={false} />} sub={h ? (h.collectibles_cents ? `securities ${fmt(h.securities_value_cents)} · collectibles ${fmt(h.collectibles_cents)}` : `cost ${fmt(h.total_cost_cents)}`) : ""} />
        <Stat label="Unrealized (securities)" value={<Money cents={h?.unrealized_cents} sign />} sub={h && h.total_cost_cents ? `${((h.unrealized_cents / h.total_cost_cents) * 100).toFixed(1)}% on ${fmt(h.total_cost_cents)} cost` : "collectibles have no cost basis here"} tone={h && h.unrealized_cents < 0 ? "bad" : h && h.unrealized_cents > 0 ? "pos" : undefined} />
        <Stat label="Dividends + interest YTD" value={<Money cents={h?.income_ytd_cents} tone={false} />} />
        <div className="card px-4 py-3">
          <div className="text-[11px] uppercase tracking-wider text-muted">Allocation</div>
          <div className="flex h-3 rounded overflow-hidden mt-2 bg-line">
            {h && Object.entries(h.by_kind).map(([k, v]) => <div key={k} style={{ width: `${(v / (h.total_value_cents || 1)) * 100}%`, background: KIND_COLORS[k] ?? "#888" }} title={`${k} ${fmt(v)}`} />)}
          </div>
          <div className="flex flex-wrap gap-2 mt-1 text-[11px]">
            {h && Object.entries(h.by_kind).map(([k, v]) => <span key={k} className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ background: KIND_COLORS[k] ?? "#888" }} />{k} {h.total_value_cents ? ((v / h.total_value_cents) * 100).toFixed(0) : 0}%</span>)}
          </div>
        </div>
      </div>
      {h && h.warnings.length > 0 && <div className="card px-4 py-2 text-[12px] text-warn">{h.warnings.map((w) => <div key={w.trade_id}>trade #{w.trade_id}: {w.msg}</div>)}</div>}

      {tab === "holdings" && (
        <Card title="Holdings">
          <table className="tbl">
            <thead><tr><th>Symbol</th><th>Account</th><th className="r">Qty</th><th className="r">Avg cost</th><th className="r">Price</th><th className="r">Value</th><th className="r">Unrealized</th><th className="r">Weight</th><th>Price as of</th></tr></thead>
            <tbody>
              {(h?.positions ?? []).map((p) => (
                <tr key={`${p.account_id}-${p.instrument_id}`}>
                  <td><b>{p.symbol}</b> <span className="text-muted text-[11px]">{p.name}</span> <span className="chip ml-1">{p.kind}</span></td>
                  <td className="text-muted">{p.account_name}</td>
                  <td className="r num">{e8(p.qty_e8, p.kind === "crypto" ? 8 : 4)}</td>
                  <td className="r num text-muted">${e8(p.avg_cost_e8, 2)}</td>
                  <td className="r num">{p.price_e8 ? `$${e8(p.price_e8, p.kind === "crypto" && p.price_e8 < 1e8 ? 6 : 2)}` : <span className="text-warn">no price</span>}</td>
                  <td className="r"><Money cents={p.value_cents} tone={false} /></td>
                  <td className="r"><Money cents={p.unrealized_cents} sign /></td>
                  <td className="r num text-muted">{(p.weight * 100).toFixed(1)}%</td>
                  <td>{p.price_date ? <span className={`chip ${p.price_stale ? "warn" : ""}`}>{fmtDate(p.price_date)} · {p.price_source}{p.price_stale ? " · stale" : ""}</span> : <span className="chip bad">none</span>}</td>
                </tr>
              ))}
              {h && h.positions.length === 0 && <tr><td colSpan={9}><Empty>No holdings yet. Add an instrument, then a buy trade — or import a broker CSV.</Empty></td></tr>}
            </tbody>
          </table>
        </Card>
      )}
      {tab === "trades" && <TradesTab onEdit={(t) => setAddTrade(t)} onChange={inval} />}
      {tab === "realized" && <RealizedTab />}
      {tab === "instruments" && <InstrumentsTab rows={instruments.data ?? []} onChange={inval} />}
      {tab === "collectibles" && <CollectiblesTab />}

      {addInst && <InstrumentModal onClose={() => setAddInst(false)} onDone={() => { setAddInst(false); inval(); }} />}
      {addTrade && <TradeModal draft={addTrade} instruments={instruments.data ?? []} onClose={() => setAddTrade(null)} onDone={() => { setAddTrade(null); inval(); }} />}
      {importOpen && <BrokerImportModal onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); inval(); }} />}
    </Page>
  );
}

function TradesTab({ onEdit, onChange }: { onEdit: (t: Trade) => void; onChange: () => void }) {
  const trades = useQuery({ queryKey: ["trades"], queryFn: () => get<Trade[]>("/api/trades") });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/trades/${id}`), onSuccess: onChange });
  return (
    <Card title="Trades">
      <table className="tbl">
        <thead><tr><th>Date</th><th>Account</th><th>Symbol</th><th>Kind</th><th className="r">Qty</th><th className="r">Price</th><th className="r">Fee</th><th className="r">Cash</th><th>Note</th><th></th></tr></thead>
        <tbody>
          {(trades.data ?? []).map((t) => (
            <tr key={t.id}>
              <td className="num text-muted">{fmtDate(t.date)}</td><td className="text-muted">{t.account_name}</td><td><b>{t.symbol}</b></td><td><span className="chip">{t.kind}</span></td>
              <td className="r num">{e8(t.qty_e8, 8)}</td><td className="r num">{t.price_e8 ? `$${e8(t.price_e8, 2)}` : ""}</td><td className="r num text-muted">{t.fee_cents ? fmt(t.fee_cents) : ""}</td>
              <td className="r"><Money cents={t.total_cents} /></td><td className="text-muted">{t.note}</td>
              <td className="r whitespace-nowrap"><button className="btn sm ghost" onClick={() => onEdit(t)}>edit</button><button className="btn sm ghost" onClick={() => { if (confirm("Delete this trade?")) remove.mutate(t.id); }}>delete</button></td>
            </tr>
          ))}
          {trades.data && trades.data.length === 0 && <tr><td colSpan={10}><Empty>No trades.</Empty></td></tr>}
        </tbody>
      </table>
    </Card>
  );
}

function RealizedTab() {
  const [year, setYear] = useState(new Date().getFullYear());
  const q = useQuery({ queryKey: ["realized", year], queryFn: () => get<Realized>(`/api/realized?year=${year}`) });
  const d = q.data;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <select className="inp w-24 num" value={year} onChange={(e) => setYear(parseInt(e.target.value, 10))}>{[year - 2, year - 1, year, year + 1].map((y) => <option key={y} value={y}>{y}</option>)}</select>
        <span className="text-[12px]">short-term <Money cents={d?.short_term_cents} sign /> · long-term <Money cents={d?.long_term_cents} sign /> · dividends/interest <Money cents={d?.income_cents} tone={false} /></span>
      </div>
      <Card title={`Realized gains · ${year}`}>
        <table className="tbl">
          <thead><tr><th>Date</th><th>Symbol</th><th className="r">Qty</th><th className="r">Proceeds</th><th className="r">Cost</th><th className="r">Gain</th><th>Term</th></tr></thead>
          <tbody>
            {(d?.rows ?? []).map((r) => (
              <tr key={r.sell_trade_id}><td className="num text-muted">{fmtDate(r.date)}</td><td><b>{r.symbol}</b></td><td className="r num">{e8(r.qty_e8, 8)}</td><td className="r"><Money cents={r.proceeds_cents} tone={false} /></td><td className="r"><Money cents={r.cost_cents} tone={false} /></td><td className="r"><Money cents={r.gain_cents} sign /></td><td><span className="chip">{r.long_term ? "long" : "short"}</span></td></tr>
            ))}
            {d && d.rows.length === 0 && <tr><td colSpan={7}><Empty>No sales in {year}.</Empty></td></tr>}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function InstrumentsTab({ rows, onChange }: { rows: Instrument[]; onChange: () => void }) {
  const [manual, setManual] = useState<{ id: number; price: string } | null>(null);
  const setPrice = useMutation({ mutationFn: ({ id, price }: { id: number; price: string }) => post(`/api/prices/${id}/manual`, { price }), onSuccess: () => { setManual(null); onChange(); } });
  return (
    <Card title="Instruments">
      <table className="tbl">
        <thead><tr><th>Symbol</th><th>Name</th><th>Kind</th><th>Source</th><th className="r">Last price</th><th>As of</th><th className="r">Trades</th><th></th></tr></thead>
        <tbody>
          {rows.map((i) => (
            <tr key={i.id}>
              <td><b>{i.symbol}</b></td><td className="text-muted">{i.name}{i.coingecko_id ? <span className="text-dim text-[11px]"> · {i.coingecko_id}</span> : null}</td><td><span className="chip">{i.kind}</span></td><td className="text-muted">{i.price_source}</td>
              <td className="r num">{i.price ? `$${e8(i.price.price_e8, i.kind === "crypto" && i.price.price_e8 < 1e8 ? 6 : 2)}` : "—"}</td>
              <td>{i.price ? <span className={`chip ${i.price.stale ? "warn" : ""}`}>{fmtDate(i.price.date)} · {i.price.source}</span> : <span className="chip bad">no price</span>}</td>
              <td className="r num text-muted">{i.trades}</td>
              <td className="r">
                {manual?.id === i.id ? (
                  <span className="flex gap-1 justify-end"><input className="inp num w-28" autoFocus value={manual.price} onChange={(e) => setManual({ id: i.id, price: e.target.value })} onKeyDown={(e) => { if (e.key === "Enter") setPrice.mutate(manual); if (e.key === "Escape") setManual(null); }} /><button className="btn sm" onClick={() => setPrice.mutate(manual)}>set</button></span>
                ) : <button className="btn sm ghost" onClick={() => setManual({ id: i.id, price: i.price ? e8(i.price.price_e8, 2) : "" })}>set price</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

type Collectible = { id: number; account_id: number; account_name: string; name: string; set_name: string; number: string; condition: string; quantity: number; unit_value_cents: number; value_cents: number; cost_cents: number | null; imported_at: string };
type Collectibles = { rows: Collectible[]; accounts: { id: number; name: string; manual_value_cents: number | null; items: number; imported_at: string | null }[]; history: Record<number, { date: string; balance_cents: number }[]>; total_value_cents: number; total_cost_cents: number; items: number };

type ShareStatus = { connected: boolean; share_url: string | null; handle: string | null; last: { at: string; username: string; value_cents: number; items: number; items_value_cents: number; total_cards: number; total_sealed: number; total_graded: number; product_keys: string[] } | null; job: { running: number; next_due_at: string | null; last_ok: number | null; last_error: string | null } | null };

/** React 19 rewrites any javascript: href to a throw, so the real URL is set
 *  on the DOM node after React is done with it; the href prop stays "#" so
 *  React never touches the attribute again. The script itself comes from the
 *  server, built for this page's origin. */
function BookmarkletLink({ handle }: { handle: string }) {
  const ref = useRef<HTMLAnchorElement>(null);
  const q = useQuery({ queryKey: ["bookmarklet", handle, window.location.origin], queryFn: () => get<{ href: string }>(`/api/collectr/bookmarklet?origin=${encodeURIComponent(window.location.origin)}`) });
  const js = q.data?.href ?? "";
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (js) ref.current?.setAttribute("href", js); }, [js]);
  return (
    <>
      <a ref={ref} href="#" className="btn sm primary" draggable title="drag me to the bookmarks bar" onClick={(e) => { e.preventDefault(); alert("Drag this button onto your bookmarks bar (Ctrl+Shift+B shows the bar). Then open app.getcollectr.com, signed in, and click the bookmark."); }}>Grab Collectr</a>
      <button className="btn sm" disabled={!js} onClick={async () => { try { await navigator.clipboard.writeText(js); setCopied(true); setTimeout(() => setCopied(false), 3000); } catch { prompt("Copy this, then: right-click the bookmarks bar > Add page > paste it as the URL", js); } }}>{copied ? "copied" : "copy bookmark code"}</button>
    </>
  );
}

function CollectiblesTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["collectibles"], queryFn: () => get<Collectibles>("/api/collectibles") });
  const share = useQuery({ queryKey: ["collectr-status"], queryFn: () => get<ShareStatus>("/api/collectr/status"), refetchInterval: 5000 });
  const [importing, setImporting] = useState(false);
  const [link, setLink] = useState("");
  const [pasted, setPasted] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const inval = () => { qc.invalidateQueries({ queryKey: ["collectibles"] }); qc.invalidateQueries({ queryKey: ["collectr-status"] }); qc.invalidateQueries({ queryKey: ["accounts"] }); qc.invalidateQueries({ queryKey: ["networth-now"] }); qc.invalidateQueries({ queryKey: ["summary"] }); };
  const connect = useMutation({ mutationFn: () => post<{ direct: { blocked?: boolean; empty?: boolean; note?: string; error?: string; items?: number } | null }>("/api/collectr/connect", { share_url: link }), onSuccess: (r) => { setLink(""); setErr(null); setNote(r.direct?.note ?? r.direct?.error ?? (r.direct?.items !== undefined ? `read ${r.direct.items} items directly` : null)); inval(); }, onError: setErr });
  const paste = useMutation({ mutationFn: () => post<{ items: number; value_cents: number; product_keys: string[] }>("/api/collectr/paste", { payload: pasted }), onSuccess: (r) => { setPasted(""); setErr(null); setNote(`imported ${r.items} items · fields seen: ${r.product_keys.join(", ")}`); inval(); }, onError: setErr });
  const disconnect = useMutation({ mutationFn: () => del("/api/collectr/share"), onSuccess: inval, onError: setErr });
  const d = q.data;
  const s = share.data;
  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Collection value" value={<Money cents={s?.last?.value_cents ?? d?.total_value_cents} tone={false} />} sub={s?.last ? `Collectr's own total · ${s.last.total_cards} cards, ${s.last.total_sealed} sealed, ${s.last.total_graded} graded` : d ? `${d.items} items` : ""} />
        <Stat label="Items listed" value={<span className="num">{d?.rows.length ?? "—"}</span>} sub={s?.last && s.last.items_value_cents !== s.last.value_cents ? `rows add to ${fmt(s.last.items_value_cents)}; the total above is Collectr's` : ""} />
        <Stat label="Last sync" value={<span className="text-base">{s?.last ? fmtWhen(s.last.at) : "never"}</span>} sub={s?.job?.next_due_at ? `next ${s.job.next_due_at.replace("T", " ")}` : "daily at 04:10"} tone={s?.job?.last_ok === 0 ? "bad" : undefined} />
        <div className="card px-4 py-3 flex flex-col justify-between">
          <div className="text-[11px] uppercase tracking-wider text-muted">Collectr</div>
          {s?.connected ? (
            <div className="flex flex-wrap gap-2 mt-2 items-center">
              <span className="chip ok">{s.handle}</span>
              {s.handle && <BookmarkletLink handle={s.handle} />}
              <button className="btn sm ghost" onClick={() => setImporting(true)}>CSV instead</button>
              <button className="btn sm ghost" onClick={() => { if (confirm("Forget the Collectr link? The collection rows stay until the next import.")) disconnect.mutate(); }}>forget</button>
            </div>
          ) : (
            <div className="flex gap-2 mt-2">
              <input className="inp" placeholder="paste your Collectr profile/share link" value={link} onChange={(e) => setLink(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && link.trim()) connect.mutate(); }} />
              <button className="btn sm primary" onClick={() => connect.mutate()} disabled={!link.trim() || connect.isPending}>{connect.isPending ? "…" : "Connect"}</button>
            </div>
          )}
          <div className="text-[11px] text-dim mt-1">{note ?? "Drag \"Grab Collectr\" onto your bookmarks bar (or: copy bookmark code, right-click the bar > Add page, paste as URL). Then on app.getcollectr.com click the bookmark. It sends the collection here by itself."}</div>
        </div>
      </div>
      {s?.connected && (
        <Card title="Or paste the JSON by hand">
          <div className="p-4 flex gap-3 items-start">
            <textarea className="inp h-20 num text-[11px]" placeholder='{"user":"you","products":[…' value={pasted} onChange={(e) => setPasted(e.target.value)} />
            <button className="btn primary" disabled={!pasted.trim() || paste.isPending} onClick={() => paste.mutate()}>Import</button>
          </div>
          <div className="px-4 pb-3 text-[11px] text-dim">Normally the bookmark does this for you. Each import replaces the list and adds a point to the value history.</div>
        </Card>
      )}
      <Card title="Collection">
        <table className="tbl">
          <thead><tr><th>Item</th><th>Set</th><th>#</th><th>Condition</th><th className="r">Qty</th><th className="r">Each</th><th className="r">Value</th><th className="r">Paid</th></tr></thead>
          <tbody>
            {(d?.rows ?? []).map((r) => (
              <tr key={r.id}><td>{r.name}</td><td className="text-muted">{r.set_name}</td><td className="num text-muted">{r.number}</td><td className="text-muted">{r.condition}</td><td className="r num">{r.quantity}</td><td className="r"><Money cents={r.unit_value_cents} tone={false} /></td><td className="r"><Money cents={r.value_cents} tone={false} /></td><td className="r text-muted">{r.cost_cents ? fmt(r.cost_cents) : "—"}</td></tr>
            ))}
            {d && d.rows.length === 0 && <tr><td colSpan={8}><Empty>No collection imported yet.</Empty></td></tr>}
          </tbody>
        </table>
      </Card>
      {importing && <CollectrImportModal accounts={d?.accounts ?? []} onClose={() => setImporting(false)} onDone={() => { setImporting(false); inval(); }} />}
    </div>
  );
}

function CollectrImportModal({ accounts, onClose, onDone }: { accounts: Collectibles["accounts"]; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState(accounts[0] ? String(accounts[0].id) : "");
  const [pv, setPv] = useState<{ profile: Record<string, unknown>; rows: { name: string; set_name: string; quantity: number; value_cents: number }[]; total: number; errors: string[]; total_value_cents: number; items: number; sniff: { headers: string[] } } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const preview = useMutation({ mutationFn: async () => { const f = new FormData(); f.append("file", file!); return api<typeof pv>("/api/imports/collectr/preview", { form: f }); }, onSuccess: (r) => { setPv(r); setErr(null); }, onError: setErr });
  const commit = useMutation({ mutationFn: async () => { const f = new FormData(); f.append("file", file!); f.append("account_id", accountId); f.append("profile", JSON.stringify(pv!.profile)); return api("/api/imports/collectr", { form: f }); }, onSuccess: onDone, onError: setErr });
  return (
    <Modal title="Import a Collectr export" onClose={onClose} width="max-w-3xl">
      <div className="space-y-3">
        <div className="flex items-end gap-3">
          <Field label="Account"><select className="inp w-48" value={accountId} onChange={(e) => setAccountId(e.target.value)}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label="CSV"><input type="file" accept=".csv,text/csv" className="text-[12px]" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPv(null); }} /></Field>
          <button className="btn" disabled={!file} onClick={() => preview.mutate()}>Preview</button>
        </div>
        <ErrorLine error={err} />
        {pv && (
          <>
            <div className="text-[12px]"><b className="text-accent">{pv.total}</b> rows · {pv.items} items · <Money cents={pv.total_value_cents} tone={false} />{pv.errors.length ? <span className="text-warn"> · {pv.errors.join("; ")}</span> : null}</div>
            <div className="text-[11px] text-dim num">columns: {pv.sniff.headers.join(", ")}</div>
            <div className="max-h-64 overflow-auto card">
              <table className="tbl"><thead><tr><th>Item</th><th>Set</th><th className="r">Qty</th><th className="r">Value</th></tr></thead>
                <tbody>{pv.rows.map((r, i) => <tr key={i}><td>{r.name}</td><td className="text-muted">{r.set_name}</td><td className="r num">{r.quantity}</td><td className="r"><Money cents={r.value_cents} tone={false} /></td></tr>)}</tbody>
              </table>
            </div>
            <div className="flex justify-end"><button className="btn primary" disabled={pv.total === 0 || !accountId} onClick={() => commit.mutate()}>Replace collection with these {pv.total} rows</button></div>
          </>
        )}
      </div>
    </Modal>
  );
}

function InstrumentModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [q, setQ] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const search = useQuery({ queryKey: ["inst-search", q], queryFn: () => get<Search>(`/api/instruments/search?q=${encodeURIComponent(q)}`), enabled: q.trim().length >= 1 });
  const create = useMutation({ mutationFn: (body: Record<string, unknown>) => post("/api/instruments", body), onSuccess: onDone, onError: setErr });
  return (
    <Modal title="Add instrument" onClose={onClose}>
      <div className="space-y-3">
        <Field label="Search ticker or coin"><input className="inp" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="VTI, AAPL, bitcoin…" /></Field>
        <ErrorLine error={err} />
        {search.isFetching && <div className="text-muted text-[12px]">searching…</div>}
        {search.data && (
          <div className="grid grid-cols-2 gap-3 text-[12px]">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-muted mb-1">Stocks & ETFs</div>
              {search.data.equities_error && <div className="text-warn">{search.data.equities_error}</div>}
              {search.data.equities.map((e) => (
                <button key={e.symbol} className="btn sm w-full justify-between mb-1" onClick={() => create.mutate({ symbol: e.symbol, kind: e.kind, name: e.name })}>
                  <span><b>{e.symbol}</b> <span className="text-muted">{e.name}</span></span><span className="chip">{e.kind}</span>
                </button>
              ))}
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider text-muted mb-1">Crypto (CoinGecko)</div>
              {search.data.crypto_error && <div className="text-warn">{search.data.crypto_error}</div>}
              {search.data.crypto.map((c) => (
                <button key={c.coingecko_id} className="btn sm w-full justify-between mb-1" onClick={() => create.mutate({ symbol: c.symbol, kind: "crypto", name: c.name, coingecko_id: c.coingecko_id })}>
                  <span><b>{c.symbol}</b> <span className="text-muted">{c.name}</span></span><span className="text-dim num">{c.coingecko_id}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="text-[11px] text-dim">Or add by hand with manual pricing: <button className="btn sm ghost" onClick={() => q.trim() && create.mutate({ symbol: q.trim().toUpperCase(), kind: "other", price_source: "manual" })}>add "{q.trim().toUpperCase() || "…"}" as manual</button></div>
      </div>
    </Modal>
  );
}

function TradeModal({ draft, instruments, onClose, onDone }: { draft: Partial<Trade>; instruments: Instrument[]; onClose: () => void; onDone: () => void }) {
  const accounts = useAccounts();
  const [d, setD] = useState({
    account_id: draft.account_id ?? "", instrument_id: draft.instrument_id ?? "", date: draft.date ?? "", kind: draft.kind ?? "buy",
    qty: draft.qty_e8 !== undefined ? e8(draft.qty_e8, 8) : "", price: draft.price_e8 !== undefined ? e8(draft.price_e8, 8) : "",
    fee: draft.fee_cents !== undefined ? (draft.fee_cents / 100).toFixed(2) : "", total: draft.total_cents !== undefined && ["dividend", "interest"].includes(draft.kind ?? "") ? (draft.total_cents / 100).toFixed(2) : "",
    basis: "", note: draft.note ?? "",
  });
  const [err, setErr] = useState<unknown>(null);
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { account_id: Number(d.account_id), instrument_id: Number(d.instrument_id), date: d.date, kind: d.kind, qty: d.qty || "0", price: d.price || "0", fee_cents: Math.round(parseFloat(d.fee || "0") * 100), note: d.note };
      if (["dividend", "interest"].includes(d.kind)) body.total_cents = Math.round(parseFloat(d.total || "0") * 100);
      if (d.kind === "transfer_in" && d.basis) body.cost_basis_cents = Math.round(parseFloat(d.basis) * 100);
      return draft.id ? api(`/api/trades/${draft.id}`, { method: "PATCH", body }) : post("/api/trades", body);
    },
    onSuccess: onDone, onError: setErr,
  });
  const invAccounts = (accounts.data ?? []).filter((a) => ["brokerage", "crypto"].includes(a.kind));
  return (
    <Modal title={draft.id ? "Edit trade" : "New trade"} onClose={onClose}>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Account">
          <select className="inp" value={d.account_id} onChange={(e) => setD({ ...d, account_id: e.target.value })}>
            <option value="">choose…</option>
            {invAccounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
        <Field label="Instrument">
          <select className="inp" value={d.instrument_id} onChange={(e) => setD({ ...d, instrument_id: e.target.value })}>
            <option value="">choose…</option>
            {instruments.map((i) => <option key={i.id} value={i.id}>{i.symbol} {i.name ? `· ${i.name}` : ""}</option>)}
          </select>
        </Field>
        <Field label="Kind">
          <select className="inp" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value })}>
            {["buy", "sell", "transfer_in", "transfer_out", "dividend", "interest", "fee", "split"].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </Field>
        <Field label="Date"><input className="inp num" type="date" value={d.date} onChange={(e) => setD({ ...d, date: e.target.value })} /></Field>
        {!["dividend", "interest", "fee"].includes(d.kind) && <Field label={d.kind === "split" ? "Shares added" : "Quantity"}><input className="inp num" value={d.qty} onChange={(e) => setD({ ...d, qty: e.target.value })} /></Field>}
        {["buy", "sell", "transfer_in", "transfer_out"].includes(d.kind) && <Field label="Price each"><input className="inp num" value={d.price} onChange={(e) => setD({ ...d, price: e.target.value })} /></Field>}
        {["buy", "sell", "fee"].includes(d.kind) && <Field label="Fee $"><input className="inp num" value={d.fee} onChange={(e) => setD({ ...d, fee: e.target.value })} /></Field>}
        {["dividend", "interest"].includes(d.kind) && <Field label="Amount $"><input className="inp num" value={d.total} onChange={(e) => setD({ ...d, total: e.target.value })} /></Field>}
        {d.kind === "transfer_in" && <Field label="Carried cost basis $" hint="total basis of the shares moved in"><input className="inp num" value={d.basis} onChange={(e) => setD({ ...d, basis: e.target.value })} /></Field>}
        <Field label="Note"><input className="inp" value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} /></Field>
      </div>
      <ErrorLine error={err} />
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn ghost" onClick={onClose}>cancel</button>
        <button className="btn primary" disabled={!d.account_id || !d.instrument_id || !d.date} onClick={() => save.mutate()}>Save</button>
      </div>
    </Modal>
  );
}

function BrokerImportModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const accounts = useAccounts();
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState("");
  const [pv, setPv] = useState<{ profile: Record<string, unknown>; rows: { date: string; symbol: string; kind: string; qty_e8: number; price_e8: number; total_cents: number; duplicate: boolean }[]; total: number; errors: string[]; would_insert: number; would_skip: number; new_symbols: string[] } | null>(null);
  const [kinds, setKinds] = useState<Record<string, string>>({});
  const [err, setErr] = useState<unknown>(null);
  const preview = useMutation({
    mutationFn: async () => { const f = new FormData(); f.append("file", file!); f.append("account_id", accountId); return api<typeof pv>("/api/imports/broker-csv/preview", { form: f }); },
    onSuccess: (r) => { setPv(r); setKinds(Object.fromEntries((r?.new_symbols ?? []).map((s) => [s, "stock"]))); }, onError: setErr,
  });
  const commit = useMutation({
    mutationFn: async () => { const f = new FormData(); f.append("file", file!); f.append("account_id", accountId); f.append("profile", JSON.stringify(pv!.profile)); f.append("symbol_kinds", JSON.stringify(Object.fromEntries(Object.entries(kinds).map(([s, k]) => [s, { kind: k }])))); return api("/api/imports/broker-csv", { form: f }); },
    onSuccess: onDone, onError: setErr,
  });
  return (
    <Modal title="Import broker CSV" onClose={onClose} width="max-w-3xl">
      <div className="space-y-3">
        <div className="flex items-end gap-3">
          <Field label="Account"><select className="inp w-48" value={accountId} onChange={(e) => setAccountId(e.target.value)}><option value="">choose…</option>{(accounts.data ?? []).filter((a) => ["brokerage", "crypto"].includes(a.kind)).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          <Field label="File"><input type="file" accept=".csv" className="text-[12px]" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
          <button className="btn" disabled={!file || !accountId} onClick={() => preview.mutate()}>Preview</button>
        </div>
        <ErrorLine error={err} />
        {pv && (
          <>
            <div className="text-[12px]"><b className="text-accent">{pv.would_insert}</b> new · {pv.would_skip} already imported · {pv.total} rows{pv.errors.length ? <span className="text-warn"> · {pv.errors.join("; ")}</span> : null}</div>
            {pv.new_symbols.length > 0 && (
              <div className="flex flex-wrap gap-2 text-[12px]">
                new symbols:
                {pv.new_symbols.map((s) => <span key={s} className="flex items-center gap-1"><b>{s}</b><select className="inp w-24" value={kinds[s]} onChange={(e) => setKinds({ ...kinds, [s]: e.target.value })}><option value="stock">stock</option><option value="etf">etf</option><option value="crypto">crypto</option></select></span>)}
                <span className="text-dim">(crypto needs a CoinGecko id afterwards — set it in Instruments)</span>
              </div>
            )}
            <div className="max-h-64 overflow-auto card">
              <table className="tbl"><thead><tr><th>Date</th><th>Symbol</th><th>Kind</th><th className="r">Qty</th><th className="r">Price</th><th className="r">Cash</th><th></th></tr></thead>
                <tbody>{pv.rows.map((r, i) => <tr key={i} className={r.duplicate ? "opacity-50" : ""}><td className="num">{r.date}</td><td>{r.symbol}</td><td>{r.kind}</td><td className="r num">{e8(r.qty_e8, 8)}</td><td className="r num">{e8(r.price_e8, 2)}</td><td className="r"><Money cents={r.total_cents} /></td><td>{r.duplicate && <span className="chip">dup</span>}</td></tr>)}</tbody>
              </table>
            </div>
            <div className="flex justify-end"><button className="btn primary" disabled={pv.would_insert === 0} onClick={() => commit.mutate()}>Import {pv.would_insert} trades</button></div>
          </>
        )}
      </div>
    </Modal>
  );
}
