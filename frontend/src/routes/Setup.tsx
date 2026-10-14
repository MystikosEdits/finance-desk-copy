import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import { ErrorLine, Field } from "../components/ui";

export default function Setup() {
  const nav = useNavigate();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    get<{ allowed: boolean; users_exist: boolean }>("/api/setup")
      .then((s) => {
        if (s.users_exist) nav("/login", { replace: true });
        else setAllowed(s.allowed);
      })
      .catch(() => setAllowed(false));
  }, [nav]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    if (password !== again) {
      setErr(new Error("passwords do not match"));
      return;
    }
    try {
      const r = await post<{ note: string }>("/api/setup", { name, password });
      setDone(r.note);
    } catch (e2) {
      setErr(e2);
    }
  }

  return (
    <div className="h-full grid place-items-center">
      <div className="card w-[26rem] p-5 space-y-4">
        <div>
          <div className="font-semibold">First account</div>
          <div className="text-[12px] text-muted">This page works once, and only from the machine the server runs on.</div>
        </div>
        {allowed === false && (
          <div className="text-[12px] text-warn space-y-2">
            <p>Setup is only reachable from localhost. From the laptop, tunnel in and reload:</p>
            <pre className="num text-[11px] bg-bg p-2 rounded border border-line-2 overflow-auto">ssh -L 8020:127.0.0.1:8020 you@your-server{"\n"}open http://localhost:8020/setup</pre>
          </div>
        )}
        {done ? (
          <div className="space-y-3">
            <div className="chip ok">account created</div>
            <p className="text-[12px]">{done}</p>
            <pre className="num text-[11px] bg-bg p-2 rounded border border-line-2">.\restart.ps1</pre>
            <button className="btn primary" onClick={() => nav("/", { replace: true })}>
              Open the desk
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <Field label="Name" hint="letters, numbers, - and _">
              <input className="inp" autoFocus value={name} onChange={(e) => setName(e.target.value)} disabled={allowed !== true} />
            </Field>
            <Field label="Password" hint="at least 8 characters">
              <input className="inp" type="password" value={password} onChange={(e) => setPassword(e.target.value)} disabled={allowed !== true} />
            </Field>
            <Field label="Again">
              <input className="inp" type="password" value={again} onChange={(e) => setAgain(e.target.value)} disabled={allowed !== true} />
            </Field>
            <ErrorLine error={err} />
            <button className="btn primary w-full justify-center" disabled={allowed !== true || !name || password.length < 8}>
              Create owner account
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
