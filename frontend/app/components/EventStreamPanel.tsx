import { useEffect, useRef } from "react";

import EventStream, { type StreamEvent } from "./EventStream";
import type { EventHistoryItem } from "../lib/contracts";

export interface EventStreamPanelProps {
  events: EventHistoryItem[];
  total: number;
  live: boolean;
  truncated: boolean;
  onLoadOlder: (() => void) | null;
}

function toStreamEvents(items: EventHistoryItem[]): StreamEvent[] {
  return items.map((e) => {
    const summaryRaw = (e.payload as { summary?: unknown } | undefined)?.summary;
    return {
      id: e.id,
      ts: e.occurred_at,
      kind: e.kind,
      summary: typeof summaryRaw === "string" ? summaryRaw : null,
    };
  });
}

export default function EventStreamPanel({
  events,
  total,
  live,
  truncated,
  onLoadOlder,
}: EventStreamPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);

  // Track whether the user has scrolled down so we don't fight them.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      userScrolledRef.current = el.scrollTop > 50;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // When live + new event arrives + user is at top, stay at top.
  useEffect(() => {
    if (!live) return;
    const el = scrollRef.current;
    if (el && !userScrolledRef.current) el.scrollTop = 0;
  }, [events, live]);

  const counter = truncated ? `${events.length} of ${total}` : `${total} events`;

  return (
    <section
      data-testid="event-stream-panel"
      className="flex flex-col rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper)]"
    >
      <header className="flex items-center justify-between border-b border-[var(--color-rule)] px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="lo-display text-[color:var(--color-ink)]">Events</span>
          {live && (
            <span
              data-testid="event-stream-live-indicator"
              className="inline-flex items-center gap-1 text-[color:var(--color-accent-positive)]"
            >
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[color:var(--color-accent-positive)]" />
              Live
            </span>
          )}
        </div>
        <span className="text-[color:var(--color-ink-soft)]">{counter}</span>
      </header>
      <div ref={scrollRef} className="max-h-96 overflow-y-auto">
        <EventStream events={toStreamEvents(events)} limit={500} />
      </div>
      {truncated && onLoadOlder && (
        <footer className="border-t border-[var(--color-rule)] px-3 py-2">
          <button
            type="button"
            onClick={onLoadOlder}
            className="text-xs text-[color:var(--color-ink-soft)] hover:text-[color:var(--color-ink)]"
          >
            Load older
          </button>
        </footer>
      )}
    </section>
  );
}
