import { describe, expect, it } from "vitest";
import { inboxReducer, type InboxState } from "./inbox";

const s0: InboxState = { index: 0, count: 5, mode: "list" };

describe("inbox reducer", () => {
  it("moves with j/k and clamps", () => {
    let s = inboxReducer(s0, { type: "key", key: "j" });
    expect(s.index).toBe(1);
    s = inboxReducer(s, { type: "key", key: "k" });
    s = inboxReducer(s, { type: "key", key: "k" });
    expect(s.index).toBe(0);
    s = inboxReducer(s, { type: "key", key: "G" });
    expect(s.index).toBe(4);
    expect(inboxReducer(s, { type: "key", key: "j" }).index).toBe(4);
  });
  it("enters and leaves modes", () => {
    const s = inboxReducer(s0, { type: "key", key: "c" });
    expect(s.mode).toBe("categorize");
    expect(inboxReducer(s, { type: "key", key: "j" }).index).toBe(0); // keys are the modal's while open
    expect(inboxReducer(s, { type: "key", key: "Escape" }).mode).toBe("list");
  });
  it("reviewing removes the row and keeps the cursor on the next one", () => {
    let s = { ...s0, index: 2 };
    s = inboxReducer(s, { type: "reviewed" });
    expect(s).toEqual({ index: 2, count: 4, mode: "list" });
    s = { ...s0, index: 4 };
    s = inboxReducer(s, { type: "reviewed" });
    expect(s.index).toBe(3);
    s = inboxReducer({ index: 0, count: 1, mode: "list" }, { type: "reviewed" });
    expect(s).toEqual({ index: 0, count: 0, mode: "list" });
  });
  it("count updates clamp the index", () => {
    expect(inboxReducer({ ...s0, index: 4 }, { type: "count", count: 2 }).index).toBe(1);
    expect(inboxReducer(s0, { type: "count", count: 0 }).index).toBe(0);
  });
});
