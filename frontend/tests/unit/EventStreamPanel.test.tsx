import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import EventStreamPanel from "../../app/components/EventStreamPanel";
import type { EventHistoryItem } from "../../app/lib/contracts";

const evt = (id: number): EventHistoryItem => ({
  id,
  kind: "tick",
  payload: { summary: `evt ${id}` },
  occurred_at: "2026-01-01T00:00:00Z",
});

describe("<EventStreamPanel>", () => {
  it("renders events in order", () => {
    render(
      <EventStreamPanel
        events={[evt(3), evt(2), evt(1)]}
        total={3}
        live={false}
        truncated={false}
        onLoadOlder={null}
      />,
    );
    const text = screen.getByTestId("event-stream-panel").textContent ?? "";
    expect(text.indexOf("evt 3")).toBeLessThan(text.indexOf("evt 2"));
    expect(text.indexOf("evt 2")).toBeLessThan(text.indexOf("evt 1"));
  });

  it("shows N of total counter when terminal + truncated", () => {
    render(
      <EventStreamPanel
        events={[evt(1), evt(2)]}
        total={500}
        live={false}
        truncated
        onLoadOlder={() => {}}
      />,
    );
    expect(screen.getByText(/2 of 500/)).toBeInTheDocument();
  });

  it("shows N events counter when not truncated", () => {
    render(
      <EventStreamPanel
        events={[evt(1)]}
        total={1}
        live={false}
        truncated={false}
        onLoadOlder={null}
      />,
    );
    expect(screen.getByText(/1 events?/i)).toBeInTheDocument();
  });

  it("shows live indicator when live=true", () => {
    render(
      <EventStreamPanel events={[]} total={0} live truncated={false} onLoadOlder={null} />,
    );
    expect(screen.getByTestId("event-stream-live-indicator")).toBeInTheDocument();
  });

  it("hides live indicator when live=false", () => {
    render(
      <EventStreamPanel
        events={[]}
        total={0}
        live={false}
        truncated={false}
        onLoadOlder={null}
      />,
    );
    expect(screen.queryByTestId("event-stream-live-indicator")).not.toBeInTheDocument();
  });

  it("renders Load older button when truncated and onLoadOlder set", () => {
    const onLoadOlder = vi.fn();
    render(
      <EventStreamPanel
        events={[evt(1)]}
        total={100}
        live={false}
        truncated
        onLoadOlder={onLoadOlder}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /load older/i }));
    expect(onLoadOlder).toHaveBeenCalledOnce();
  });

  it("hides Load older when onLoadOlder is null", () => {
    render(
      <EventStreamPanel
        events={[evt(1)]}
        total={100}
        live={false}
        truncated
        onLoadOlder={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /load older/i })).not.toBeInTheDocument();
  });

  it("scrolls to top on new live event when at top", () => {
    const { container, rerender } = render(
      <EventStreamPanel
        events={[evt(1)]}
        total={1}
        live
        truncated={false}
        onLoadOlder={null}
      />,
    );
    const scroller = container.querySelector(
      "[data-testid='event-stream-panel'] > div",
    ) as HTMLDivElement;
    scroller.scrollTop = 0;

    rerender(
      <EventStreamPanel
        events={[evt(2), evt(1)]}
        total={2}
        live
        truncated={false}
        onLoadOlder={null}
      />,
    );

    expect(scroller.scrollTop).toBe(0);
  });

  it("does not scroll to top when user has scrolled down", () => {
    const { container, rerender } = render(
      <EventStreamPanel
        events={[evt(1)]}
        total={1}
        live
        truncated={false}
        onLoadOlder={null}
      />,
    );
    const scroller = container.querySelector(
      "[data-testid='event-stream-panel'] > div",
    ) as HTMLDivElement;

    // Simulate the user scrolling down past the 50px threshold.
    Object.defineProperty(scroller, "scrollTop", {
      value: 200,
      writable: true,
      configurable: true,
    });
    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });

    rerender(
      <EventStreamPanel
        events={[evt(2), evt(1)]}
        total={2}
        live
        truncated={false}
        onLoadOlder={null}
      />,
    );

    expect(scroller.scrollTop).toBe(200);
  });
});
