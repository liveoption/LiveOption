/**
 * Tests for useSSE hook.
 *
 * Covers:
 *   - EventSource opens on mount, closes on unmount
 *   - Named-event handler dispatch with JSON parsing
 *   - lastEventId tracking across events
 *   - Reconnect on error, passing Last-Event-ID via query string fallback
 *   - Heartbeat timeout triggers reconnect when no events arrive
 *   - enabled=false skips connection
 *   - Status transitions (connecting -> open -> error -> connecting -> open)
 */
import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSSE } from "../../app/lib/sse/useSSE";

type Listener = (e: MessageEvent) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  url: string;
  readyState = 0;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  private listeners = new Map<string, Set<Listener>>();

  constructor(url: string) {
    this.url = url;
    this.readyState = MockEventSource.CONNECTING;
    MockEventSource.instances.push(this);
  }

  addEventListener(kind: string, handler: Listener): void {
    if (!this.listeners.has(kind)) this.listeners.set(kind, new Set());
    this.listeners.get(kind)!.add(handler);
  }

  removeEventListener(kind: string, handler: Listener): void {
    this.listeners.get(kind)?.delete(handler);
  }

  close(): void {
    this.readyState = MockEventSource.CLOSED;
  }

  // Test helpers
  _open(): void {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  _emit(kind: string, data: unknown, id?: string): void {
    const evt = new MessageEvent(kind, {
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId: id ?? "",
    });
    if (kind === "message") this.onmessage?.(evt);
    for (const h of this.listeners.get(kind) ?? []) h(evt);
  }

  _error(): void {
    this.onerror?.(new Event("error"));
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.useFakeTimers();
  (globalThis as any).EventSource = MockEventSource;
});

afterEach(() => {
  vi.useRealTimers();
  delete (globalThis as any).EventSource;
});

