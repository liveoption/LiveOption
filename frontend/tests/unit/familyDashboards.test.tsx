/**
 * Tests for family-specific run dashboards (Phase D-full-3).
 *
 * Dashboards are pure functions of `familySpecific` / `behavioral` so we
 * exercise empty / partial / full-data renders without any network. The
 * FamilyDashboard switch is covered separately via mock route.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import OverlayDashboard from "../../app/components/run-dashboard/OverlayDashboard";
import EarningsDashboard from "../../app/components/run-dashboard/EarningsDashboard";
import IntradayDashboard from "../../app/components/run-dashboard/IntradayDashboard";
import BehavioralPanel from "../../app/components/run-dashboard/BehavioralPanel";

describe("OverlayDashboard", () => {
  it("renders with empty data without crashing", () => {
    render(<OverlayDashboard familySpecific={null} />);
    expect(screen.getByTestId("overlay-dashboard")).toBeTruthy();
    expect(screen.getByTestId("overlay-income")).toBeTruthy();
  });

  it("renders populated values", () => {
    render(
      <OverlayDashboard
        familySpecific={{
          overlay_income: 1234.5,
          underlying_drift: -200,
          theta_capture: 50,
        }}
      />,
    );
    expect(screen.getByTestId("overlay-income").textContent).toMatch(/1,234/);
  });

  it("reads evaluator output fields", () => {
    render(<OverlayDashboard familySpecific={{ overlay_income_total: 425, underlying_drift_pnl: -12, greeks_attribution: { theta_pnl: 31 } }} />);
    expect(screen.getByTestId("overlay-income").textContent).toMatch(/425/);
    expect(screen.getByTestId("overlay-drift").textContent).toMatch(/-\$12/);
    expect(screen.getByTestId("overlay-drift").textContent).toContain("Not a separately measured stock return");
  });
});

describe("EarningsDashboard", () => {
  it("renders without per-event/per-ticker tables when empty", () => {
    render(<EarningsDashboard familySpecific={{}} />);
    expect(screen.getByTestId("earnings-dashboard")).toBeTruthy();
    expect(screen.queryByTestId("earnings-waterfall")).toBeNull();
    expect(screen.queryByTestId("earnings-per-ticker")).toBeNull();
  });

  it("renders waterfall + per-ticker tables when provided", () => {
    render(
      <EarningsDashboard
        familySpecific={{
          event_waterfall: [
            { ticker: "AAPL", entry: 100, at_event: 110, exit: 105, pnl: 5 },
          ],
          per_ticker: [{ ticker: "AAPL", win_rate: 0.6 }],
        }}
      />,
    );
    expect(screen.getByTestId("earnings-waterfall")).toBeTruthy();
    expect(screen.getByTestId("earnings-per-ticker")).toBeTruthy();
  });

  it("reads evaluator output fields", () => {
    render(<EarningsDashboard familySpecific={{ vega_pnl_total: 48, per_ticker_notional: { AAPL: 100 }, greeks_attribution: { delta_pnl: 19 } }} />);
    expect(screen.getByTestId("earnings-vega").textContent).toMatch(/48/);
    expect(screen.getByTestId("earnings-per-ticker").textContent).toMatch(/AAPL/);
  });
});

describe("IntradayDashboard", () => {
  it("renders without histogram when missing", () => {
    render(<IntradayDashboard familySpecific={{}} />);
    expect(screen.getByTestId("intraday-dashboard")).toBeTruthy();
    expect(screen.queryByTestId("intraday-histogram")).toBeNull();
  });

  it("renders histogram bars when provided", () => {
    render(
      <IntradayDashboard
        familySpecific={{
          hold_time_histogram: [
            { bucket: "0-5m", count: 4 },
            { bucket: "5-15m", count: 1 },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("intraday-histogram")).toBeTruthy();
  });

  it("reads evaluator output fields", () => {
    render(<IntradayDashboard familySpecific={{ trades_per_day: 2, gamma_pnl_total: 13, greeks_attribution: { delta_pnl: 7 } }} />);
    expect(screen.getByTestId("intraday-efficiency").textContent).toMatch(/2/);
    expect(screen.getByTestId("intraday-hold-avg").textContent).toMatch(/13/);
    expect(screen.getByTestId("intraday-gamma").textContent).toMatch(/7/);
  });
});

describe("BehavioralPanel", () => {
  it("omits unmeasured placeholders", () => {
    const { container } = render(<BehavioralPanel behavioral={null} />);
    expect(screen.getByTestId("behavioral-panel")).toBeTruthy();
    const cards = container.querySelectorAll(
      "[data-testid='behavioral-panel'] .grid > *",
    );
    expect(cards.length).toBe(0);
    expect(container.textContent).toMatch(/No measured behavioral metrics/);
  });

  it("keeps Greeks attribution out of the behavioral panel", () => {
    render(
      <BehavioralPanel
        behavioral={{ greeks_attribution_summary: { delta: 0.1, gamma: 0.02 } }}
      />,
    );
    expect(screen.queryByTestId("greeks-attribution")).toBeNull();
  });
});
