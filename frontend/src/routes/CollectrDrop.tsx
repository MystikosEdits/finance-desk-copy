import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { post } from "../api/client";
import { fmt } from "../lib/money";

/** The bookmarklet opens this page with the Collectr JSON in the URL fragment.
 *  Same origin, so the session cookie and the CSRF header work; nothing
 *  crosses from https to http as a fetch (browsers block that), only a
 *  navigation, which they allow. */
export default function CollectrDrop() {
  const [state, setState] = useState<{ status: "working" | "done" | "error"; msg: string; keys?: string[] }>({ status: "working", msg: "reading what the bookmarklet sent…" });
  useEffect(() => {
    const raw = location.hash.startsWith("#") ? decodeURIComponent(location.hash.slice(1)) : "";
    if (!raw) {
      setState({ status: "error", msg: "Nothing arrived. Click the bookmarklet while on app.getcollectr.com; it opens this page with your collection attached." });
      return;
    }
    history.replaceState(null, "", location.pathname);
    post<{ items: number; value_cents: number; username: string; product_keys: string[] }>("/api/collectr/paste", { payload: raw })
      .then((r) => setState({ status: "done", msg: `Imported ${r.items} items for @${r.username || ""} · Collectr value ${fmt(r.value_cents)}`, keys: r.product_keys }))
      .catch((e) => setState({ status: "error", msg: e instanceof Error ? e.message : String(e) }));
  }, []);
  return (
    <div className="h-full grid place-items-center">
      <div className="card w-[30rem] p-5 space-y-3">
        <div className="font-semibold">Collectr drop</div>
        <div className={`text-[13px] ${state.status === "error" ? "text-bad" : state.status === "done" ? "text-accent" : "text-muted"}`}>{state.msg}</div>
        {state.keys && state.keys.length > 0 && <div className="text-[11px] text-dim num">item fields seen: {state.keys.join(", ")}</div>}
        <Link to="/investments?tab=collectibles" className="btn primary">Open collectibles</Link>
      </div>
    </div>
  );
}
