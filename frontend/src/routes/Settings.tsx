import { useSearchParams } from "react-router-dom";
import { Page } from "../components/ui";
import Categories from "./settings/Categories";
import Connections from "./settings/Connections";
import CsvImport from "./settings/CsvImport";
import General from "./settings/General";
import Payees from "./settings/Payees";
import Rules from "./settings/Rules";

const TABS: { key: string; label: string }[] = [
  { key: "general", label: "Server & backups" },
  { key: "connections", label: "Bank sync" },
  { key: "import", label: "CSV import" },
  { key: "rules", label: "Rules" },
  { key: "categories", label: "Categories" },
  { key: "payees", label: "Payees" },
];

export default function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "general";
  return (
    <Page
      title="Settings"
      actions={
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button key={t.key} className={`btn sm ${tab === t.key ? "primary" : "ghost"}`} onClick={() => setParams({ tab: t.key }, { replace: true })}>
              {t.label}
            </button>
          ))}
        </div>
      }
    >
      {tab === "general" && <General />}
      {tab === "connections" && <Connections />}
      {tab === "import" && <CsvImport />}
      {tab === "rules" && <Rules />}
      {tab === "categories" && <Categories />}
      {tab === "payees" && <Payees />}
    </Page>
  );
}
