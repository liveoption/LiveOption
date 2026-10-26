/**
 * `<RunErrorBanner>` — persistent run-level error summary.
 *
 * Aggregates `worker.error` events received via the run's event stream
 * (REST history + live SSE) by `payload.category`, summing `payload.count`
 * per category. Renders one row per category, color-coded by max severity.
 *
 * Mounting: rendered at the top of `/runs/:runId` above the 4-panel layout.
 * The component returns `null` when there are no errors/warnings, so it is
 * safe to mount unconditionally.
 *
 * Style: matches the `ProblemAlert` palette but uses `border-l-4` for more
 * weight (errors are loud on purpose).
 *
 * A11y: `role="alert"` on the container; the spec calls this out as
 * non-dismissible because a dismissible variant led to the original
 * "completed exit=0, user saw nothing" failure mode.
 */
import { useMemo, useState } from "react";

import type { EventHistoryItem } from "../lib/contracts";

export type WorkerErrorSeverity = "error" | "warning" | "info";

export interface WorkerErrorPayload {
  severity?: WorkerErrorSeverity;
  category?: string;
  message?: string;
  count?: number;
  first_ts?: string;
  last_ts?: string;
  context?: Record<string, unknown> | null;
}

export interface RunErrorBannerProps {
  runId: string;
  events: EventHistoryItem[];
  /** Optional class names for layout tweaks. */
  className?: string;
}

interface CategoryAgg {
  category: string;
  totalCount: number;
  maxSeverity: WorkerErrorSeverity;
  lastMessage: string;
  firstTs: string | null;
  lastTs: string | null;
  lastContext: Record<string, unknown> | null;
}

const SEVERITY_RANK: Record<WorkerErrorSeverity, number> = {
  info: 0,
  warning: 1,
  error: 2,
};

function severityMax(
  a: WorkerErrorSeverity,
  b: WorkerErrorSeverity,
): WorkerErrorSeverity {
  return SEVERITY_RANK[a] >= SEVERITY_RANK[b] ? a : b;
}

function readPayload(event: EventHistoryItem): WorkerErrorPayload | null {
  if (event.kind !== "worker.error") return null;
  const p = event.payload as Record<string, unknown> | undefined;
  if (!p) return null;
  return p as WorkerErrorPayload;
}

function formatTs(iso: string | null): string {
  if (!iso) return "—";
  // RFC3339 → HH:MM:SS local; fall back to raw on parse failure.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(11, 19);
}

/**
 * Group worker.error events by category, summing payload.count per category.
 *
 * The worker emits its current running count on each flush (1, 10, 100…),
 * so for a category we want the **maximum** count seen, not the sum across
 * flushes. Across distinct categories we keep separate rows.
 */
function aggregate(events: EventHistoryItem[]): CategoryAgg[] {
  const buckets = new Map<string, CategoryAgg>();
  for (const ev of events) {
    const payload = readPayload(ev);
    if (!payload) continue;
    const category = payload.category ?? "internal";
    const severity = (payload.severity ?? "error") as WorkerErrorSeverity;
    const count =
      typeof payload.count === "number" && Number.isFinite(payload.count)
        ? payload.count
        : 1;
    const message = payload.message ?? "";
    const firstTs = payload.first_ts ?? null;
    const lastTs = payload.last_ts ?? ev.occurred_at ?? null;
    const context =
      payload.context && typeof payload.context === "object"
        ? (payload.context as Record<string, unknown>)
        : null;

    const existing = buckets.get(category);
    if (!existing) {
      buckets.set(category, {
        category,
        totalCount: count,
        maxSeverity: severity,
        lastMessage: message,
        firstTs,
        lastTs,
        lastContext: context,
      });
      continue;
    }
    // Worker emits running count per flush — take max, not sum.
    existing.totalCount = Math.max(existing.totalCount, count);
    existing.maxSeverity = severityMax(existing.maxSeverity, severity);
    // Track earliest first_ts, latest last_ts.
    if (firstTs && (!existing.firstTs || firstTs < existing.firstTs)) {
      existing.firstTs = firstTs;
    }
    if (lastTs && (!existing.lastTs || lastTs > existing.lastTs)) {
      existing.lastTs = lastTs;
      existing.lastMessage = message || existing.lastMessage;
      existing.lastContext = context ?? existing.lastContext;
    }
  }
  // Sort: errors first, then by total count desc.
  return [...buckets.values()].sort((a, b) => {
    const sev = SEVERITY_RANK[b.maxSeverity] - SEVERITY_RANK[a.maxSeverity];
    if (sev !== 0) return sev;
    return b.totalCount - a.totalCount;
  });
}

const ROW_TONE: Record<WorkerErrorSeverity, string> = {
  error:
    "border-red-300 bg-red-50 text-red-900 dark:border-red-900/60 dark:bg-red-950/60 dark:text-red-200",
  warning:
    "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/60 dark:text-amber-200",
  info: "border-neutral-300 bg-neutral-50 text-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200",
};

export function RunErrorBanner({ runId, events, className = "" }: RunErrorBannerProps) {
  const rows = useMemo(() => aggregate(events), [events]);
  if (rows.length === 0) return null;

  return (
    <section
      role="alert"
      id={`run-error-banner-${runId}`}
      data-testid="run-error-banner"
      className={`flex flex-col gap-2 ${className}`}
    >
      {rows.map((row) => (
        <CategoryRow key={row.category} row={row} />
      ))}
    </section>
  );
}

function CategoryRow({ row }: { row: CategoryAgg }) {
  const [expanded, setExpanded] = useState(false);
  const tone = ROW_TONE[row.maxSeverity] ?? ROW_TONE.error;
  const noun = row.totalCount === 1 ? "occurrence" : "occurrences";

  return (
    <div
      data-testid="run-error-banner-row"
      data-category={row.category}
      data-severity={row.maxSeverity}
      className={`rounded-md border border-l-4 px-3 py-2 text-sm ${tone}`}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <span aria-hidden="true">{row.maxSeverity === "warning" ? "\u26A0" : "\u26A0"}</span>
        <strong className="font-semibold">{row.category}</strong>
        <span>·</span>
        <span data-testid="run-error-banner-count">
          {row.totalCount} {noun}
        </span>
        <span>·</span>
        <span className="truncate font-mono text-[12px]" title={row.lastMessage}>
          “{row.lastMessage || "(no message)"}”
        </span>
        <span className="ml-auto font-mono text-[11px] opacity-80">
          first {formatTs(row.firstTs)} · last {formatTs(row.lastTs)}
        </span>
      </div>
      {row.lastContext && (
        <div className="mt-1">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            data-testid="run-error-banner-toggle"
            className="font-mono text-[11px] underline-offset-2 hover:underline"
          >
            {expanded ? "Hide context" : "View context"}
          </button>
          {expanded && (
            <pre
              data-testid="run-error-banner-context"
              className="mt-1 overflow-auto rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5"
            >
              {JSON.stringify(row.lastContext, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default RunErrorBanner;
