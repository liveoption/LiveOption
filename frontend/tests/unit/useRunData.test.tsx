/**
 * Unit tests for `app/lib/run-data/useRunData` — live status derivation.
 *
 * Network is intercepted via MSW. orval's httpClient rewrites `/v1/*` to
 * `/api/*`, so MSW handlers register `/api/...` paths.
 *
 * useSSE is mocked via vi.mock because SSE has its own dedicated test file
 * and we only need a static status stub here.
 */
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import { RUN_SSE_EVENTS, useRunData } from "../../app/lib/run-data/useRunData";

// SSE has its own test file; stub it out for these status-only tests.
vi.mock("../../app/lib/sse/useSSE", () => ({
  useSSE: vi.fn(() => ({
    status: "closed",
    lastEventId: undefined,
    close: vi.fn(),
  })),
}));

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  vi.clearAllMocks();
});
afterAll(() => server.close());

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
  };
}

/**
 * Register MSW handlers for the four run-data endpoints.
 * Responses are wrapped as `{ data: <payload> }` to match the orval
 * httpClient envelope (`{ data, status, headers }`).
 */
function runHandlers(runId: string, status: string) {
  return [
    http.get(`/api/runs/${runId}`, () =>
      HttpResponse.json({
        id: runId,
        status,
        scenario: "test_scenario",
        kind: "batch",
      }),
    ),
    http.get(`/api/runs/${runId}/timeline`, () =>
      HttpResponse.json([]),
    ),
    http.get(`/api/runs/${runId}/pnl`, () =>
      HttpResponse.json({ points: [] }),
    ),
    http.get(`/api/runs/${runId}/events/history`, () =>
      HttpResponse.json({
        events: [],
        total: 0,
        truncated: false,
        next_cursor: null,
      }),
    ),
  ];
}

describe("useRunData — envelope unwrap", () => {
  it("exposes the parsed run payload (envelope unwrap canary)", async () => {
    const wrapper = makeWrapper();
    // Register the specific run payload first so it takes priority, then add
    // the remaining endpoints (timeline, pnl, events/history) from runHandlers.
    server.use(
      http.get("/api/runs/r-canary", () =>
        HttpResponse.json({
          id: "r-canary",
          kind: "batch",
          scenario: "x",
          status: "running",
          date_start: "2026-01-01",
          date_end: "2026-01-05",
          created_at: "2026-04-18T00:00:00.000Z",
          status_updated_at: "2026-04-18T00:00:00.000Z",
        }),
      ),
      ...runHandlers("r-canary", "running").slice(1), // skip the default run handler
    );
    const { result } = renderHook(() => useRunData("r-canary"), { wrapper });
    await waitFor(() => expect(result.current.run?.id).toBe("r-canary"));
    expect(result.current.run?.status).toBe("running");
    expect(result.current.live).toBe(true);
  });
});

