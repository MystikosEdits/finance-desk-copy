import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { get } from "../api/client";
import { Card, Empty, Money, Page, Stat, fmtDate, fmtWhen } from "../components/ui";
import { fmt } from "../lib/money";
import { useCreditScores } from "../components/CreditScore";

type Job = { name: string; last_finished_at: string | null; last_ok: number | null; last_error: string | null; next_due_at: string | null; running: number };
type KeyInfo = { fingerprint: string; acknowledged: boolean };
type Summary = {
  today: string; inbox: number; suggested_transfers: number;
  month: { from: string; to: string; income_cents: number; spending_cents: number; prev_income_cents: number; prev_spending_cents: number };
  net_worth: { cents: number; date: string | null; source: string; prev_cents: number | null; assets_cents: number; liabilities_cents: number };
  tax: { year: number; set_aside_cents: number | null; ytd_net_profit_cents: number | null };
  business_month: { income_cents: number; expenses_cents: number; net_cents: number } | null;
  top_spending: { name: string; cents: number; n: number }[];
  upcoming: { id: number; name: string; expected_cents: number; next_date: string; cadence: string; business: number }[];
};

export default function Dashboard() {
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => get<Job[]>("/api/system/jobs"), refetchInterval: 30_000 });
  const key = useQuery({ queryKey: ["backup-key"], queryFn: () => get<KeyInfo>("/api/system/backup-key/fingerprint") });
  const sum = useQuery({ queryKey: ["summary"], queryFn: () => get<Summary>("/api/reports/summary"), refetchInterval: 60_000 });
  const credit = useCreditScores();
  const s = sum.data;
  const failed = (jobs.data ?? []).filter((j) => j.last_ok === 0);
  const delta = s?.net_worth.prev_cents !== null && s?.net_worth.prev_cents !== undefined ? s.net_worth.cents - s.net_worth.prev_cents : null;

  return (
    <Page title="Dashboard">
      {key.data && !key.data.acknowledged && (
        <div className="card px-4 py-3 border-warn/40 flex items-center justify-between">
          <div className="text-[13px]">
            <span className="chip warn mr-2">backup key</span>
            The encrypted backups are useless without the key in <span className="num">data/backup.key</span>. Put it in your password manager, then confirm in Settings.
          </div>
          <Link to="/settings" className="btn sm">Settings</Link>
        </div>
      )}
      {failed.length > 0 && (
        <div className="card px-4 py-3 border-bad/40">
          <span className="chip bad mr-2">job failed</span>
          {failed.map((j) => <span key={j.name} className="mr-3 text-[12px]"><b>{j.name}</b>: {j.last_error}</span>)}
        </div>
      )}
      <div className="grid grid-cols-5 gap-3">
        <Stat label="Credit score" value={<Link to="/networth" className="hover:text-accent">{credit.data?.latest ? credit.data.latest.score : "—"}</Link>} sub={credit.data?.latest ? `${credit.data.latest.source}${credit.data.delta !== null ? ` · ${credit.data.delta >= 0 ? "+" : ""}${credit.data.delta}` : ""}` : "log one on the Net worth page"} tone={credit.data?.latest && credit.data.latest.score < 670 ? "warn" : undefined} />
        <Stat label="Net worth" value={<Money cents={s?.net_worth.cents} />} sub={s ? (delta !== null ? `${delta >= 0 ? "+" : ""}${fmt(delta)} vs 30 days ago` : s.net_worth.source === "accounts" ? "from account balances" : `as of ${fmtDate(s.net_worth.date)}`) : ""} />
        <Stat label="This month" value={s ? <span><Money cents={s.month.income_cents} /> <span className="text-muted text-sm">in</span> <Money cents={-s.month.spending_cents} /> <span className="text-muted text-sm">out</span></span> : "—"} sub={s ? `last month: ${fmt(s.month.prev_income_cents)} in, ${fmt(s.month.prev_spending_cents)} out` : ""} />
        <Stat label="Inbox" value={<Link to="/transactions" className="hover:text-accent">{s?.inbox ?? "—"}</Link>} sub={s && s.suggested_transfers ? `${s.suggested_transfers} possible transfers to confirm` : "transactions to review"} tone={s && s.inbox > 0 ? "warn" : undefined} />
        <Stat label={`Tax set-aside ${s?.tax.year ?? ""}`} value={<Money cents={s?.tax.set_aside_cents} tone={false} />} sub={s?.tax.ytd_net_profit_cents !== null && s?.tax.ytd_net_profit_cents !== undefined ? `on ${fmt(s.tax.ytd_net_profit_cents)} net profit YTD · estimate` : "estimate"} tone={s && (s.tax.set_aside_cents ?? 0) > 0 ? "warn" : undefined} />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <Card title="Personal spending this month">
          <table className="tbl">
            <tbody>
              {(s?.top_spending ?? []).map((r) => <tr key={r.name}><td>{r.name}</td><td className="r num text-muted">{r.n}</td><td className="r"><Money cents={-r.cents} /></td></tr>)}
            </tbody>
          </table>
          {s && s.top_spending.length === 0 && <Empty>nothing categorised yet this month</Empty>}
        </Card>
        <Card title="Business this month">
          {s?.business_month ? (
            <table className="tbl"><tbody>
              <tr><td className="text-muted">income</td><td className="r"><Money cents={s.business_month.income_cents} /></td></tr>
              <tr><td className="text-muted">expenses</td><td className="r"><Money cents={-s.business_month.expenses_cents} /></td></tr>
              <tr className="font-medium"><td>net</td><td className="r"><Money cents={s.business_month.net_cents} /></td></tr>
            </tbody></table>
          ) : <Empty>no business activity this month</Empty>}
          <div className="px-4 py-2"><Link to="/business" className="text-[12px] text-accent">Schedule C view →</Link></div>
        </Card>
        <Card title="Coming up (30 days)">
          <table className="tbl">
            <tbody>
              {(s?.upcoming ?? []).map((u) => <tr key={u.id}><td className="num text-muted">{fmtDate(u.next_date)}</td><td>{u.name}{u.business ? <span className="chip biz ml-1">biz</span> : null}</td><td className="r"><Money cents={-u.expected_cents} tone={false} /></td></tr>)}
            </tbody>
          </table>
          {s && s.upcoming.length === 0 && <Empty>no confirmed subscriptions due</Empty>}
        </Card>
      </div>
      <Card title="Server">
        <table className="tbl">
          <thead><tr><th>Job</th><th>Last run</th><th>Result</th><th>Next</th></tr></thead>
          <tbody>
            {(jobs.data ?? []).map((j) => (
              <tr key={j.name}>
                <td>{j.name}</td>
                <td className="muted">{fmtWhen(j.last_finished_at)}</td>
                <td>{j.running ? <span className="chip info">running</span> : j.last_ok === null ? <span className="chip">never</span> : j.last_ok ? <span className="chip ok">ok</span> : <span className="chip bad">failed</span>}</td>
                <td className="muted num">{j.next_due_at?.replace("T", " ") ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Page>
  );
}
