import type { ProblemDetails } from "../lib/http-client";

/**
 * `<ProblemAlert>` — renders an RFC 7807 `application/problem+json` document
 * as a compact, styled error card.
 *
 * Use on route pages for query error paths when the error is an `HttpError`
 * carrying a parsed `problem` body (see `useProblemError`). Falls back to a
 * generic message when fields are absent.
 *
 * Accessibility: rendered with `role="alert"` so screen readers announce it
 * when it appears in the DOM. Task 14 owns broader a11y polish.
 */

export interface ProblemAlertProps {
  problem: ProblemDetails | null | undefined;
  /** Optional fallback title when `problem?.title` is missing. */
  fallbackTitle?: string;
  /** Optional additional class names for layout tweaks. */
  className?: string;
}

export function ProblemAlert({
  problem,
  fallbackTitle = "Request failed",
  className = "",
}: ProblemAlertProps) {
  if (!problem) return null;

  const title = problem.title ?? fallbackTitle;
  const status = problem.status;
  const detail = problem.detail;
  const type = problem.type;

  return (
    <div
      role="alert"
      data-testid="problem-alert"
      className={`rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950 dark:text-red-200 ${className}`}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <strong className="font-semibold">{title}</strong>
        {typeof status === "number" && (
          <span className="rounded bg-red-100 px-1.5 py-0.5 font-mono text-[11px] text-red-700 dark:bg-red-900/40 dark:text-red-300">
            {status}
          </span>
        )}
      </div>
      {detail && <p className="mt-1 whitespace-pre-wrap">{detail}</p>}
      {type && type !== "about:blank" && (
        <p className="mt-1 font-mono text-[11px] text-red-700/80 dark:text-red-300/80">
          {type}
        </p>
      )}
    </div>
  );
}

export default ProblemAlert;
