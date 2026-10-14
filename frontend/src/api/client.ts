/**
 * The only fetch in the app.
 *
 * - Every non-GET carries X-Requested-With, which the server demands on
 *   every mutating /api call (the CSRF lock next to SameSite=Lax).
 * - A 401 sends the browser to /login BEFORE anything throws, so a page that
 *   polls does not spray errors after the session expires.
 * - FastAPI's 422 `detail` is a LIST, not a string; it is flattened here so
 *   a caller can always show `err.message`.
 */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailToMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((e) => {
          const loc = Array.isArray(e?.loc) ? e.loc.slice(1).join(".") : "";
          return loc ? `${loc}: ${e?.msg ?? ""}` : String(e?.msg ?? JSON.stringify(e));
        })
        .join("; ");
    }
  }
  return `HTTP ${status}`;
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal } = {},
): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  if (method !== "GET" && method !== "HEAD") headers["X-Requested-With"] = "FinanceDesk";
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, { method, headers, body, credentials: "same-origin", signal: opts.signal });
  if (res.status === 401 && !location.pathname.startsWith("/login") && !location.pathname.startsWith("/setup")) {
    location.assign("/login");
    throw new ApiError(401, "not logged in");
  }
  const text = await res.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!res.ok) throw new ApiError(res.status, detailToMessage(res.status, parsed));
  return parsed as T;
}

export const get = <T,>(path: string, signal?: AbortSignal) => api<T>(path, { signal });
export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body: body ?? {} });
export const patch = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body: body ?? {} });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}
