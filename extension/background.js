// Finance Desk: Collectr sync.
//
// Collectr's API sits behind a bot challenge that only a real browser passes,
// so the read has to happen in YOUR browser, signed in. This does the same
// thing the "Grab Collectr" bookmark does, on a schedule:
//   1. open app.getcollectr.com in a background tab
//   2. run the reader in that tab (your session, your token; the token never
//      leaves the tab)
//   3. send that tab to Finance Desk's /collectr-drop page, which imports it
//   4. close the tab
// Runs once a day while Chrome is open, and whenever you click the icon.

const DEFAULTS = { desk: "http://localhost:8020", handle: "@yourhandle", everyHours: 24 };

async function settings() {
  const s = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...s };
}

chrome.runtime.onInstalled.addListener(async () => {
  const s = await settings();
  chrome.alarms.create("collectr-sync", { periodInMinutes: s.everyHours * 60, delayInMinutes: 1 });
});
chrome.runtime.onStartup.addListener(async () => {
  const s = await settings();
  chrome.alarms.create("collectr-sync", { periodInMinutes: s.everyHours * 60, delayInMinutes: 2 });
});
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "collectr-sync") sync("alarm"); });
chrome.runtime.onMessage.addListener((m, _s, reply) => {
  if (m && m.type === "sync-now") { sync("click").then(reply); return true; }
});

// Runs INSIDE the Collectr tab. Returns the payload as a string, or {error}.
function readPortfolio(handle) {
  return (async () => {
    const A = "https://api-v2.getcollectr.com";
    const ck = Object.fromEntries(document.cookie.split(";").map((c) => { const i = c.indexOf("="); return [c.slice(0, i).trim(), decodeURIComponent(c.slice(i + 1))]; }));
    let tok = null; try { tok = ck.collectrToken ? JSON.parse(ck.collectrToken) : null; } catch (e) {}
    if (!(tok && tok.token && tok.username)) return { error: "not signed in to app.getcollectr.com" };
    const H = { Authorization: tok.token, Accept: "application/json" };
    const u = tok.username;
    const g = async (p) => { const r = await fetch(A + p, { headers: H, credentials: "include" }); if (r.status === 401) throw new Error("Collectr session expired - sign in again"); if (!r.ok) throw new Error("Collectr said " + r.status); return r.json(); };
    try {
      const cols = ((await g("/accounts/" + u + "/collections?details=true")).data) || [];
      const sum = (await g("/accounts/" + u + "/collections/summary?allCollections=true")).data || {};
      let hist = []; try { hist = ((await g("/collections/" + u + "/overview?allCollections=true&days=all")).data || {}).historical_values || []; } catch (e) {}
      let off = 0, value = null, all = [];
      for (let i = 0; i < 400; i++) {
        const j = await g("/collections/" + u + "/products?offset=" + off + "&limit=30&allCollections=true&unstackedView=true&currency=USD");
        if (value === null) value = j.value;
        const p = j.data || []; all = all.concat(p); if (p.length < 30) break; off += p.length;
      }
      return { payload: JSON.stringify({ source: "private", handle, value, summary: sum, collections: cols, history: hist, products: all }), items: all.length };
    } catch (e) { return { error: String(e.message || e) }; }
  })();
}

function waitLoaded(tabId) {
  return new Promise((resolve) => {
    const done = (id, info) => { if (id === tabId && info.status === "complete") { chrome.tabs.onUpdated.removeListener(done); resolve(); } };
    chrome.tabs.onUpdated.addListener(done);
    setTimeout(() => { chrome.tabs.onUpdated.removeListener(done); resolve(); }, 20000);
  });
}

async function sync(trigger) {
  const s = await settings();
  const started = new Date().toISOString();
  let tab = null;
  try {
    tab = await chrome.tabs.create({ url: "https://app.getcollectr.com/portfolio", active: false });
    await waitLoaded(tab.id);
    await new Promise((r) => setTimeout(r, 2500));   // let the app set its cookies
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readPortfolio, args: [s.handle] });
    const out = res && res.result;
    if (!out || out.error) throw new Error(out ? out.error : "no result from the Collectr tab");
    const url = s.desk.replace(/\/$/, "") + "/collectr-drop#" + encodeURIComponent(out.payload);
    if (url.length > 1900000) throw new Error("collection too large for a URL (" + out.items + " items)");
    await chrome.tabs.update(tab.id, { url });
    await waitLoaded(tab.id);
    await new Promise((r) => setTimeout(r, 6000));  // the drop page posts to the API on load
    const [check] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => document.body ? document.body.innerText.slice(0, 400) : "" });
    const text = (check && check.result) || "";
    const ok = /Imported \d+ items/.test(text);
    const result = { at: started, trigger, ok, items: out.items, note: ok ? (text.match(/Imported [^\n]*/) || [""])[0] : (text.split("\n").find((l) => l.trim()) || "no confirmation from Finance Desk (signed in there?)") };
    await chrome.storage.local.set({ last: result });
    chrome.action.setBadgeText({ text: ok ? "ok" : "!" });
    chrome.action.setBadgeBackgroundColor({ color: ok ? "#059669" : "#dc2626" });
    if (ok) setTimeout(() => chrome.tabs.remove(tab.id).catch(() => {}), 1500);
    return result;
  } catch (e) {
    const result = { at: started, trigger, ok: false, note: String(e.message || e) };
    await chrome.storage.local.set({ last: result });
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#dc2626" });
    if (tab) chrome.tabs.remove(tab.id).catch(() => {});
    return result;
  }
}
