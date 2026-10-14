import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { get, post } from "../api/client";

type Health = {
  ok: boolean;
  stale: boolean;
  setup_mode: boolean;
  last_sync: string | null;
  last_backup: string | null;
};

const NAV: { to: string; label: string; key: string }[] = [
  { to: "/", label: "Dashboard", key: "1" },
  { to: "/transactions", label: "Transactions", key: "2" },
  { to: "/accounts", label: "Accounts", key: "3" },
  { to: "/cashflow", label: "Cash flow", key: "4" },
  { to: "/subscriptions", label: "Subscriptions", key: "5" },
  { to: "/business", label: "Business", key: "6" },
  { to: "/taxes", label: "Taxes", key: "7" },
  { to: "/investments", label: "Investments", key: "8" },
  { to: "/networth", label: "Net worth", key: "9" },
  { to: "/reports", label: "Reports", key: "0" },
  { to: "/settings", label: "Settings", key: "," },
];

function ageHours(iso: string | null): number | null {
  if (!iso) return null;
  return (Date.now() - new Date(iso).getTime()) / 3_600_000;
}

export default function Layout() {
  const nav = useNavigate();
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<{ name: string }>("/api/me") });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => get<Health>("/health"),
    refetchInterval: 60_000,
  });
  const h = health.data;
  const syncAge = ageHours(h?.last_sync ?? null);
  const backupAge = ageHours(h?.last_backup ?? null);

  async function logout() {
    await post("/api/logout");
    nav("/login");
  }

  return (
    <div className="flex h-full">
      <aside className="w-52 shrink-0 border-r border-line bg-panel flex flex-col">
        <div className="px-4 py-3 border-b border-line flex items-center gap-2">
          <span className="w-6 h-6 rounded-md bg-accent-2 text-white grid place-items-center font-bold text-xs">$</span>
          <span className="font-semibold tracking-tight">Finance Desk</span>
        </div>
        <nav className="flex-1 py-2">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `flex items-center justify-between px-4 py-1.5 text-[13px] ${
                  isActive ? "text-fg bg-panel-2 border-r-2 border-accent" : "text-muted hover:text-fg"
                }`
              }
            >
              <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-line text-[11px] space-y-1">
          {h?.stale && <div className="chip warn">STALE SERVER — restart the task</div>}
          {syncAge !== null && syncAge > 36 && <div className="chip bad">sync {Math.round(syncAge)}h old</div>}
          {backupAge !== null && backupAge > 36 && <div className="chip bad">backup {Math.round(backupAge)}h old</div>}
          {h && !h.last_backup && <div className="chip warn">no backup yet</div>}
          <div className="flex items-center justify-between pt-1">
            <span className="muted">{me.data?.name ?? ""}</span>
            <button className="btn ghost sm" onClick={logout}>
              log out
            </button>
          </div>
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
