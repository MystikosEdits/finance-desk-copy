import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { del, get, post } from "../api/client";
import { Card, Empty, ErrorLine, Field, fmtDate } from "./ui";

type Score = { id: number; date: string; score: number; source: string; model: string; notes: string };
type Scores = { rows: Score[]; latest: Score | null; previous_same_source: Score | null; delta: number | null };

const SOURCES = ["Credit Karma (TransUnion)", "Credit Karma (Equifax)", "Experian", "Chase Credit Journey", "Capital One CreditWise", "Discover Scorecard", "Other"];

export function useCreditScores() {
  return useQuery({ queryKey: ["credit"], queryFn: () => get<Scores>("/api/credit") });
}

export default function CreditScoreCard({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient();
  const q = useCreditScores();
  const [d, setD] = useState({ score: "", date: new Date().toISOString().slice(0, 10), source: SOURCES[0], model: "VantageScore 3.0", notes: "" });
  const [err, setErr] = useState<unknown>(null);
  const add = useMutation({ mutationFn: () => post("/api/credit", { ...d, score: parseInt(d.score, 10) }), onSuccess: () => { setD({ ...d, score: "", notes: "" }); qc.invalidateQueries({ queryKey: ["credit"] }); }, onError: setErr });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/credit/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["credit"] }) });
  const s = q.data;
  const data = [...(s?.rows ?? [])].reverse().map((r) => ({ date: r.date.slice(5), score: r.score, source: r.source }));
  const latest = s?.latest;
  const tone = latest ? (latest.score >= 740 ? "text-accent" : latest.score >= 670 ? "text-fg" : latest.score >= 580 ? "text-warn" : "text-bad") : "";
  const band = latest ? (latest.score >= 800 ? "exceptional" : latest.score >= 740 ? "very good" : latest.score >= 670 ? "good" : latest.score >= 580 ? "fair" : "poor") : "";

  return (
    <Card title="Credit score" right={latest ? <span className="text-[11px] text-muted">{latest.source} · {fmtDate(latest.date)}</span> : null}>
      <div className={compact ? "p-4" : "p-4 grid grid-cols-3 gap-4"}>
        <div>
          <div className="flex items-baseline gap-3">
            <span className={`num text-3xl ${tone}`}>{latest ? latest.score : "—"}</span>
            {latest && <span className="text-[12px] text-muted">{band}</span>}
            {s?.delta !== null && s?.delta !== undefined && <span className={`num text-[12px] ${s.delta >= 0 ? "text-accent" : "text-bad"}`}>{s.delta >= 0 ? "+" : ""}{s.delta} since last {latest?.source}</span>}
          </div>
          <div className="h-32 mt-2">
            {data.length > 1 ? (
              <ResponsiveContainer>
                <LineChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="#1f2a3d" vertical={false} />
                  <XAxis dataKey="date" stroke="#5b667a" fontSize={10} minTickGap={20} />
                  <YAxis stroke="#5b667a" fontSize={10} domain={["dataMin - 20", "dataMax + 20"]} width={32} />
                  <Tooltip contentStyle={{ background: "#151d2c", border: "1px solid #2a3850", fontSize: 12 }} />
                  <Line type="monotone" dataKey="score" stroke="#60a5fa" strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : <Empty>Log a score whenever you check it; the trend appears after two.</Empty>}
          </div>
        </div>
        {!compact && (
          <>
            <div className="space-y-2">
              <div className="text-[11px] uppercase tracking-wider text-muted">Log a reading</div>
              <div className="grid grid-cols-2 gap-2">
                <Field label="Score"><input className="inp num" value={d.score} onChange={(e) => setD({ ...d, score: e.target.value.replace(/\D/g, "").slice(0, 3) })} placeholder="742" /></Field>
                <Field label="Date"><input className="inp num" type="date" value={d.date} onChange={(e) => setD({ ...d, date: e.target.value })} /></Field>
                <Field label="Source">
                  <select className="inp" value={d.source} onChange={(e) => setD({ ...d, source: e.target.value })}>{SOURCES.map((x) => <option key={x}>{x}</option>)}</select>
                </Field>
                <Field label="Model">
                  <select className="inp" value={d.model} onChange={(e) => setD({ ...d, model: e.target.value })}>{["VantageScore 3.0", "VantageScore 4.0", "FICO 8", "FICO 9", "other"].map((x) => <option key={x}>{x}</option>)}</select>
                </Field>
              </div>
              <input className="inp" placeholder="note (new card, paid off X…)" value={d.notes} onChange={(e) => setD({ ...d, notes: e.target.value })} />
              <ErrorLine error={err} />
              <button className="btn primary" disabled={!d.score || add.isPending} onClick={() => add.mutate()}>Add</button>
              <div className="text-[11px] text-dim">No bureau offers a personal API, so this is typed in. Compare like with like: a VantageScore from Credit Karma and a FICO from your card app can differ by 30 points on the same day.</div>
            </div>
            <div className="max-h-56 overflow-auto">
              <table className="tbl">
                <thead><tr><th>Date</th><th className="r">Score</th><th>Source</th><th>Note</th><th></th></tr></thead>
                <tbody>
                  {(s?.rows ?? []).map((r) => (
                    <tr key={r.id}><td className="num text-muted">{fmtDate(r.date)}</td><td className="r num">{r.score}</td><td className="text-muted text-[11px]">{r.source}{r.model ? ` · ${r.model}` : ""}</td><td className="text-muted text-[11px]">{r.notes}</td>
                      <td className="r"><button className="btn sm ghost" onClick={() => remove.mutate(r.id)}>×</button></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
