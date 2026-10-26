import type { SSEStatus } from "../lib/sse/useSSE";

/**
 * SSEIndicator — pill-shaped connection-state indicator for the
 * `/runs/:runId` page when the run is in pending/running state.
 *
 * Mirrors the four `SSEStatus` values returned by `useSSE`:
 *   - `connecting` (amber, spinner-like dot)
 *   - `open`       (green, filled dot)
 *   - `error`      (red, hollow dot)
 *   - `closed`     (neutral, hollow dot)
 *
 * Purely presentational: the caller decides when to render it. The plan
 * calls out a "surface when status != open for >2s" affordance; that
 * debounce lives in the page so the component itself stays deterministic
 * and trivial to snapshot/test.
 */
export interface SSEIndicatorProps {
  status: SSEStatus;
  /** Optional short hint shown to the right of the status word. */
  detail?: string | null;
}

const TONE: Record<SSEStatus, { dot: string; pill: string; label: string }> = {
  connecting: {
    dot: "bg-amber-400 animate-pulse",
    pill: "bg-amber-50 text-amber-800 border-amber-200 dark:bg-[var(--color-amber-950)]/40 dark:text-[var(--color-amber-200)] dark:border-[var(--color-amber-900)]",
    label: "Connecting",
  },
  open: {
    dot: "bg-emerald-500",
    pill: "bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-[var(--color-emerald-950)]/40 dark:text-[var(--color-emerald-200)] dark:border-[var(--color-emerald-300)]",
    label: "Live",
  },
  error: {
    dot: "bg-red-500 animate-pulse",
    pill: "bg-red-50 text-red-800 border-red-200 dark:bg-[var(--color-red-950)]/40 dark:text-[var(--color-red-300)] dark:border-[var(--color-red-400)]",
    label: "Reconnecting",
  },
  closed: {
    dot: "bg-neutral-400",
    pill: "bg-neutral-100 text-neutral-700 border-neutral-200 dark:bg-[var(--color-neutral-900)] dark:text-[var(--color-neutral-300)] dark:border-[var(--color-neutral-800)]",
    label: "Closed",
  },
};

export default function SSEIndicator({ status, detail }: SSEIndicatorProps) {
  const tone = TONE[status];
  return (
    <span
      data-testid="sse-indicator"
      data-status={status}
      role="status"
      aria-live="polite"
      aria-label={`Live stream: ${tone.label}${detail ? ` ${detail}` : ""}`}
      title={
        status === "connecting" ? "Connecting to live event stream..." :
        status === "open" ? "Live connection — receiving event updates" :
        status === "error" ? "Connection interrupted, reconnecting..." :
        "Stream closed — run has ended"
      }
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${tone.pill}`}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-2 w-2 rounded-full ${tone.dot}`}
      />
      <span>{tone.label}</span>
      {detail ? (
        <span className="ml-1 font-mono text-[10px] opacity-70">{detail}</span>
      ) : null}
    </span>
  );
}
