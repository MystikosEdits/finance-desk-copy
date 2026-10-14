import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, del, get, post } from "../../api/client";
import { useAccounts } from "../../api/hooks";
import { Card, ErrorLine, Field, Money, fmtWhen } from "../../components/ui";

type Profile = { id: number; name: string; account_id: number | null; account_name: string | null; column_map: Record<string, string>; date_format: string; negate: number; skip_rows: number };
type Preview = {
  sniff: { headers: string[]; delimiter: string; sample: string[][] };
  profile: { column_map: Record<string, string>; date_format: string; negate: number; skip_rows: number };
  rows: { date: string; amount_cents: number; payee_raw: string; memo_raw: string; duplicate: boolean }[];
  total: number;
  errors: string[];
  would_insert: number;
  would_skip: number;
};
type Import = { id: number; kind: string; filename: string; account_name: string; inserted: number; skipped: number; started_at: string; error: string | null; undone_at: string | null };

const DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d/%m/%Y", "%b %d, %Y", "%m-%d-%Y", "%Y/%m/%d"];

export default function CsvImport() {
  const qc = useQueryClient();
  const accounts = useAccounts();
  const profiles = useQuery({ queryKey: ["csv-profiles"], queryFn: () => get<Profile[]>("/api/imports/csv/profiles") });
  const imports = useQuery({ queryKey: ["imports"], queryFn: () => get<Import[]>("/api/imports") });
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState("");
  const [prof, setProf] = useState<Preview["profile"] | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [saveAs, setSaveAs] = useState("");
  const [err, setErr] = useState<unknown>(null);

  const run = useMutation({
    mutationFn: async (p: Preview["profile"] | null) => {
      const form = new FormData();
      form.append("file", file!);
      form.append("account_id", accountId);
      if (p) form.append("profile", JSON.stringify(p));
      return api<Preview>("/api/imports/csv/preview", { form });
    },
    onSuccess: (pv) => { setPreview(pv); setProf(pv.profile); setErr(null); },
    onError: setErr,
  });
  const commit = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", file!);
      form.append("account_id", accountId);
      form.append("profile", JSON.stringify(prof));
      if (saveAs.trim()) form.append("save_profile_as", saveAs.trim());
      return api<{ inserted: number; skipped: number }>("/api/imports/csv", { form });
    },
    onSuccess: () => {
      setPreview(null); setFile(null); setSaveAs("");
      qc.invalidateQueries({ queryKey: ["imports"] }); qc.invalidateQueries({ queryKey: ["csv-profiles"] });
      qc.invalidateQueries({ queryKey: ["txns"] }); qc.invalidateQueries({ queryKey: ["accounts"] });
    },
    onError: setErr,
  });
  const undo = useMutation({ mutationFn: (id: number) => post(`/api/imports/${id}/undo`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["imports"] }); qc.invalidateQueries({ queryKey: ["txns"] }); }, onError: setErr });
  const delProfile = useMutation({ mutationFn: (id: number) => del(`/api/imports/csv/profiles/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["csv-profiles"] }) });

  const headers = preview?.sniff.headers ?? [];
  const setMap = (k: string, v: string) => {
    if (!prof) return;
    const cm = { ...prof.column_map };
    if (v) cm[k] = v; else delete cm[k];
    if (k === "amount" && v) { delete cm.debit; delete cm.credit; }
    if ((k === "debit" || k === "credit") && v) delete cm.amount;
    setProf({ ...prof, column_map: cm });
  };

  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <Card title="Import a bank CSV">
        <div className="p-4 space-y-3 text-[12.5px]">
          <p className="text-muted">For history older than SimpleFIN's window, or a bank it cannot reach. Rows already present (from a sync or an earlier file) are skipped; the same file twice is refused; an import can be undone while its rows are still unreviewed.</p>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Account">
              <select className="inp w-56" value={accountId} onChange={(e) => { setAccountId(e.target.value); setPreview(null); }}>
                <option value="">choose…</option>
                {(accounts.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </Field>
            <Field label="File">
              <input type="file" accept=".csv,.txt,text/csv" className="text-[12px]" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); }} />
            </Field>
            <Field label="Saved profile">
              <select className="inp w-48" onChange={(e) => { const p = profiles.data?.find((x) => String(x.id) === e.target.value); if (p) { setProf({ column_map: p.column_map, date_format: p.date_format, negate: p.negate, skip_rows: p.skip_rows }); if (file && accountId) run.mutate({ column_map: p.column_map, date_format: p.date_format, negate: p.negate, skip_rows: p.skip_rows }); } }}>
                <option value="">guess from headers</option>
                {(profiles.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </Field>
            <button className="btn primary" disabled={!file || !accountId || run.isPending} onClick={() => run.mutate(null)}>Preview</button>
          </div>

          {preview && prof && (
            <div className="space-y-3">
              <div className="grid grid-cols-6 gap-2">
                {(["date", "amount", "debit", "credit", "payee", "memo"] as const).map((k) => (
                  <Field key={k} label={k}>
                    <select className="inp" value={prof.column_map[k] ?? ""} onChange={(e) => setMap(k, e.target.value)}>
                      <option value="">—</option>
                      {headers.map((h) => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </Field>
                ))}
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <Field label="Date format">
                  <select className="inp w-40 num" value={prof.date_format} onChange={(e) => setProf({ ...prof, date_format: e.target.value })}>
                    {DATE_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                  </select>
                </Field>
                <label className="flex items-center gap-1.5 pb-2"><input type="checkbox" checked={!!prof.negate} onChange={(e) => setProf({ ...prof, negate: e.target.checked ? 1 : 0 })} /> flip sign (bank writes charges as positive)</label>
                <Field label="Skip rows"><input className="inp w-16 num" value={prof.skip_rows} onChange={(e) => setProf({ ...prof, skip_rows: parseInt(e.target.value || "0", 10) })} /></Field>
                <button className="btn" onClick={() => run.mutate(prof)} disabled={run.isPending}>Re-preview</button>
                <span className="ml-auto">
                  <b className="text-accent">{preview.would_insert}</b> new · <span className="text-muted">{preview.would_skip} already present</span> · {preview.total} rows
                </span>
              </div>
              {preview.errors.length > 0 && <div className="text-warn text-[11px]">{preview.errors.join(" · ")}</div>}
              <div className="max-h-64 overflow-auto card">
                <table className="tbl">
                  <thead><tr><th>Date</th><th>Payee</th><th>Memo</th><th className="r">Amount</th><th></th></tr></thead>
                  <tbody>
                    {preview.rows.map((r, i) => (
                      <tr key={i} className={r.duplicate ? "opacity-50" : ""}>
                        <td className="num">{r.date}</td><td>{r.payee_raw}</td><td className="text-muted">{r.memo_raw}</td>
                        <td className="r"><Money cents={r.amount_cents} /></td>
                        <td>{r.duplicate && <span className="chip">already here</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex items-end gap-3">
                <Field label="Save this mapping as" hint="so next month's file from this bank is one click">
                  <input className="inp w-56" value={saveAs} onChange={(e) => setSaveAs(e.target.value)} placeholder="Chase card" />
                </Field>
                <button className="btn primary" disabled={commit.isPending || preview.would_insert === 0} onClick={() => commit.mutate()}>
                  Import {preview.would_insert} rows
                </button>
              </div>
            </div>
          )}
        </div>
      </Card>
      <div className="grid grid-cols-2 gap-4">
        <Card title="Saved profiles">
          <table className="tbl">
            <thead><tr><th>Name</th><th>Account</th><th>Columns</th><th></th></tr></thead>
            <tbody>
              {(profiles.data ?? []).map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td><td className="text-muted">{p.account_name ?? "—"}</td>
                  <td className="text-[11px] text-muted num">{Object.entries(p.column_map).map(([k, v]) => `${k}=${v}`).join(" ")}{p.negate ? " (flipped)" : ""}</td>
                  <td className="r"><button className="btn sm ghost" onClick={() => delProfile.mutate(p.id)}>delete</button></td>
                </tr>
              ))}
              {(profiles.data ?? []).length === 0 && <tr><td colSpan={4} className="text-muted text-center py-3">none yet</td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title="Past imports">
          <table className="tbl">
            <thead><tr><th>When</th><th>File</th><th>Account</th><th className="r">In</th><th className="r">Skipped</th><th></th></tr></thead>
            <tbody>
              {(imports.data ?? []).map((i) => (
                <tr key={i.id} className={i.undone_at ? "opacity-50" : ""}>
                  <td className="text-muted">{fmtWhen(i.started_at)}</td><td className="truncate max-w-40">{i.filename}</td><td className="text-muted">{i.account_name}</td>
                  <td className="r num">{i.inserted}</td><td className="r num">{i.skipped}</td>
                  <td className="r">{i.undone_at ? <span className="chip">undone</span> : <button className="btn sm ghost" onClick={() => { if (confirm("Remove the still-unreviewed rows from this import?")) undo.mutate(i.id); }}>undo</button>}</td>
                </tr>
              ))}
              {(imports.data ?? []).length === 0 && <tr><td colSpan={6} className="text-muted text-center py-3">none yet</td></tr>}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
