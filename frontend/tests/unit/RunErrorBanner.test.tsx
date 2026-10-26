import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RunErrorBanner from "../../app/components/RunErrorBanner";
import type { EventHistoryItem } from "../../app/lib/contracts";

const errorEvent = (
  id: number,
  payload: Record<string, unknown>,
): EventHistoryItem => ({
  id,
  kind: "worker.error",
  payload,
  occurred_at: "2026-04-27T07:00:00Z",
});

describe("<RunErrorBanner>", () => {
  it("returns null when there are no worker.error events", () => {
    const { container } = render(
      <RunErrorBanner runId="r1" events={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one row per category with severity tone and count", () => {
    const events: EventHistoryItem[] = [
      errorEvent(1, {
        severity: "error",
        category: "auth",
        message: "Qwen API returned 401",
        count: 17,
        first_ts: "2026-04-27T07:18:42.000Z",
        last_ts: "2026-04-27T07:42:11.000Z",
        context: { provider: "qwen" },
      }),
      errorEvent(2, {
        severity: "warning",
        category: "parse",
        message: "non-JSON content",
        count: 3,
        first_ts: "2026-04-27T07:20:00.000Z",
        last_ts: "2026-04-27T07:30:00.000Z",
      }),
    ];
    render(<RunErrorBanner runId="r1" events={events} />);

    const rows = screen.getAllByTestId("run-error-banner-row");
    expect(rows).toHaveLength(2);

    const auth = rows.find((r) => r.getAttribute("data-category") === "auth")!;
    expect(auth.getAttribute("data-severity")).toBe("error");
    expect(auth.textContent).toMatch(/17 occurrences/);
    expect(auth.textContent).toMatch(/Qwen API returned 401/);

    const parse = rows.find((r) => r.getAttribute("data-category") === "parse")!;
    expect(parse.getAttribute("data-severity")).toBe("warning");
  });

  it("takes the max count across multiple flushes for the same category", () => {
    const events: EventHistoryItem[] = [
      errorEvent(1, {
        severity: "error",
        category: "auth",
        message: "401",
        count: 1,
        last_ts: "2026-04-27T07:00:00.000Z",
      }),
      errorEvent(2, {
        severity: "error",
        category: "auth",
        message: "401",
        count: 10,
        last_ts: "2026-04-27T07:30:00.000Z",
      }),
      errorEvent(3, {
        severity: "error",
        category: "auth",
        message: "401",
        count: 100,
        last_ts: "2026-04-27T08:00:00.000Z",
      }),
    ];
    render(<RunErrorBanner runId="r1" events={events} />);
    expect(screen.getByTestId("run-error-banner-count").textContent).toMatch(
      /100 occurrences/,
    );
  });

  it("toggles the context expander", () => {
    const events: EventHistoryItem[] = [
      errorEvent(1, {
        severity: "error",
        category: "auth",
        message: "401",
        count: 1,
        context: { provider: "qwen", model_id: "qwen-plus" },
      }),
    ];
    render(<RunErrorBanner runId="r1" events={events} />);

    expect(screen.queryByTestId("run-error-banner-context")).toBeNull();
    fireEvent.click(screen.getByTestId("run-error-banner-toggle"));
    const ctx = screen.getByTestId("run-error-banner-context");
    expect(ctx.textContent).toMatch(/qwen-plus/);
  });

  it("ignores non-worker.error events", () => {
    const events: EventHistoryItem[] = [
      {
        id: 1,
        kind: "day.completed",
        payload: { trading_day: "2024-01-02" },
        occurred_at: "2026-04-27T07:00:00Z",
      },
    ];
    const { container } = render(
      <RunErrorBanner runId="r1" events={events} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
