/**
 * useSSE — React hook wrapping the native EventSource API.
 *
 * Designed for the LiveOption Job Server where the frontend consumes
 * `/api/runs/:id/events` (proxied to FastAPI). The native `EventSource`
 * auto-reconnects on error, but:
 *   - It does not let us set arbitrary headers (so `Last-Event-ID` must be
 *     passed via query string as a fallback across reconnects).
 *   - It does not surface silent connection drops; we add an optional
 *     `heartbeatTimeoutMs` watchdog.
 *   - Consumers want a typed `onEvent(kind, data, id)` callback and a
 *     status indicator for the UI.
 *
 * Behavior:
 *   - On mount (or when `url` / `enabled` changes), opens an `EventSource`.
 *   - Subscribes to the event kinds listed in `events` (default: `["message"]`).
 *   - Tracks `lastEventId` from every delivered event.
 *   - On error, closes the socket and schedules a reconnect after
 *     `reconnectDelayMs` (default 1000ms), re-opening with `last_event_id`
 *     appended to the query string.
 *   - If `heartbeatTimeoutMs` is set, a reconnect is forced whenever no
 *     event is received within that window.
 *   - `close()` tears down the socket, cancels any pending reconnect, and
 *     prevents further auto-reconnect for the remainder of the effect.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type SSEStatus = "connecting" | "open" | "closed" | "error";

export type SSEEventHandler = (
  kind: string,
  data: unknown,
  id: string | undefined,
) => void;

export interface UseSSEOptions {
  /** SSE endpoint. Pass `null` to skip connecting. */
  url: string | null;
  /** Callback fired for every subscribed event. */
  onEvent: SSEEventHandler;
  /** Which named events to subscribe to. Defaults to `["message"]`. */
  events?: string[];
  /** Whether to connect at all. Defaults to true. */
  enabled?: boolean;
  /** Delay before attempting reconnect after an error. Default 1000ms. */
  reconnectDelayMs?: number;
  /**
   * If set, force a reconnect when no event has been received for this
   * many milliseconds. Useful for detecting silent TCP drops.
   */
  heartbeatTimeoutMs?: number;
}

export interface UseSSEReturn {
  status: SSEStatus;
  lastEventId: string | undefined;
  /** Manually close the connection and stop auto-reconnect. */
  close: () => void;
}

function appendLastEventId(url: string, id: string): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}last_event_id=${encodeURIComponent(id)}`;
}

export function useSSE(opts: UseSSEOptions): UseSSEReturn {
  const {
    url,
    onEvent,
    events,
    enabled = true,
    reconnectDelayMs = 1000,
    heartbeatTimeoutMs,
  } = opts;

  const [status, setStatus] = useState<SSEStatus>("connecting");
  const [lastEventId, setLastEventId] = useState<string | undefined>(undefined);

  // Refs keep the latest values visible to the long-lived effect without
  // retriggering it when they change.
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const lastEventIdRef = useRef<string | undefined>(undefined);

  // External kill switch: flipped by `close()` to suppress reconnect.
  const manuallyClosedRef = useRef(false);
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const eventsKey = (events ?? ["message"]).join(",");

  const close = useCallback(() => {
    manuallyClosedRef.current = true;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (heartbeatTimerRef.current) {
      clearTimeout(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    esRef.current?.close();
    esRef.current = null;
    setStatus("closed");
  }, []);

  useEffect(() => {
    if (!url || !enabled) {
      setStatus("closed");
      return;
    }

    manuallyClosedRef.current = false;
    const eventList = events ?? ["message"];

    const clearHeartbeat = () => {
      if (heartbeatTimerRef.current) {
        clearTimeout(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
    };

    const armHeartbeat = () => {
      if (!heartbeatTimeoutMs) return;
      clearHeartbeat();
      heartbeatTimerRef.current = setTimeout(() => {
        // Silent drop detected — force a reconnect by simulating an error.
        if (manuallyClosedRef.current) return;
        esRef.current?.close();
        scheduleReconnect();
      }, heartbeatTimeoutMs);
    };

    const scheduleReconnect = () => {
      if (manuallyClosedRef.current) return;
      if (reconnectTimerRef.current) return;
      setStatus("error");
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        if (manuallyClosedRef.current) return;
        setStatus("connecting");
        open();
      }, reconnectDelayMs);
    };

    const open = () => {
      const id = lastEventIdRef.current;
      const effectiveUrl = id ? appendLastEventId(url, id) : url;
      const es = new EventSource(effectiveUrl);
      esRef.current = es;

      es.onopen = () => {
        setStatus("open");
        armHeartbeat();
      };

      es.onerror = () => {
        // Native EventSource auto-reconnects, but we want explicit control
        // so we can carry `last_event_id` forward via query string.
        clearHeartbeat();
        es.close();
        if (esRef.current === es) esRef.current = null;
        scheduleReconnect();
      };

      // Watchdog must reset on ANY incoming frame, not just the subset
      // the consumer subscribed to. Server-side `heartbeat:` keep-alives
      // (sent during idle periods to keep proxies from closing the socket)
      // arrive either as the default `message` kind or as a named
      // `heartbeat` event — either one counts as liveness.
      es.onmessage = () => {
        armHeartbeat();
      };
      es.addEventListener("heartbeat", () => {
        armHeartbeat();
      });

      for (const kind of eventList) {
        es.addEventListener(kind, (raw: Event) => {
          const e = raw as MessageEvent;
          let data: unknown = e.data;
          if (typeof e.data === "string") {
            try {
              data = JSON.parse(e.data);
            } catch {
              data = e.data;
            }
          }
          const id = e.lastEventId || undefined;
          if (id) {
            lastEventIdRef.current = id;
            setLastEventId(id);
          }
          armHeartbeat();
          onEventRef.current(kind, data, id);
        });
      }
    };

    setStatus("connecting");
    open();

    return () => {
      manuallyClosedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      clearHeartbeat();
      esRef.current?.close();
      esRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled, eventsKey, reconnectDelayMs, heartbeatTimeoutMs]);

  return { status, lastEventId, close };
}
