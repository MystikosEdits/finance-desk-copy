import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { get, post } from "../api/client";
import { Card, Empty, Money, Page, Stat } from "../components/ui";
import CreditScoreCard from "../components/CreditScore";
import { e8, fmt } from "../lib/money";

type Snap = { date: string; assets_cents: number; liabilities_cents: number; net_cents: number };
type Now = { date: string; assets_cents: number; liabilities_cents: number; net_cents: number; accounts: { account_id: number; name: string; kind: string; value_cents: number; source: string; liability: boolean }[]; holdings: { symbol: string; qty_e8: number; price_e8: number; value_cents: number; cost_cents: number; price_date: string | null }[]; stale_prices: string[] };

export default function NetWorth() {
  const qc = useQueryClient();
  const [range, setRange] = useState("1y");
  const frm = (() => { const d = new Date(); if (range === "3m") d.setMonth(d.getMonth() - 3); else if (range === "1y") d.setFullYear(d.getFullYear() - 1); else if (range === "all") return "2000-01-01"; else d.setMonth(d.getMonth() - 1); return d.toISOString().slice(0, 10); })();
  const hist = useQuery({ queryKey: ["networth", frm], queryFn: () => get<Snap[]>(`/api/networth?frm=${frm}`) });
  const now = useQuery({ queryKey: ["networth-now"], queryFn: () => get<Now>("/api/networth/now") });
  const snap = useMutation({ mutationFn: () => post("/api/networth/snapshot"), onSuccess: () => { qc.invalidateQueries({ queryKey: ["networth"] }); qc.invalidateQueries({ queryKey: ["summary"] }); } });
  const n = now.data;
  const data = (hist.data ?? []).map((s) => ({ date: s.date.slice(5), net: s.net_cents / 100, assets: s.assets_cents / 100, liabilities: -s.liabilities_cents / 100 }));
  const first = hist.data?.[0];
  const change = first && n ? n.net_cents - first.net_cents : null;
  const groups = [["Cash", ["checking", "savings", "cash"]], ["Investments", ["brokerage", "crypto"]], ["Other assets", ["asset"]], ["Debts", ["credit", "loan", "liability"]]] as const;

  return (
    <Page title="Net worth" actions={<>
      <div className="flex gap-1">{["1m", "3m", "1y", "all"].map((r) => <button key={r} className={`btn sm ${range === r ? "primary" : "ghost"}`} onClick={() => setRange(r)}>{r}</button>)}</div>
      <button className="btn sm" onClick={() => snap.mutate()} disabled={snap.isPending}>Snapshot now</button>
    </>}>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Net worth today" value={<Money cents={n?.net_cents} />} sub={change !== null && first ? `${change >= 0 ? "+" : ""}${fmt(change)} since ${first.date}` : "live from accounts + holdings"} />
        <Stat label="Assets" value={<Money cents={n?.assets_cents} tone={false} />} />
        <Stat label="Liabilities" value={<Money cents={n?.liabilities_cents} tone={false} />} />
        <Stat label="Snapshots" value={<span className="num">{hist.data?.length ?? 0}</span>} sub={n && n.stale_prices.length ? `stale prices: ${n.stale_prices.join(", ")}` : "taken nightly after prices"} tone={n && n.stale_prices.length ? "warn" : undefined} />
      </div>
      <Card title="History">
        <div className="h-72 p-2">
          {data.length > 1 ? (
            <ResponsiveContainer>
              <AreaChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                <defs><linearGradient id="nw" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#34d399" stopOpacity={0.35} /><stop offset="100%" stopColor="#34d399" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid stroke="#1f2a3d" vertical={false} />
                <XAxis dataKey="date" stroke="#5b667a" fontSize={11} minTickGap={30} />
                <YAxis stroke="#5b667a" fontSize={11} tickFormatter={(v) => `$${Math.abs(v) >= 1000 ? (v / 1000).toFixed(1).replace(/.0$/, "") + "k" : v}`} domain={["auto", "auto"]} />
                <Tooltip contentStyle={{ background: "#151d2c", border: "1px solid #2a3850", fontSize: 12 }} formatter={(v) => `$${Number(v).toLocaleString()}`} />
                <Area type="monotone" dataKey="net" stroke="#34d399" fill="url(#nw)" strokeWidth={2} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : <Empty>Net worth is snapshotted every night after prices refresh. The chart starts once there are two snapshots.</Empty>}
        </div>
      </Card>
      <CreditScoreCard />
      <div className="grid grid-cols-2 gap-4">
        <Card title="By account, today">
          <table className="tbl">
            <tbody>
              {groups.map(([title, kinds]) => {
                const rows = (n?.accounts ?? []).filter((a) => (kinds as readonly string[]).includes(a.kind));
                if (!rows.length) return null;
                const sum = rows.reduce((s, a) => s + a.value_cents, 0);
                return [
                  <tr key={title + "h"} className="font-medium"><td>{title}</td><td></td><td className="r"><Money cents={sum} tone={false} /></td></tr>,
                  ...rows.map((a) => <tr key={a.account_id}><td className="pl-6 text-muted">{a.name}</td><td className="text-dim text-[11px]">{a.source}</td><td className="r"><Money cents={a.value_cents} tone={false} /></td></tr>),
                ];
              })}
            </tbody>
          </table>
        </Card>
        <Card title="Holdings, today">
          <table className="tbl">
            <thead><tr><th>Symbol</th><th className="r">Qty</th><th className="r">Price</th><th className="r">Value</th><th className="r">Gain</th></tr></thead>
            <tbody>
              {(n?.holdings ?? []).map((h, i) => <tr key={i}><td><b>{h.symbol}</b></td><td className="r num">{e8(h.qty_e8, 6)}</td><td className="r num">{h.price_e8 ? `$${e8(h.price_e8, 2)}` : "—"}</td><td className="r"><Money cents={h.value_cents} tone={false} /></td><td className="r"><Money cents={h.value_cents - h.cost_cents} sign /></td></tr>)}
              {n && n.holdings.length === 0 && <tr><td colSpan={5}><Empty>no holdings</Empty></td></tr>}
            </tbody>
          </table>
        </Card>
      </div>
    </Page>
  );
}
