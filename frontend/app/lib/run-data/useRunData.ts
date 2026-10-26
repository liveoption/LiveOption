import { useEffect, useMemo, useState } from "react";

import {
  eventsHistoryV1RunsRunIdEventsHistoryGet,
  useEventsHistoryV1RunsRunIdEventsHistoryGet,
  useGetRunV1RunsRunIdGet,
  usePnlV1RunsRunIdPnlGet,
  useTimelineV1RunsRunIdTimelineGet,
} from "../orval/runs/runs";
import { useSSE, type SSEStatus } from "../sse/useSSE";
import { pushError, type ToastSeverity } from "../error-toast-store";
import { isLiveStatus } from "./isLiveStatus";
import {
  parseContract,
  zRunDetail,
  zTimelinePage,
  zPnLSeries,
  zEventHistoryPage,
  type RunDetail,
  type DecisionRecordRow,
  type PnLPoint,
  type EventHistoryItem,
} from "../contracts";

// Live-mode SSE events don't come from the REST history endpoint, so we
// synthesise conforming EventHistoryItem shapes from SSE (id coerced to number,
// occurred_at set to now). This keeps the `events` field uniformly typed.

export interface UseRunDataResult {
  run: RunDetail | null;
  live: boolean;
  isLoading: boolean;
  timeline: DecisionRecordRow[];
  timelineLoading: boolean;
  pnlPoints: PnLPoint[];
  pnlLoading: boolean;
  livePnl: number | null;
  events: EventHistoryItem[];
  eventsTotal: number;
  eventsTruncated: boolean;
  eventsLoadOlder: (() => Promise<void>) | null;
  sseStatus: SSEStatus | null;
  sseLastEventId: string | null;
  errors: { run?: unknown; timeline?: unknown; pnl?: unknown; events?: unknown };
  refetchRun: () => void;
}

// The run stream uses named SSE frames (`event: day.completed`, etc.), not
// default `message` frames. Keep `message` for older stream producers.
export const RUN_SSE_EVENTS = [
  "message",
  "run.started",
  "run.finished",
  "decision.started",
  "decision.completed",
  "decision.skipped",
  "phase.completed",
  "fill.executed",
  "day.completed",
  "worker.error",
];

