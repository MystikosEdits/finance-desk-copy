import type { ReactNode } from "react";
import { fmt } from "../lib/money";

export function Page({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="p-5 space-y-4 max-w-[1500px]">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        <div className="flex items-center gap-2">{actions}</div>
      </div>
      {children}
    </div>
  );
}

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between px-4 py-2.5 border-b border-line">
          <h2 className="text-[12px] uppercase tracking-wider text-muted font-medium">{title}</h2>
          <div className="flex items-center gap-2">{right}</div>
        </header>
      )}
      <div>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "pos" | "neg" | "warn" | "bad" }) {
  const cls = tone === "pos" ? "text-accent" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "";
  return (
    <div className="card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`num text-xl mt-1 ${cls}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted mt-0.5">{sub}</div>}
    </div>
  );
}

export function Money({ cents, sign = false, tone = true, compact = false }: { cents: number | null | undefined; sign?: boolean; tone?: boolean; compact?: boolean }) {
  const cls = tone && cents !== null && cents !== undefined ? (cents > 0 ? "pos" : cents < 0 ? "neg" : "muted") : "";
  return <span className={`num ${cls}`}>{fmt(cents, { sign, compact })}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-10 text-center text-muted text-[13px]">{children}</div>;
}

export function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="text-bad text-[12px] px-1 py-1">{msg}</div>;
}

export function Spinner() {
  return <span className="inline-block w-3 h-3 rounded-full border-2 border-line-2 border-t-accent animate-spin" />;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1">
      <span className="text-[11px] uppercase tracking-wider text-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-dim">{hint}</span>}
    </label>
  );
}

export function Modal({ title, onClose, children, width = "max-w-xl" }: { title: ReactNode; onClose: () => void; children: ReactNode; width?: string }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center pt-20 px-4" onMouseDown={onClose}>
      <div className={`card w-full ${width} shadow-2xl`} onMouseDown={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between px-4 py-3 border-b border-line">
          <h2 className="font-medium">{title}</h2>
          <button className="btn ghost sm" onClick={onClose}>
            esc
          </button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}

export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const h = (Date.now() - d.getTime()) / 3_600_000;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m ago`;
  if (h < 48) return `${Math.round(h)}h ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
