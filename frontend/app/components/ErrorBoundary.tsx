import { Component, type ErrorInfo, type ReactNode } from "react";
import { useRouterState } from "@tanstack/react-router";

/**
 * `<ErrorBoundary>` — catches render-time errors thrown by descendants and
 * shows a friendly fallback with a retry button.
 *
 * This covers *synchronous* render/commit errors only — React Query async
 * errors do not reach here and are rendered via `<ProblemAlert>` on each
 * page. Mount near the root so an unexpected render explosion in one route
 * does not blank the whole app.
 */

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Optional custom fallback; receives the error + reset handler. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundaryCore extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface in dev console; production wiring (e.g. Sentry) is out of
    // scope for Plan C and can slot in here later.
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }

    return (
      <div
        role="alert"
        data-testid="error-boundary-fallback"
        className="mx-auto my-8 max-w-xl rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950 dark:text-red-200"
      >
        <h2 className="text-base font-semibold">Something went wrong</h2>
        <p className="mt-2">
          The page hit an unexpected error while rendering. You can retry, or
          navigate elsewhere and come back.
        </p>
        <pre className="mt-3 max-h-40 overflow-auto rounded bg-red-100 p-2 font-mono text-xs text-red-900 dark:bg-red-900/40 dark:text-red-100">
          {error.message}
        </pre>
        <button
          type="button"
          onClick={this.reset}
          className="mt-4 rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-red-500"
        >
          Retry
        </button>
      </div>
    );
  }
}

/**
 * Router-aware wrapper: React's class ErrorBoundary cannot observe router
 * state directly, and a crash that sets `state.error` would otherwise
 * persist across client-side navigations (user sees the fallback even on a
 * totally different route). We subscribe to the router pathname and remount
 * the inner boundary via `key` so a new route always starts with a clean
 * `state.error = null`.
 *
 * If the router context is unavailable (e.g. a unit test rendering the
 * boundary outside a RouterProvider), `useRouterState` will throw; we fall
 * back to the core boundary with a stable key so tests that only exercise
 * the class behaviour still work.
 */
export function ErrorBoundary(props: ErrorBoundaryProps): ReactNode {
  let pathname = "__no_router__";
  try {
    // useRouterState returns the current router state; selecting only
    // pathname keeps re-renders cheap.
    pathname = useRouterState({
      select: (s) => s.location.pathname,
    });
  } catch {
    // Router context not mounted — keep the default key so the boundary
    // still functions in isolation.
  }
  return <ErrorBoundaryCore key={pathname} {...props} />;
}

export default ErrorBoundary;
