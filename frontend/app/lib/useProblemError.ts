/**
 * `useProblemError` — extracts an RFC 7807 problem object from a React Query
 * error (or any unknown value).
 *
 * Returns the parsed `ProblemDetails` when the error is an `HttpError` that
 * carries a problem body. For any other error shape (plain `Error`,
 * `HttpError` without a problem, `null`, etc.) it returns `null` so callers
 * can safely render nothing / fall back to a generic message.
 *
 * The hook is intentionally pure (no internal state) — it is exposed as a
 * hook rather than a helper so call-sites stay uniform with the rest of the
 * React Query integration and future iterations can cache / normalize
 * without touching call-sites.
 */

import { HttpError, type ProblemDetails } from "./http-client";

export function useProblemError(error: unknown): ProblemDetails | null {
  if (error instanceof HttpError && error.problem) {
    return error.problem;
  }
  return null;
}

export default useProblemError;
