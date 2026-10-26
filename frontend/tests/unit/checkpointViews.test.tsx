/**
 * Tests for family-specific checkpoint views (Phase D-full-4).
 *
 * Views are pure presentational components keyed off the
 * `family_view.kind` discriminator. We assert each variant renders the
 * core fields and that FamilyCheckpointView dispatches correctly /
 * returns null for unknown kinds.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import OverlayView from "../../app/components/checkpoint-view/OverlayView";
import EarningsView from "../../app/components/checkpoint-view/EarningsView";
import IntradayView from "../../app/components/checkpoint-view/IntradayView";
import FamilyCheckpointView from "../../app/components/checkpoint-view/FamilyCheckpointView";

describe("OverlayView", () => {
  it("renders empty state for missing fields", () => {
    render(<OverlayView view={{}} />);
    expect(screen.getByTestId("overlay-view")).toBeTruthy();
    expect(screen.getByText(/No underlying holdings/i)).toBeTruthy();
    expect(screen.getByText(/No active overlay legs/i)).toBeTruthy();
  });

  it("renders holdings + legs + hedge status", () => {
    render(
      <OverlayView
        view={{
          underlying_holdings: [
            { symbol: "SPY", shares: 100, cost_basis: 400, market_value: 410 },
          ],
          overlay_legs: [{ contract: "SPY 250620 C 500", income: 50, days_to_expiry: 30 }],
          delta_hedge_status: { net_delta: 0.05 },
        }}
      />,
    );
    expect(screen.getByTestId("overlay-holdings")).toBeTruthy();
    expect(screen.getByTestId("overlay-legs")).toBeTruthy();
    expect(screen.getByTestId("overlay-hedge-status")).toBeTruthy();
  });
});

describe("EarningsView", () => {
  it("renders empty counts when no events", () => {
    render(<EarningsView view={{}} />);
    expect(screen.getByTestId("earnings-view")).toBeTruthy();
  });

  it("renders active and pending lists", () => {
    render(
      <EarningsView
        view={{
          active_event_windows: [
            { ticker: "AAPL", event_date: "2025-04-30", phase: "in" },
          ],
          pending_events: [{ ticker: "MSFT", event_date: "2025-05-02" }],
          post_event_realized: 123,
        }}
      />,
    );
    expect(screen.getByTestId("earnings-active")).toBeTruthy();
    expect(screen.getByTestId("earnings-pending")).toBeTruthy();
  });
});

describe("IntradayView", () => {
  it("renders all four cards", () => {
    render(<IntradayView view={{}} />);
    expect(screen.getByTestId("intraday-mtc")).toBeTruthy();
    expect(screen.getByTestId("intraday-same-day-opens")).toBeTruthy();
    expect(screen.getByTestId("intraday-gamma-exposure")).toBeTruthy();
    expect(screen.getByTestId("intraday-realized-today")).toBeTruthy();
  });
});

describe("FamilyCheckpointView", () => {
  it("returns null when family_view absent", () => {
    const { container } = render(
      <FamilyCheckpointView portfolioSummary={{ generic: {} }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("returns null for unknown kind", () => {
    const { container } = render(
      <FamilyCheckpointView
        portfolioSummary={{ family_view: { kind: "unknown" } }}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("dispatches to OverlayView when kind=overlay", () => {
    render(
      <FamilyCheckpointView
        portfolioSummary={{ family_view: { kind: "overlay" } }}
      />,
    );
    expect(screen.getByTestId("overlay-view")).toBeTruthy();
  });

  it("dispatches to EarningsView when kind=earnings", () => {
    render(
      <FamilyCheckpointView
        portfolioSummary={{ family_view: { kind: "earnings" } }}
      />,
    );
    expect(screen.getByTestId("earnings-view")).toBeTruthy();
  });

  it("dispatches to IntradayView when kind=intraday", () => {
    render(
      <FamilyCheckpointView
        portfolioSummary={{ family_view: { kind: "intraday" } }}
      />,
    );
    expect(screen.getByTestId("intraday-view")).toBeTruthy();
  });
});