describe("useSSE", () => {
  it("opens EventSource on mount and closes on unmount", () => {
    const { unmount } = renderHook(() => useSSE({ url: "/api/sse", onEvent: () => {} }));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toBe("/api/sse");
    unmount();
    expect(MockEventSource.instances[0].readyState).toBe(MockEventSource.CLOSED);
  });

  it("skips connection when enabled=false", () => {
    renderHook(() =>
      useSSE({ url: "/api/sse", onEvent: () => {}, enabled: false }),
    );
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("skips connection when url is null", () => {
    renderHook(() => useSSE({ url: null, onEvent: () => {} }));
    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("transitions status connecting -> open on onopen", () => {
    const { result } = renderHook(() =>
      useSSE({ url: "/api/sse", onEvent: () => {} }),
    );
    expect(result.current.status).toBe("connecting");
    act(() => {
      MockEventSource.instances[0]._open();
    });
    expect(result.current.status).toBe("open");
  });

  it("dispatches named events with parsed JSON and lastEventId", () => {
    const onEvent = vi.fn();
    const { result } = renderHook(() =>
      useSSE({
        url: "/api/sse",
        onEvent,
        events: ["decision.completed", "snapshot.updated"],
      }),
    );
    const es = MockEventSource.instances[0];
    act(() => es._open());
    act(() => es._emit("decision.completed", { x: 1 }, "evt-42"));
    expect(onEvent).toHaveBeenCalledWith(
      "decision.completed",
      { x: 1 },
      "evt-42",
    );
    expect(result.current.lastEventId).toBe("evt-42");

    act(() => es._emit("snapshot.updated", { y: 2 }, "evt-43"));
    expect(result.current.lastEventId).toBe("evt-43");
  });

  it("falls back to raw string when JSON.parse fails", () => {
    const onEvent = vi.fn();
    renderHook(() =>
      useSSE({ url: "/api/sse", onEvent, events: ["ping"] }),
    );
    const es = MockEventSource.instances[0];
    act(() => es._emit("ping", "not-json", "id-1"));
    expect(onEvent).toHaveBeenCalledWith("ping", "not-json", "id-1");
  });

  it("reconnects on error and appends Last-Event-ID query param", () => {
    const onEvent = vi.fn();
    renderHook(() =>
      useSSE({
        url: "/api/sse",
        onEvent,
        events: ["tick"],
        reconnectDelayMs: 10,
      }),
    );
    const first = MockEventSource.instances[0];
    act(() => first._open());
    act(() => first._emit("tick", { n: 1 }, "evt-1"));

    // Simulate error
    act(() => first._error());
    // Advance reconnect timer
    act(() => {
      vi.advanceTimersByTime(10);
    });

    expect(MockEventSource.instances).toHaveLength(2);
    const second = MockEventSource.instances[1];
    expect(second.url).toContain("last_event_id=evt-1");
  });

  it("preserves existing query string when appending last_event_id", () => {
    renderHook(() =>
      useSSE({
        url: "/api/sse?token=abc",
        onEvent: () => {},
        events: ["tick"],
        reconnectDelayMs: 1,
      }),
    );
    const first = MockEventSource.instances[0];
    act(() => first._open());
    act(() => first._emit("tick", {}, "evt-9"));
    act(() => first._error());
    act(() => vi.advanceTimersByTime(1));
    const second = MockEventSource.instances[1];
    expect(second.url).toMatch(/token=abc/);
    expect(second.url).toMatch(/last_event_id=evt-9/);
  });

  it("status becomes error then connecting on reconnect", () => {
    const { result } = renderHook(() =>
      useSSE({
        url: "/api/sse",
        onEvent: () => {},
        reconnectDelayMs: 5,
      }),
    );
    const first = MockEventSource.instances[0];
    act(() => first._open());
    expect(result.current.status).toBe("open");
    act(() => first._error());
    expect(result.current.status).toBe("error");
    act(() => vi.advanceTimersByTime(5));
    expect(result.current.status).toBe("connecting");
    expect(MockEventSource.instances).toHaveLength(2);
  });

  it("heartbeatTimeoutMs triggers reconnect when silent", () => {
    renderHook(() =>
      useSSE({
        url: "/api/sse",
        onEvent: () => {},
        events: ["tick"],
        heartbeatTimeoutMs: 1000,
        reconnectDelayMs: 1,
      }),
    );
    const first = MockEventSource.instances[0];
    act(() => first._open());
    // No events for > heartbeatTimeoutMs
    act(() => vi.advanceTimersByTime(1001));
    // After heartbeat fires, reconnect delay elapses
    act(() => vi.advanceTimersByTime(1));
    expect(MockEventSource.instances.length).toBeGreaterThanOrEqual(2);
    expect(first.readyState).toBe(MockEventSource.CLOSED);
  });

  it("heartbeat is reset by incoming events", () => {
    renderHook(() =>
      useSSE({
        url: "/api/sse",
        onEvent: () => {},
        events: ["tick"],
        heartbeatTimeoutMs: 1000,
        reconnectDelayMs: 1,
      }),
    );
    const first = MockEventSource.instances[0];
    act(() => first._open());
    // Emit event every 500ms so heartbeat never fires
    for (let i = 0; i < 5; i++) {
      act(() => vi.advanceTimersByTime(500));
      act(() => first._emit("tick", { i }, `id-${i}`));
    }
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("close() manually tears down and stops reconnect", () => {
    const { result } = renderHook(() =>
      useSSE({
        url: "/api/sse",
        onEvent: () => {},
        reconnectDelayMs: 10,
      }),
    );
    const first = MockEventSource.instances[0];
    act(() => first._open());
    act(() => {
      result.current.close();
    });
    expect(first.readyState).toBe(MockEventSource.CLOSED);
    expect(result.current.status).toBe("closed");

    // An error after manual close should not reconnect
    act(() => first._error());
    act(() => vi.advanceTimersByTime(100));
    expect(MockEventSource.instances).toHaveLength(1);
  });
});