export function useRunData(runId: string): UseRunDataResult {
  const runQuery = useGetRunV1RunsRunIdGet(runId, {
    query: { refetchInterval: 10_000 },
  });

  const run = useMemo<RunDetail | null>(() => {
    const raw = runQuery.data?.data;
    if (raw === undefined) return null;
    try {
      return parseContract(zRunDetail, raw);
    } catch {
      return null;
    }
  }, [runQuery.data]);

  const live = isLiveStatus(run?.status);

  const timelineQuery = useTimelineV1RunsRunIdTimelineGet(runId, {
    query: { refetchInterval: live ? 5_000 : false },
  });

  // PnL + events history are fetched in BOTH modes:
  //   - terminal: the only data source.
  //   - live: a baseline so the panel isn't empty when the user opens a
  //     mid-flight run; SSE-pushed updates layer on top via the live fold
  //     and sseEvents accumulator below.
  const pnlQuery = usePnlV1RunsRunIdPnlGet(runId);
  const eventsQuery = useEventsHistoryV1RunsRunIdEventsHistoryGet(runId, {
    limit: 500,
  });

  const timeline = useMemo<DecisionRecordRow[]>(() => {
    const raw = timelineQuery.data?.data;
    if (raw === undefined) return [];
    try {
      return parseContract(zTimelinePage, raw).items;
    } catch {
      return [];
    }
  }, [timelineQuery.data]);

  // SSE event accumulation lives in the hook so we can flip data sources cleanly.
  // Live SSE events are shaped as EventHistoryItem so the `events` field is
  // uniformly typed regardless of data source.
  const [sseEvents, setSseEvents] = useState<EventHistoryItem[]>([]);

  // Live PnL series — accumulated from day.completed SSE events.
  const [livePnlPoints, setLivePnlPoints] = useState<PnLPoint[]>([]);

  // REST PnL baseline (always parsed when present).
  const restPnlPoints = useMemo<PnLPoint[]>(() => {
    const raw = pnlQuery.data?.data;
    if (raw === undefined) return [];
    try {
      return parseContract(zPnLSeries, raw).items;
    } catch {
      return [];
    }
  }, [pnlQuery.data]);

  // In live mode merge SSE-folded points on top of the REST baseline,
  // de-duplicating by trading_day so SSE never shows a stale point.
  const pnlPoints = useMemo<PnLPoint[]>(() => {
    if (!live) return restPnlPoints;
    if (livePnlPoints.length === 0) return restPnlPoints;
    const liveDays = new Set(
      livePnlPoints.map((p) => p.trading_day).filter((d): d is string => !!d),
    );
    return [
      ...restPnlPoints.filter((p) => !p.trading_day || !liveDays.has(p.trading_day)),
      ...livePnlPoints,
    ];
  }, [live, restPnlPoints, livePnlPoints]);

  // Parsed REST event history page (always parsed when present).
  const parsedHistory = useMemo(() => {
    const raw = eventsQuery.data?.data;
    if (raw === undefined) return null;
    try {
      return parseContract(zEventHistoryPage, raw);
    } catch {
      return null;
    }
  }, [eventsQuery.data]);

  // Pagination state for terminal-mode event history.
  const [olderPages, setOlderPages] = useState<EventHistoryItem[]>([]);
  const [latestCursor, setLatestCursor] = useState<number | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);

  // Seed the pagination cursor from the first REST page's next_cursor.
  // (REST history is fetched in both live and terminal modes now.)
  useEffect(() => {
    if (parsedHistory) {
      setLatestCursor(parsedHistory.next_cursor ?? null);
    }
  }, [parsedHistory]);

  const sse = useSSE({
    url: live ? `/api/runs/${runId}/events` : null,
    enabled: live,
    events: RUN_SSE_EVENTS,
    onEvent: (kind, data, id) => {
      const payload = (data ?? {}) as Record<string, unknown>;
      // Only legacy default-message frames carry their event kind in data.
      // A named run.started payload can contain kind="batch", for example.
      const eventKind = kind === "message" && typeof payload.kind === "string"
        ? payload.kind
        : kind;
      setSseEvents((prev) => [
        {
          id: Number(id) || 0,
          kind: eventKind,
          payload,
          occurred_at: new Date().toISOString(),
        },
        ...prev,
      ]);
      // Phase 3: forward `worker.error` events to the global toast store.
      // Decision: option (b) from the spec — piggyback on the existing
      // per-run SSE consumer rather than opening a second multiplexed
      // connection. The store is module-level so toasts persist across
      // route transitions even after this hook unmounts.
      if (eventKind === "worker.error") {
        const inner = (payload.payload ?? payload) as Record<string, unknown>;
        const severity = (inner.severity as ToastSeverity | undefined) ?? "error";
        if (severity === "error" || severity === "warning") {
          const category =
            typeof inner.category === "string" && inner.category.length > 0
              ? inner.category
              : "internal";
          const message =
            typeof inner.message === "string" ? inner.message : "Worker error";
          const count =
            typeof inner.count === "number" && Number.isFinite(inner.count)
              ? inner.count
              : 1;
          const firstTs =
            typeof inner.first_ts === "string"
              ? inner.first_ts
              : new Date().toISOString();
          const lastTs =
            typeof inner.last_ts === "string"
              ? inner.last_ts
              : new Date().toISOString();
          pushError({
            runId,
            category,
            severity,
            count,
            message,
            firstTs,
            lastTs,
          });
        }
      }
      if (eventKind === "day.completed" && typeof payload.cum_pnl === "number") {
        setLivePnlPoints((prev) => [
          ...prev,
          {
            trading_day: typeof payload.trading_day === "string" ? payload.trading_day : null,
            cum_pnl: payload.cum_pnl as number,
            portfolio_value: null,
          },
        ]);
      }
    },
  });

  // Events: REST history (newest-first) ∪ older pages, with SSE-pushed
  // events prepended in live mode and de-duped by id so SSE replay (which
  // overlaps with REST history) doesn't show twice.
  const events = useMemo<EventHistoryItem[]>(() => {
    const restEvents = [...(parsedHistory?.events ?? []), ...olderPages];
    if (!live || sseEvents.length === 0) return restEvents;
    const restIds = new Set(restEvents.map((e) => e.id));
    return [...sseEvents.filter((e) => !restIds.has(e.id)), ...restEvents];
  }, [live, sseEvents, parsedHistory, olderPages]);
  const eventsTotal = parsedHistory?.total ?? events.length;
  const eventsTruncated = parsedHistory !== null && latestCursor !== null;

  const eventsLoadOlder =
    eventsTruncated && latestCursor !== null && !loadingOlder
      ? async () => {
          setLoadingOlder(true);
          try {
            const resp = await eventsHistoryV1RunsRunIdEventsHistoryGet(runId, {
              limit: 500,
              cursor: latestCursor,
            });
            const raw = (resp as { data?: unknown }).data;
            try {
              const page = parseContract(zEventHistoryPage, raw);
              setOlderPages((prev) => [...prev, ...page.events]);
              setLatestCursor(page.next_cursor ?? null);
            } catch {
              // Ignore malformed page; cursor stays put so a retry can re-attempt.
            }
          } finally {
            setLoadingOlder(false);
          }
        }
      : null;

  return {
    run,
    live,
    isLoading: runQuery.isLoading,
    timeline,
    timelineLoading: timelineQuery.isLoading,
    pnlPoints,
    pnlLoading: pnlQuery.isLoading,
    livePnl:
      live && pnlPoints.length > 0
        ? (pnlPoints[pnlPoints.length - 1].cum_pnl ?? null)
        : null,
    events,
    eventsTotal,
    eventsTruncated,
    eventsLoadOlder,
    sseStatus: live ? sse.status : null,
    sseLastEventId: live ? (sse.lastEventId ?? null) : null,
    errors: {
      run: runQuery.error,
      timeline: timelineQuery.error,
      pnl: pnlQuery.error,
      events: eventsQuery.error,
    },
    refetchRun: () => {
      runQuery.refetch();
    },
  };
}
