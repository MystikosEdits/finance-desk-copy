import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { del, get, post } from "../../api/client";
import { Card, ErrorLine, Field, fmtWhen } from "../../components/ui";

type Status = {
  connected: boolean;
  host: string | null;
  last: { started_at: string; finished_at: string | null; ok: number | null; inserted: number; updated: number; rekeyed: number; vanished: number; error: string | null; errlist_json: string | null } | null;
  last_ok: { started_at: string } | null;
  job: { running: number; next_due_at: string | null } | null;
};
type CbStatus = { connected: boolean; last: { at: string; wallets: number; trades_inserted: number; adjustments: number; skipped_types: Record<string, number> } | null; account_id: number | null; job: { running: number; next_due_at: string | null; last_ok: number | null; last_error: string | null } | null };

function CoinbaseCard() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["cb-status"], queryFn: () => get<CbStatus>("/api/coinbase/status"), refetchInterval: 5000 });
  const [keyName, setKeyName] = useState("");
  const [pem, setPem] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [wallets, setWallets] = useState<{ currency: string; balance: string; name: string }[] | null>(null);
  const inval = () => { qc.invalidateQueries({ queryKey: ["cb-status"] }); qc.invalidateQueries({ queryKey: ["accounts"] }); qc.invalidateQueries({ queryKey: ["holdings"] }); qc.invalidateQueries({ queryKey: ["instruments"] }); };
  const connect = useMutation({ mutationFn: () => post<{ wallets: CbStatus["last"] extends null ? never : { currency: string; balance: string; name: string }[] }>("/api/coinbase/connect", { key_name: keyName, private_key: pem }), onSuccess: (r) => { setWallets(r.wallets); setKeyName(""); setPem(""); setErr(null); inval(); }, onError: setErr });
  const test = useMutation({ mutationFn: () => post<{ wallets: { currency: string; balance: string; name: string }[] }>("/api/coinbase/test"), onSuccess: (r) => { setWallets(r.wallets); setErr(null); }, onError: setErr });
  const sync = useMutation({ mutationFn: () => post("/api/coinbase/sync"), onSuccess: inval, onError: setErr });
  const disconnect = useMutation({ mutationFn: () => del("/api/coinbase"), onSuccess: inval, onError: setErr });
  const s = st.data;
  return (
    <Card title="Coinbase" right={s?.connected ? <span className="chip ok">connected</span> : <span className="chip">not connected</span>}>
      <div className="p-4 space-y-3 text-[12.5px]">
        <ErrorLine error={err} />
        {s?.connected ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <span className="muted">last sync</span> <span>{s.last ? fmtWhen(s.last.at) : "never"}</span>
              {s.last && <span className="text-muted">{s.last.wallets} wallets · {s.last.trades_inserted} new trades{s.last.adjustments ? ` · ${s.last.adjustments} balance adjustments` : ""}</span>}
              <span className="muted">next</span> <span className="num">{s.job?.next_due_at?.replace("T", " ") ?? "—"}</span>
              <button className="btn sm primary" onClick={() => sync.mutate()} disabled={!!s.job?.running}>{s.job?.running ? "syncing…" : "Sync now"}</button>
              <button className="btn sm" onClick={() => test.mutate()} disabled={test.isPending}>Test</button>
              <button className="btn sm danger ml-auto" onClick={() => { if (confirm("Disconnect Coinbase? The trades already imported stay.")) disconnect.mutate(); }}>disconnect</button>
            </div>
            {s.job?.last_ok === 0 && <div className="text-bad">last sync failed: {s.job.last_error}</div>}
            {s.last && Object.keys(s.last.skipped_types).length > 0 && <div className="text-muted">transaction types with no position effect, skipped: {Object.entries(s.last.skipped_types).map(([k, v]) => `${k} ×${v}`).join(", ")}</div>}
            <p className="text-muted">Wallets become holdings in one "Coinbase" crypto account. Buys, sells, sends, receives and rewards become trades; if a wallet's balance still disagrees with the trades (history before the key existed), a dated balance adjustment is written so net worth is right even where cost basis is unknown.</p>
          </>
        ) : (
          <>
            <p>In Coinbase: <b>Developer Platform → API keys → Create</b>. Choose <b>ECDSA</b> as the algorithm (not Ed25519) and only the read permissions for accounts and transactions. Paste the key name and the private key here. They are stored with Windows DPAPI on the server, never in the database.</p>
            <div className="grid grid-cols-2 gap-3">
              <Field label="API key name"><input className="inp num" value={keyName} onChange={(e) => setKeyName(e.target.value)} placeholder="organizations/…/apiKeys/…" /></Field>
              <Field label="EC private key (PEM)"><textarea className="inp num h-24" value={pem} onChange={(e) => setPem(e.target.value)} placeholder={"-----BEGIN EC PRIVATE KEY-----\n…"} /></Field>
            </div>
            <button className="btn primary" onClick={() => connect.mutate()} disabled={!keyName.trim() || !pem.trim() || connect.isPending}>{connect.isPending ? "checking…" : "Connect + first sync"}</button>
          </>
        )}
        {wallets && (
          <div className="text-[12px]"><span className="muted">wallets seen:</span> {wallets.filter((w) => parseFloat(w.balance || "0") > 0).map((w) => `${w.currency} ${w.balance}`).join(" · ") || "none with a balance"}</div>
        )}
      </div>
    </Card>
  );
}

