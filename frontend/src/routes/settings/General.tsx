import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { get, post } from "../../api/client";
import { Card, ErrorLine, fmtWhen } from "../../components/ui";

type Job = { name: string; last_finished_at: string | null; last_ok: number | null; last_error: string | null; next_due_at: string | null; running: number; times: string[] };
type KeyInfo = { fingerprint: string; acknowledged: boolean };
type Backups = { local: { path: string; bytes: number }[]; offsite: { path: string; bytes: number }[]; offsite_dir: string; key_fingerprint: string };
type Integrity = { ok: boolean; pragma: string; splits_mismatch: unknown[]; fk_violations: number };

export default function General() {
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => get<Job[]>("/api/system/jobs"), refetchInterval: 15_000 });
  const key = useQuery({ queryKey: ["backup-key"], queryFn: () => get<KeyInfo>("/api/system/backup-key/fingerprint") });
  const backups = useQuery({ queryKey: ["backups"], queryFn: () => get<Backups>("/api/system/backups") });
  const [verify, setVerify] = useState<unknown>(null);
  const [integrity, setIntegrity] = useState<Integrity | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const runJob = useMutation({
    mutationFn: (name: string) => post(`/api/system/jobs/${name}/run`),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ["jobs"] }), 800),
    onError: setErr,
  });
  const ack = useMutation({
    mutationFn: () => post("/api/system/backup-key/acknowledge"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backup-key"] }),
  });
  const backupNow = useMutation({
    mutationFn: () => post("/api/system/backup"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backups"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: setErr,
  });
  const verifyNow = useMutation({ mutationFn: () => post("/api/system/backup/verify"), onSuccess: setVerify, onError: setErr });
  const integrityNow = useMutation({ mutationFn: () => get<Integrity>("/api/system/integrity"), onSuccess: setIntegrity, onError: setErr });

  return (
    <div className="space-y-4">
      <ErrorLine error={err} />
      <div className="grid grid-cols-2 gap-4">
        <Card title="Backups" right={<button className="btn sm" onClick={() => backupNow.mutate()} disabled={backupNow.isPending}>Back up now</button>}>
          <div className="p-4 space-y-3 text-[12px]">
            <div className="flex items-center gap-3">
              <span className="muted">key fingerprint</span>
              <span className="num">{key.data?.fingerprint ?? "…"}</span>
              {key.data?.acknowledged ? (
                <span className="chip ok">saved in password manager</span>
              ) : (
                <button className="btn sm primary" onClick={() => ack.mutate()}>
                  I have saved data/backup.key in my password manager
                </button>
              )}
            </div>
            <div>
              <span className="muted">offsite folder</span> <span className="num">{backups.data?.offsite_dir || "(not set)"}</span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="muted mb-1">local ({backups.data?.local.length ?? 0})</div>
                <ul className="num text-[11px] space-y-0.5 max-h-40 overflow-auto">
                  {(backups.data?.local ?? []).slice(-8).reverse().map((b) => (
                    <li key={b.path}>{b.path.split(/[\\/]/).pop()} · {(b.bytes / 1024).toFixed(0)} KB</li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="muted mb-1">offsite, encrypted ({backups.data?.offsite.length ?? 0})</div>
                <ul className="num text-[11px] space-y-0.5 max-h-40 overflow-auto">
                  {(backups.data?.offsite ?? []).slice(-8).reverse().map((b) => (
                    <li key={b.path}>{b.path.split(/[\\/]/).pop()} · {(b.bytes / 1024).toFixed(0)} KB</li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn sm" onClick={() => verifyNow.mutate()} disabled={verifyNow.isPending}>
                Verify newest offsite copy
              </button>
              <button className="btn sm" onClick={() => integrityNow.mutate()} disabled={integrityNow.isPending}>
                Integrity check
              </button>
            </div>
            {verify != null && <pre className="num text-[11px] bg-bg p-2 rounded border border-line-2 overflow-auto max-h-48">{JSON.stringify(verify, null, 2)}</pre>}
            {integrity && (
              <div>
                {integrity.ok ? <span className="chip ok">integrity ok</span> : <span className="chip bad">problems</span>}
                {!integrity.ok && <pre className="num text-[11px] mt-2">{JSON.stringify(integrity, null, 2)}</pre>}
              </div>
            )}
            <p className="text-dim">
              Restore: <span className="num">python backend\backup.py restore &lt;file.fernet&gt; --key &lt;key&gt; --to &lt;dir&gt;</span>
            </p>
          </div>
        </Card>
        <Card title="Jobs">
          <table className="tbl">
            <thead>
              <tr>
                <th>Job</th>
                <th>Schedule</th>
                <th>Last</th>
                <th>Result</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(jobs.data ?? []).map((j) => (
                <tr key={j.name}>
                  <td>{j.name}</td>
                  <td className="muted num">{j.times.join(" ")}</td>
                  <td className="muted">{fmtWhen(j.last_finished_at)}</td>
                  <td>
                    {j.running ? <span className="chip info">running</span> : j.last_ok === null ? <span className="chip">never</span> : j.last_ok ? <span className="chip ok">ok</span> : <span className="chip bad" title={j.last_error ?? ""}>failed</span>}
                  </td>
                  <td className="r">
                    <button className="btn sm" onClick={() => runJob.mutate(j.name)} disabled={!!j.running}>
                      run now
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
