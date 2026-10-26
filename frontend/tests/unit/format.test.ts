/**
 * Tests for `app/lib/format.ts`.
 *
 * Locale is pinned to `en-US` and time zone to `America/New_York` inside the
 * module, so assertions here are deterministic regardless of the host
 * machine's locale / TZ. Values are chosen to exercise edge cases rather
 * than pin exact glyphs where the ICU output differs across Node versions.
 */
import { describe, expect, it } from "vitest";
import { formatDateTime, formatMoney, formatPct } from "~/lib/format";

describe("formatDateTime", () => {
  it("formats an ISO string with year/month/day and HH:mm", () => {
    // 2026-04-19T14:30:00Z is 10:30 ET (EDT). We assert on stable tokens
    // rather than exact punctuation because ICU narrow-no-break-spaces vary
    // across Node/ICU versions.
    const out = formatDateTime("2026-04-19T14:30:00Z");
    expect(out).toMatch(/2026/);
    expect(out).toMatch(/Apr/);
    expect(out).toMatch(/19/);
    expect(out).toMatch(/10:30/);
  });

  it("accepts Date instances", () => {
    const out = formatDateTime(new Date("2026-01-01T00:00:00Z"));
    // 2025-12-31 19:00 ET at the boundary.
    expect(out).toMatch(/2025/);
  });

  it("returns '' for null/undefined/empty", () => {
    expect(formatDateTime(null)).toBe("");
    expect(formatDateTime(undefined)).toBe("");
    expect(formatDateTime("")).toBe("");
  });

  it("passes through unparseable strings unchanged", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});

describe("formatPct", () => {
  it("formats a ratio as a percent with two decimals", () => {
    expect(formatPct(0.1234)).toBe("12.34%");
    expect(formatPct(-0.05)).toBe("-5.00%");
    expect(formatPct(0)).toBe("0.00%");
  });

  it("returns em-dash for non-finite / nullish", () => {
    expect(formatPct(null)).toBe("—");
    expect(formatPct(undefined)).toBe("—");
    expect(formatPct(Number.NaN)).toBe("—");
    expect(formatPct(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("formatMoney", () => {
  it("formats USD amounts with two decimals and currency symbol", () => {
    expect(formatMoney(1234.5)).toBe("$1,234.50");
    expect(formatMoney(-42)).toBe("-$42.00");
    expect(formatMoney(0)).toBe("$0.00");
  });

  it("returns em-dash for non-finite / nullish", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
    expect(formatMoney(Number.NaN)).toBe("—");
  });
});
