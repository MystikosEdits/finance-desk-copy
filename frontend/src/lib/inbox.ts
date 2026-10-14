/**
 * The inbox keyboard model, as a pure reducer so it can be tested without a DOM.
 *
 *   j / ↓   next row          k / ↑   previous row
 *   c       categorise        b       toggle business
 *   s       split             r       make a rule
 *   a       attach receipt    n       note
 *   Enter   mark reviewed, advance
 *   Esc     back to the list
 */

export type Mode = "list" | "categorize" | "split" | "rule" | "attach" | "note";

export type InboxState = { index: number; count: number; mode: Mode };

export type InboxEvent =
  | { type: "key"; key: string }
  | { type: "count"; count: number }
  | { type: "select"; index: number }
  | { type: "close" }
  | { type: "reviewed" };

export const KEY_MODES: Record<string, Mode> = { c: "categorize", s: "split", r: "rule", a: "attach", n: "note" };

export function inboxReducer(s: InboxState, e: InboxEvent): InboxState {
  switch (e.type) {
    case "count": {
      const count = e.count;
      const index = count === 0 ? 0 : Math.min(s.index, count - 1);
      return { ...s, count, index };
    }
    case "select":
      return { ...s, index: Math.max(0, Math.min(e.index, Math.max(0, s.count - 1))), mode: "list" };
    case "close":
      return { ...s, mode: "list" };
    case "reviewed":
      // the row leaves the list; the same index now points at the next row
      return { ...s, mode: "list", count: Math.max(0, s.count - 1), index: Math.min(s.index, Math.max(0, s.count - 2)) };
    case "key": {
      if (s.mode !== "list") {
        return e.key === "Escape" ? { ...s, mode: "list" } : s;
      }
      if (s.count === 0) return s;
      if (e.key === "j" || e.key === "ArrowDown") return { ...s, index: Math.min(s.index + 1, s.count - 1) };
      if (e.key === "k" || e.key === "ArrowUp") return { ...s, index: Math.max(s.index - 1, 0) };
      if (e.key === "g") return { ...s, index: 0 };
      if (e.key === "G") return { ...s, index: s.count - 1 };
      const m = KEY_MODES[e.key];
      if (m) return { ...s, mode: m };
      return s;
    }
  }
}
