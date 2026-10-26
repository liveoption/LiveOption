import { QueryClient } from "@tanstack/react-query";

/**
 * Singleton QueryClient used by the root route's QueryClientProvider.
 *
 * Exported as a module-level singleton so hooks/tests can import it directly
 * (e.g. for `queryClient.invalidateQueries` from SSE push updates). Defaults
 * match Plan C Task 2: 30s staleTime, 5m gcTime.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
    },
  },
});
