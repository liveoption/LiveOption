/**
 * error-toast-store — global, cross-route store for `worker.error` toast state.
 *
 * Uses a shared Map and `useSyncExternalStore` to subscribe components to
 * updates without adding a state-management dependency.
 *
 * Keying: a toast is uniquely identified by `${run_id}:${category}`. The same
 * key dedupes incoming events; subsequent dispatches with the same key
 * update the existing entry's count + last_ts + message in place.
 *
 * Persistence: the store is a module-level singleton, so it survives
 * client-side route transitions. It does not persist across full page
 * reloads — by design (a stale 401 toast from yesterday would be noise).
 */
import { useSyncExternalStore } from "react";

export type ToastSeverity = "error" | "warning" | "info";

export interface ToastState {
  /** Stable key: `${runId}:${category}`. */
  key: string;
  runId: string;
  category: string;
  severity: ToastSeverity;
  count: number;
  message: string;
  firstSeenTs: string;
  lastTs: string;
}

export interface PushErrorInput {
  runId: string;
  category: string;
  severity: ToastSeverity;
  count: number;
  message: string;
  firstTs: string;
  lastTs: string;
}

type Listener = () => void;

const state: { toasts: Map<string, ToastState> } = { toasts: new Map() };
let snapshotCache: ReadonlyArray<ToastState> = [];
const listeners = new Set<Listener>();

function recomputeSnapshot(): void {
  snapshotCache = Array.from(state.toasts.values());
}

function notify(): void {
  recomputeSnapshot();
  for (const l of listeners) l();
}

export function pushError(input: PushErrorInput): void {
  const key = `${input.runId}:${input.category}`;
  const existing = state.toasts.get(key);
  if (existing) {
    // Upsert: monotonically take the larger count, freshen the message.
    const next: ToastState = {
      ...existing,
      // Server-side counter is authoritative; if a stale event arrives
      // (lower count), keep the higher value rather than regress.
      count: Math.max(existing.count, input.count),
      message: input.message || existing.message,
      severity: input.severity,
      lastTs: input.lastTs,
      // firstSeenTs is preserved across updates.
    };
    state.toasts.set(key, next);
  } else {
    state.toasts.set(key, {
      key,
      runId: input.runId,
      category: input.category,
      severity: input.severity,
      count: Math.max(1, input.count),
      message: input.message,
      firstSeenTs: input.firstTs,
      lastTs: input.lastTs,
    });
  }
  notify();
}

export function dismiss(key: string): void {
  if (state.toasts.delete(key)) notify();
}

export function dismissAll(): void {
  if (state.toasts.size === 0) return;
  state.toasts.clear();
  notify();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): ReadonlyArray<ToastState> {
  return snapshotCache;
}

/** SSR snapshot — empty list before hydration so output is deterministic. */
const SSR_SNAPSHOT: ReadonlyArray<ToastState> = [];
function getServerSnapshot(): ReadonlyArray<ToastState> {
  return SSR_SNAPSHOT;
}

export function useErrorToasts(): ReadonlyArray<ToastState> {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/* --- Test/E2E injection hook ------------------------------------------- */
//
// Playwright cannot easily push an SSE event into the page's existing
// `EventSource`, so we expose a small `window` hook that the spec calls via
// `page.evaluate()` to inject `worker.error` events directly into the store.
// This is a strict superset of the SSE path (the SSE wiring also calls
// `pushError` below), so testing it covers the production flow.
declare global {
  interface Window {
    __LO_ERROR_TOASTS__?: {
      push: (input: PushErrorInput) => void;
      dismissAll: () => void;
    };
  }
}

if (typeof window !== "undefined") {
  window.__LO_ERROR_TOASTS__ = {
    push: pushError,
    dismissAll,
  };
}
