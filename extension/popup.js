const DEFAULTS = { desk: "http://localhost:8020", handle: "@yourhandle", everyHours: 24 };

function show(last) {
  const el = document.getElementById("last");
  if (!last) { el.textContent = "never run yet"; return; }
  el.innerHTML = `<span class="${last.ok ? "ok" : "bad"}">${last.ok ? "ok" : "failed"}</span> · ${new Date(last.at).toLocaleString()} · ${last.trigger}\n${last.note || ""}`;
}

(async () => {
  const s = { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  document.getElementById("desk").value = s.desk;
  document.getElementById("handle").value = s.handle;
  document.getElementById("hours").value = s.everyHours;
  show((await chrome.storage.local.get("last")).last);
})();

document.getElementById("save").onclick = async () => {
  const desk = document.getElementById("desk").value.trim().replace(/\/$/, "");
  const handle = document.getElementById("handle").value.trim();
  const everyHours = Math.max(1, parseInt(document.getElementById("hours").value || "24", 10));
  await chrome.storage.sync.set({ desk, handle: handle.startsWith("@") ? handle : "@" + handle, everyHours });
  chrome.alarms.create("collectr-sync", { periodInMinutes: everyHours * 60, delayInMinutes: 1 });
  document.getElementById("save").textContent = "saved";
  setTimeout(() => (document.getElementById("save").textContent = "Save settings"), 1500);
};

document.getElementById("sync").onclick = () => {
  document.getElementById("sync").textContent = "syncing…";
  chrome.runtime.sendMessage({ type: "sync-now" }, (res) => {
    show(res);
    document.getElementById("sync").textContent = "Sync now";
  });
};
