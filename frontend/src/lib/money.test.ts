import { describe, expect, it } from "vitest";
import { fmt, parse } from "./money";

describe("money", () => {
  it("formats cents", () => {
    expect(fmt(-1230)).toBe("-$12.30");
    expect(fmt(123456)).toBe("$1,234.56");
    expect(fmt(5, { sign: true })).toBe("+$0.05");
    expect(fmt(null)).toBe("—");
  });
  it("parses text", () => {
    expect(parse("-12.30")).toBe(-1230);
    expect(parse("$1,234.5")).toBe(123450);
    expect(parse("(3)")).toBe(-300);
    expect(parse("")).toBe(0);
    expect(Number.isNaN(parse("abc"))).toBe(true);
    expect(Number.isNaN(parse("1.234"))).toBe(true);
  });
});