type LogRow = { id: number; started_at: string; finished_at: string | null; ok: number | null; accounts: number; inserted: number; updated: number; rekeyed: number; vanished: number; error: string | null; errlist_json: string | null };

export default function Connections() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["sf-status"], queryFn: () => get<Status>("/api/simplefin/status"), refetchInterval: 5000 });
  const log = useQuery({ queryKey: ["sf-log"], queryFn: () => get<LogRow[]>("/api/simplefin/log"), refetchInterval: 10000 });
  const [token, setToken] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const inval = () => { qc.invalidateQueries({ queryKey: ["sf-status"] }); qc.invalidateQueries({ queryKey: ["sf-log"] }); qc.invalidateQueries({ queryKey: ["accounts"] }); qc.invalidateQueries({ queryKey: ["txns"] }); };
  const claim = useMutation({ mutationFn: () => post("/api/simplefin/claim", { setup_token: token.trim() }), onSuccess: () => { setToken(""); inval(); }, onError: setErr });
  const sync = useMutation({ mutationFn: () => post("/api/simplefin/sync"), onSuccess: inval, onError: setErr });
  const disconnect = useMutation({ mutationFn: () => del("/api/simplefin"), onSuccess: inval, onError: setErr });
  const s = status.data;
  const errlist = (r: { errlist_json: string | null }) => { try { return r.errlist_json ? (JSON.parse(r.errlist_json) as string[]) : []; } catch { return []; } };

  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <Card title="SimpleFIN Bridge" right={s?.connected ? <span className="chip ok">connected</span> : <span className="chip warn">not connected</span>}>
        <div className="p-4 space-y-3 text-[12.5px]">
          {s?.connected ? (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <span className="muted">bridge</span> <span className="num">{s.host}</span>
                <span className="muted">last good sync</span> <span>{fmtWhen(s.last_ok?.started_at ?? null)}</span>
                <span className="muted">next</span> <span className="num">{s.job?.next_due_at?.replace("T", " ") ?? "—"}</span>
                <button className="btn sm primary" onClick={() => sync.mutate()} disabled={!!s.job?.running || sync.isPending}>
                  {s.job?.running ? "syncing…" : "Sync now"}
                </button>
                <button className="btn sm danger ml-auto" onClick={() => { if (confirm("Disconnect SimpleFIN? Existing transactions stay; new ones stop until you paste a new token.")) disconnect.mutate(); }}>disconnect</button>
              </div>
              {s.last && s.last.ok === 0 && <div className="text-bad">last sync failed: {s.last.error}</div>}
              {s.last && errlist(s.last).length > 0 && (
                <div className="text-warn">bank-side notices: {errlist(s.last).join(" · ")}</div>
              )}
              <p className="text-muted">
                Runs three times a day. The Bridge itself refreshes from the banks about once a day, so more often buys nothing. If a bank drops off, fix it at{" "}
                <span className="num">bridge.simplefin.org</span>; the app just reads what the Bridge has.
              </p>
            </>
          ) : (
            <>
              <p>
                Sign up at <span className="num">bridge.simplefin.org</span> (about $1.50/mo), connect your banks there, then <b>New Setup Token</b> and paste it here. A token works exactly once.
              </p>
              <Field label="Setup token">
                <textarea className="inp num h-20" value={token} onChange={(e) => setToken(e.target.value)} placeholder="aHR0cHM6Ly9icmlkZ2Uuc2ltcGxlZmluLm9yZy9zaW1wbGVmaW4vY2xhaW0v…" />
              </Field>
              <button className="btn primary" onClick={() => claim.mutate()} disabled={!token.trim() || claim.isPending}>
                {claim.isPending ? "claiming…" : "Connect + first sync"}
              </button>
              <p className="text-muted">The first sync pulls up to a year of history. Accounts appear under Accounts with their default set to "ask" - set each one to personal or business.</p>
            </>
          )}
        </div>
      </Card>
      <CoinbaseCard />
      <Card title="Sync log">
        <table className="tbl">
          <thead>
            <tr>
              <th>Started</th>
              <th>Result</th>
              <th className="r">Accounts</th>
              <th className="r">Inserted</th>
              <th className="r">Updated</th>
              <th className="r">Rekeyed</th>
              <th className="r">Vanished</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {(log.data ?? []).map((r) => (
              <tr key={r.id}>
                <td className="num text-muted">{r.started_at.replace("T", " ")}</td>
                <td>{r.ok === null ? <span className="chip info">running</span> : r.ok ? <span className="chip ok">ok</span> : <span className="chip bad">failed</span>}</td>
                <td className="r num">{r.accounts ?? "—"}</td>
                <td className="r num">{r.inserted ?? "—"}</td>
                <td className="r num">{r.updated ?? "—"}</td>
                <td className="r num">{r.rekeyed ?? "—"}</td>
                <td className="r num">{r.vanished ?? "—"}</td>
                <td className="text-[11px] text-muted max-w-md truncate">{r.error ?? errlist(r).join(" · ")}</td>
              </tr>
            ))}
            {(log.data ?? []).length === 0 && <tr><td colSpan={8} className="text-muted text-center py-4">no syncs yet</td></tr>}
          </tbody>
        </table>
        <div className="px-4 py-2 text-[11px] text-dim">"Rekeyed" counts pending charges the bank re-sent under a new id once posted. After two weeks this column tells you whether your bank does that at all.</div>
      </Card>
    </div>
  );
}
