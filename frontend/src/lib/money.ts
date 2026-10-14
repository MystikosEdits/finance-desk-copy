/** Cents in, strings out. Nothing in the frontend does arithmetic on money
 * except display formatting and the split editor's "remaining" line. */

export function fmt(cents: number | null | undefined, opts: { sign?: boolean; compact?: boolean } = {}): string {
  if (cents === null || cents === undefined || Number.isNaN(cents)) return "—";
  const neg = cents < 0;
  const abs = Math.abs(cents);
  if (opts.compact && abs >= 100000 * 100) {
    const k = abs / 100;
    const s = k >= 1_000_000 ? `${(k / 1_000_000).toFixed(2)}M` : `${(k / 1000).toFixed(1)}k`;
    return `${neg ? "-" : opts.sign ? "+" : ""}$${s}`;
  }
  const dollars = Math.floor(abs / 100);
  const c = abs % 100;
  const body = `$${dollars.toLocaleString("en-US")}.${c.toString().padStart(2, "0")}`;
  if (neg) return `-${body}`;
  return opts.sign ? `+${body}` : body;
}

/** "12.30" | "-1,234.5" | "$5" -> cents; NaN when unparsable. */
export function parse(text: string): number {
  const s = text.trim().replace(/[$,\s]/g, "");
  if (!s) return 0;
  const neg = s.startsWith("(") && s.endsWith(")");
  const core = neg ? s.slice(1, -1) : s;
  if (!/^[-+]?\d*(\.\d{0,2})?$/.test(core) || core === "-" || core === "+" || core === ".") return NaN;
  const [whole, frac = ""] = core.replace("+", "").split(".");
  const sign = whole.startsWith("-") || neg ? -1 : 1;
  const w = Math.abs(parseInt(whole || "0", 10)) || 0;
  const f = parseInt((frac + "00").slice(0, 2), 10);
  return sign * (w * 100 + f);
}

export function e8(v: number | null | undefined, places = 8): string {
  if (v === null || v === undefined) return "—";
  const s = (v / 1e8).toFixed(places);
  return s.replace(/\.?0+$/, "");
}

export function pct(n: number | null | undefined, d = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(d)}%`;
}
