import { formatDateTime } from "../lib/format";

/**
 * Narrow event shape expected by the `<EventStream>` component.
 *
 * Mirrors the persisted columns returned by
 * `GET /v1/runs/{run_id}/events` (SSE). Task 10 wires the live stream; for
 * Task 7 the component renders whatever the caller passes, which is the
 * empty array today plus a placeholder hint so the pane isn't blank.
 */
export interface StreamEvent {
  id?: string | number;
  ts?: string | null;
  kind?: string | null;
  summary?: string | null;
}

export interface EventStreamProps {
  events: StreamEvent[];
  /** Max rows to render (default 50). */
  limit?: number;
}

/**
 * Compact newest-first list of recent events for a run.
 *
 * Intentionally dumb: no SSE wiring, no autoscroll toggle, no "view
 * prompt/response" deep-links — those land in `/runs/:runId` (which auto-
 * tails SSE while the run is pending/running) and wrap this component.
 */
export default function EventStream({ events, limit = 50 }: EventStreamProps) {
  if (!events || events.length === 0) {
    return (
      <div
        data-testid="event-stream-empty"
        className="rounded-md border border-dashed border-neutral-300 p-4 text-xs text-neutral-500 dark:border-neutral-700"
      >
        Live event stream lands with Task 10. Historical events will appear
        here once a run is attached.
      </div>
    );
  }

  const rows = events.slice(0, limit);

  return (
    <ul
      data-testid="event-stream"
      aria-label="Event stream"
      aria-live="polite"
      aria-relevant="additions"
      className="flex flex-col divide-y divide-neutral-200 rounded-md border border-neutral-200 text-xs dark:divide-neutral-800 dark:border-neutral-800"
    >
      {rows.map((ev, idx) => (
        <li
          key={ev.id ?? idx}
          data-testid="event-row"
          className="flex items-center gap-2 px-3 py-1.5"
        >
          <span className="w-32 shrink-0 font-mono text-neutral-500">
            {formatDateTime(ev.ts) || "—"}
          </span>
          <span className="w-32 shrink-0 truncate font-medium text-neutral-700 dark:text-neutral-300">
            {ev.kind ?? "event"}
          </span>
          <span className="flex-1 truncate text-neutral-600 dark:text-neutral-400">
            {ev.summary ?? ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
