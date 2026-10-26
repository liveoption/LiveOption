/**
 * Minimal `Intl`-backed formatting helpers shared across the UI.
 *
 * Kept dependency-free (no date-fns here) so they are trivially tree-shaken
 * and unit-testable under jsdom. Locale is pinned to `en-US` and time zone to
 * `America/New_York` (the trading-session reference zone) so snapshots are
 * stable across CI machines regardless of the host locale.
 */

const LOCALE = "en-US";
const TZ = "America/New_York";

const dateTimeFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TZ,
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const pctFmt = new Intl.NumberFormat(LOCALE, {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const moneyFmt = new Intl.NumberFormat(LOCALE, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Format an ISO-8601 string or `Date` for display (ET, 24h clock).
 *
 * Returns the original input string (or `""` for nullish/invalid) when the
 * value can't be parsed — callers get a predictable string, never NaN output.
 */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return typeof value === "string" ? value : "";
  }
  return dateTimeFmt.format(date);
}

/**
 * Format a ratio (e.g. `0.1234`) as `"12.34%"`. Non-finite inputs render as
 * an em-dash so tables stay aligned.
 */
export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return pctFmt.format(value);
}

/**
 * Format a USD dollar amount. Non-finite inputs render as an em-dash.
 */
export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return moneyFmt.format(value);
}