describe("useRunData — data-source switching", () => {
  it("uses static history events when terminal", async () => {
    // Put the override first so it takes priority over runHandlers' default.
    server.use(
      http.get("/api/runs/r-term/events/history", () =>
        HttpResponse.json({
          events: [
            {
              id: 3,
              kind: "x",
              payload: { foo: 1 },
              occurred_at: "2026-01-01T00:00:00Z",
            },
          ],
          total: 1,
          truncated: false,
          next_cursor: null,
        }),
      ),
      ...runHandlers("r-term", "completed"),
    );
    const { result } = renderHook(() => useRunData("r-term"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.live).toBe(false));
    await waitFor(() => expect(result.current.events.length).toBe(1));
    expect(result.current.events[0].id).toBe(3);
    expect(result.current.events[0].kind).toBe("x");
    expect(result.current.eventsTotal).toBe(1);
    expect(result.current.eventsTruncated).toBe(false);
  });

  it("does not fetch /pnl or /events/history when live", async () => {
    let pnlHits = 0;
    let historyHits = 0;
    server.use(
      ...runHandlers("r-live", "running"),
      // MSW v2: handlers added via server.use() are prepended (take priority).
      http.get("/api/runs/r-live/pnl", () => {
        pnlHits++;
        return HttpResponse.json({ items: [] });
      }),
      http.get("/api/runs/r-live/events/history", () => {
        historyHits++;
        return HttpResponse.json({
          events: [],
          total: 0,
          truncated: false,
          next_cursor: null,
        });
      }),
    );
    const { result } = renderHook(() => useRunData("r-live"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.live).toBe(true));
    // Give React Query time to fire any queries it would fire.
    await new Promise((r) => setTimeout(r, 50));
    expect(pnlHits).toBe(0);
    expect(historyHits).toBe(0);
  });

  it("does not open SSE when terminal", async () => {
    const { useSSE: useSSEMock } = await import(
      "../../app/lib/sse/useSSE"
    );
    const mocked = vi.mocked(useSSEMock);
    mocked.mockClear();

    server.use(...runHandlers("r-cold", "completed"));
    const { result } = renderHook(() => useRunData("r-cold"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.live).toBe(false));

    // Find the most recent invocation after live===false is stable.
    const lastCall = mocked.mock.calls.at(-1)?.[0];
    expect(lastCall?.url).toBeNull();
    expect(lastCall?.enabled).toBe(false);
    expect(result.current.sseStatus).toBeNull();
  });
});

