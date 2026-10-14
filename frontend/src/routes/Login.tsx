import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import { ErrorLine, Field } from "../components/ui";

export default function Login() {
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Nothing to log in to yet: go make the first account (only works from localhost).
    get<{ allowed: boolean; users_exist: boolean }>("/api/setup")
      .then((s) => {
        if (!s.users_exist) nav("/setup", { replace: true });
      })
      .catch(() => {});
  }, [nav]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await post("/api/login", { name, password });
      // The Collectr bookmarklet lands on /collectr-drop#<json>; if that hit
      // the login wall the fragment is still in the URL bar. Carry it through.
      const h = location.hash;
      if (h.length > 2 && (h.startsWith("#%7B") || h.startsWith("#{"))) nav("/collectr-drop" + h, { replace: true });
      else nav("/", { replace: true });
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-full grid place-items-center">
      <form onSubmit={submit} className="card w-80 p-5 space-y-4">
        <div className="flex items-center gap-2">
          <span className="w-7 h-7 rounded-md bg-accent-2 text-white grid place-items-center font-bold">$</span>
          <div>
            <div className="font-semibold">Finance Desk</div>
            <div className="text-[11px] text-muted">sign in</div>
          </div>
        </div>
        <Field label="Name">
          <input className="inp" autoFocus autoComplete="username" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Password">
          <input className="inp" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorLine error={err} />
        <button className="btn primary w-full justify-center" disabled={busy || !name || !password}>
          Sign in
        </button>
      </form>
    </div>
  );
}
