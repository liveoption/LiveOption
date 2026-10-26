/**
 * `<ErrorToastHost />` — global toast region for `worker.error` events.
 *
 * Phase 3 of the worker error surfacing feature. Renders a fixed top-right
 * stack of dedup-keyed toasts; mounted once at the root layout level so
 * toasts survive route transitions.
 *
 * Conventions:
 *   - Color palette mirrors `ProblemAlert.tsx` (red-50/red-200/red-800 light,
 *     red-950/red-900 dark for severity=error; amber for warning).
 *   - Container: `role="status" aria-live="polite"`.
 *   - Each error toast: `role="alert" aria-live="assertive"` (per
 *     ProblemAlert convention); warnings use polite to avoid drowning out
 *     errors.
 *   - Auto-dismiss: 8s for error, 4s for warning. The timer resets whenever
 *     the toast's count changes (keeps the toast visible across a burst).
 *   - Max 3 visible; remaining queue counted as "+N more" pill.
 */
import { useEffect, useMemo, useRef } from "react";
import { Link } from "@tanstack/react-router";
import {
  dismiss,
  useErrorToasts,
  type ToastSeverity,
  type ToastState,
} from "../lib/error-toast-store";

const MAX_VISIBLE = 3;
const DISMISS_MS: Record<ToastSeverity, number> = {
  error: 8000,
  warning: 4000,
  info: 4000,
};

const PALETTE: Record<ToastSeverity, string> = {
  error:
    "border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950 dark:text-red-200",
  warning:
    "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950 dark:text-amber-200",
  info: "border-slate-200 bg-slate-50 text-slate-800 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200",
};

const ICON: Record<ToastSeverity, string> = {
  error: "✕",
  warning: "⚠",
  info: "ℹ",
};

function formatTs(ts: string): string {
  // RFC3339 → HH:MM:SS local. Defensive: if parse fails, return raw.
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString(undefined, { hour12: false });
}

interface ToastCardProps {
  toast: ToastState;
}

function ToastCard({ toast }: ToastCardProps) {
  const { key, severity, runId, category, count, message, firstSeenTs } = toast;
  const isError = severity === "error";

  // Auto-dismiss timer; reset whenever count changes (a fresh update keeps
  // the toast visible). lastTs is what `pushError` mutates per update.
  const lastTsRef = useRef(toast.lastTs);
  useEffect(() => {
    const ms = DISMISS_MS[severity] ?? 4000;
    const timer = setTimeout(() => dismiss(key), ms);
    lastTsRef.current = toast.lastTs;
    return () => clearTimeout(timer);
    // Re-arm when count or lastTs changes — count is the user-visible
    // signal, lastTs is what changed in the store.
  }, [key, severity, count, toast.lastTs]);

  return (
    <div
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      data-testid="error-toast"
      data-toast-key={key}
      data-toast-category={category}
      className={`pointer-events-auto w-80 rounded-md border-l-4 border p-3 text-sm shadow-md ${PALETTE[severity]}`}
    >
      <div className="flex items-start gap-2">
        <span aria-hidden="true" className="mt-0.5 font-mono text-base">
          {ICON[severity]}
        </span>
        <Link
          to="/runs/$runId"
          params={{ runId }}
          className="flex-1 cursor-pointer no-underline text-inherit"
          data-testid="error-toast-link"
        >
          <div className="flex flex-wrap items-baseline gap-2">
            <strong className="font-semibold">{category}</strong>
            <span
              data-testid="error-toast-count"
              className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
                isError
                  ? "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
              }`}
            >
              {count}
            </span>
          </div>
          <p className="mt-1 line-clamp-2 break-words">{message}</p>
          <p className="mt-1 font-mono text-[11px] opacity-70">
            first seen {formatTs(firstSeenTs)}
          </p>
        </Link>
        <button
          type="button"
          aria-label="Dismiss"
          data-testid="error-toast-dismiss"
          onClick={() => dismiss(key)}
          className="rounded p-0.5 text-current opacity-60 hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-current"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

export function ErrorToastHost() {
  const toasts = useErrorToasts();

  // Most-recent-update first (lastTs desc). Stable secondary by key for
  // determinism when timestamps tie (test events fired in the same ms).
  const ordered = useMemo(() => {
    return [...toasts].sort((a, b) => {
      if (a.lastTs === b.lastTs) return a.key < b.key ? -1 : 1;
      return a.lastTs < b.lastTs ? 1 : -1;
    });
  }, [toasts]);

  const visible = ordered.slice(0, MAX_VISIBLE);
  const overflow = ordered.length - visible.length;

  if (toasts.length === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="error-toast-host"
      className="pointer-events-none fixed top-4 right-4 z-50 flex flex-col gap-2"
    >
      {visible.map((t) => (
        <ToastCard key={t.key} toast={t} />
      ))}
      {overflow > 0 && (
        <div
          data-testid="error-toast-overflow"
          className="pointer-events-auto self-end rounded bg-slate-800/90 px-2 py-1 font-mono text-[11px] text-slate-100 shadow"
        >
          +{overflow} more
        </div>
      )}
    </div>
  );
}

export default ErrorToastHost;