describe("useRunData — live status derivation", () => {
  it("returns live=true when status is 'running'", async () => {
    server.use(...runHandlers("r1", "running"));
    const { result } = renderHook(() => useRunData("r1"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.live).toBe(true);
  });

  it("returns live=true when status is 'pending'", async () => {
    server.use(...runHandlers("r2", "pending"));
    const { result } = renderHook(() => useRunData("r2"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.live).toBe(true);
  });

  it("returns live=false when status is 'completed'", async () => {
    server.use(...runHandlers("r3", "completed"));
    const { result } = renderHook(() => useRunData("r3"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.live).toBe(false);
  });

  it("returns live=false when status is 'failed'", async () => {
    server.use(...runHandlers("r4", "failed"));
    const { result } = renderHook(() => useRunData("r4"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.live).toBe(false);
  });
});

describe("useRunData — pagination", () => {
  it("eventsLoadOlder is null when not truncated", async () => {
    const wrapper = makeWrapper();
    server.use(
      http.get("/api/runs/r-paged-1/events/history", () =>
        HttpResponse.json({ events: [], total: 0, truncated: false, next_cursor: null }),
      ),
      ...runHandlers("r-paged-1", "completed"),
    );
    const { result } = renderHook(() => useRunData("r-paged-1"), { wrapper });
    await waitFor(() => expect(result.current.live).toBe(false));
    expect(result.current.eventsLoadOlder).toBeNull();
  });

  it("eventsLoadOlder fetches next cursor and appends older events", async () => {
    const wrapper = makeWrapper();
    server.use(
      // First page: 1 event, total 100, truncated, next_cursor=9.
      http.get("/api/runs/r-paged-2/events/history", ({ request }) => {
        const url = new URL(request.url);
        const cursor = url.searchParams.get("cursor");
        if (cursor === null) {
          return HttpResponse.json({
            events: [
              { id: 10, kind: "x", payload: {}, occurred_at: "2026-01-10T00:00:00Z" },
            ],
            total: 100,
            truncated: true,
            next_cursor: 9,
          });
        }
        // Older page: 1 event with id 9, no further pages.
        return HttpResponse.json({
          events: [
            { id: 9, kind: "x", payload: {}, occurred_at: "2026-01-09T00:00:00Z" },
          ],
          total: 100,
          truncated: false,
          next_cursor: null,
        });
      }),
      ...runHandlers("r-paged-2", "completed"),
    );
    const { result } = renderHook(() => useRunData("r-paged-2"), { wrapper });
    await waitFor(() => expect(result.current.events.length).toBe(1));
    expect(result.current.eventsLoadOlder).not.toBeNull();
    expect(result.current.eventsTruncated).toBe(true);

    await act(async () => {
      await result.current.eventsLoadOlder!();
    });

    await waitFor(() => {
      expect(result.current.events.map((e) => e.id)).toEqual([10, 9]);
      expect(result.current.eventsLoadOlder).toBeNull();
      expect(result.current.eventsTruncated).toBe(false);
    });
  });
});

describe("useRunData — refetchRun", () => {
  it("exposes refetchRun() that triggers a run query refetch", async () => {
    let runHits = 0;
    server.use(
      http.get("/api/runs/r-refetch", () => {
        runHits++;
        return HttpResponse.json({
          id: "r-refetch",
          kind: "batch",
          scenario: "x",
          status: "completed",
          date_start: "2026-01-01",
          date_end: "2026-01-05",
          created_at: "2026-04-18T00:00:00.000Z",
          status_updated_at: "2026-04-18T00:00:00.000Z",
        });
      }),
      http.get("/api/runs/r-refetch/timeline", () => HttpResponse.json([])),
      http.get("/api/runs/r-refetch/pnl", () => HttpResponse.json({ items: [] })),
      http.get("/api/runs/r-refetch/events/history", () =>
        HttpResponse.json({ events: [], total: 0, truncated: false, next_cursor: null }),
      ),
    );

    const wrapper = makeWrapper();
    const { result } = renderHook(() => useRunData("r-refetch"), { wrapper });
    await waitFor(() => expect(result.current.run?.id).toBe("r-refetch"));
    const before = runHits;

    await act(async () => {
      result.current.refetchRun();
      // Allow the refetch to land.
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(runHits).toBeGreaterThan(before);
  });
});

describe("useRunData — live PnL folding", () => {
  it("subscribes to the named event kinds emitted by the run server", async () => {
    const useSSEMock = vi.mocked((await import("../../app/lib/sse/useSSE")).useSSE);
    server.use(...runHandlers("r-named", "running"));
    const { result } = renderHook(() => useRunData("r-named"), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.live).toBe(true));
    expect(useSSEMock).toHaveBeenCalledWith(expect.objectContaining({
      enabled: true,
      events: expect.arrayContaining(["decision.completed", "day.completed", "worker.error"]),
    }));
    expect(RUN_SSE_EVENTS).toContain("message");
  });

  it("folds day.completed SSE events into pnlPoints + livePnl when live", async () => {
    const useSSEMock = vi.mocked(
      (await import("../../app/lib/sse/useSSE")).useSSE,
    );
    let capturedOnEvent: ((kind: string, data: unknown, id: string | undefined) => void) | null = null;
    useSSEMock.mockImplementation((opts: any) => {
      capturedOnEvent = opts.onEvent;
      return { status: "open", lastEventId: undefined, close: vi.fn() } as any;
    });

    server.use(...runHandlers("r-fold", "running"));
    const wrapper = makeWrapper();
    const { result, rerender } = renderHook(() => useRunData("r-fold"), { wrapper });
    await waitFor(() => expect(result.current.live).toBe(true));

    act(() => {
      capturedOnEvent!("day.completed", { trading_day: "2026-01-02", cum_pnl: 100 }, "1");
    });
    act(() => {
      capturedOnEvent!("day.completed", { trading_day: "2026-01-03", cum_pnl: 250 }, "2");
    });
    // A run.started payload's "kind" describes the run, not the SSE event.
    act(() => {
      capturedOnEvent!("run.started", { kind: "batch" }, "3");
    });
    // Non-day.completed event must be ignored.
    act(() => {
      capturedOnEvent!("message", { kind: "tick", t: 0 }, "4");
    });
    rerender();

    await waitFor(() => {
      expect(result.current.pnlPoints.map((p) => ({ d: p.trading_day, c: p.cum_pnl }))).toEqual([
        { d: "2026-01-02", c: 100 },
        { d: "2026-01-03", c: 250 },
      ]);
      expect(result.current.livePnl).toBe(250);
      expect(result.current.events.some((event) => event.kind === "run.started")).toBe(true);
    });
  });
});
