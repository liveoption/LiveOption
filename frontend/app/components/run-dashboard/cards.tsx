/**
 * Tiny shared primitives for family dashboards (Phase D).
 *
 * Dashboard data comes from `data.family_specific` on the metrics
 * response. Every card tolerates `undefined`/`null`/`0` values via
 * `MetricValue` (an em-dash for missing numbers; true zero stays visible).
 */
import type { ReactNode } from "react";

export function MetricCard({
  label,
  children,
  testId,
}: {
  label: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex flex-col gap-1 rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <span className="text-xs uppercase tracking-wide text-neutral-500">
        {label}
      </span>
      <div className="text-sm">{children}</div>
    </div>
  );
}

export function MetricValue({
  value,
  format,
  suffix,
}: {
  value: unknown;
  format?: "money" | "percent" | "number";
  suffix?: string;
}) {
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) {
    return <span className="font-mono text-neutral-400">—</span>;
  }
  if (typeof value === "number") {
    let text: string;
    if (format === "money") {
      text = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
    } else if (format === "percent") {
      text = `${(value * 100).toFixed(2)}%`;
    } else {
      text = value.toLocaleString();
    }
    return (
      <span className="font-mono tabular-nums">
        {text}
        {suffix ?? ""}
      </span>
    );
  }
  if (typeof value === "string") return <span className="font-mono">{value}</span>;
  return null;
}

/** Pluck a deeply-nested numeric field, returning undefined if missing. */
export function pluck(obj: unknown, ...keys: string[]): unknown {
  let cur: unknown = obj;
  for (const k of keys) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}
