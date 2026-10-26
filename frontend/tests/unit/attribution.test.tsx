import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { attributionPosition, attributionSummary, greekValues } from "../../app/lib/attribution";
import GreeksAttribution from "../../app/components/run-dashboard/GreeksAttribution";
import BehavioralPanel from "../../app/components/run-dashboard/BehavioralPanel";
import OverlayDashboard from "../../app/components/run-dashboard/OverlayDashboard";

describe("attribution visualizations", () => {
  it("keeps zero, missing and nonfinite observations distinct", () => {
    const items = greekValues({ delta_pnl: 0, gamma_pnl: NaN, theta_pnl: Infinity, vega_pnl: "12", residual: null });
    expect(items[0].value).toBe(0); expect(items.slice(1).every(item => item.value === null)).toBe(true);
    expect(attributionSummary(null)).toMatchObject({ measured: 0, net: null });
    expect(attributionSummary({ delta_pnl: -100, theta_pnl: 4, residual: 2 })).toMatchObject({ measured: 3, positive: 6, negative: -100, net: -94 });
    expect(attributionSummary({ delta_pnl: 0 })).toMatchObject({ measured: 1, net: 0 });
  });
  it("uses symmetric linear and explicitly nonlinear scales without inventing amounts", () => {
    expect(attributionPosition(-100, 100)).toBe(-1);
    expect(attributionPosition(50, 100)).toBe(0.5);
    expect(attributionPosition(0, 0, true)).toBe(0);
    expect(attributionPosition(-10, 100, true)).toBe(-attributionPosition(10, 100, true));
    expect(attributionPosition(1, 100000, true)).toBeGreaterThan(attributionPosition(1, 100000));
    expect(attributionPosition(Infinity, 1)).toBe(0);
  });
  it("renders signed values, selectable explanations, scale and an exact table", () => {
    render(<GreeksAttribution attribution={{ delta_pnl: -105094.704, theta_pnl: 0.857, residual: 0 }} source="family" />);
    expect(screen.getByTestId("attribution-delta_pnl").textContent).toContain("-$105,094.70");
    expect(screen.getByTestId("attribution-gamma_pnl").textContent).toContain("Not recorded");
    expect(screen.getByTestId("attribution-residual").textContent).toContain("$0.00");
    fireEvent.click(screen.getByTestId("attribution-theta_pnl"));
    expect(screen.getByText("Effect of time passing on option value.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Inspect small components" }));
    expect(screen.getByText(/Symmetric log scale/)).toBeTruthy();
    expect(screen.getByTestId("attribution-theta_pnl").getAttribute("data-value")).toBe("0.857");
    expect(screen.getByRole("table", { hidden: true })).toBeTruthy();
    expect(screen.getByText(/3\/7 recorded/)).toBeTruthy();
  });
  it("shows absence honestly and does not blend premium with Greeks", () => {
    render(<GreeksAttribution attribution={null} source="behavioral" />);
    expect(screen.getByText(/No measured Greeks attribution/)).toBeTruthy();
    expect(screen.queryByTestId("attribution-delta_pnl")).toBeNull();
    render(<OverlayDashboard generic={{ total_pnl: 100 }} familySpecific={{ overlay_income_total: 20, underlying_drift_pnl: 80 }} />);
    expect(screen.getByTestId("overlay-net-pnl").textContent).toContain("$100.00");
    expect(screen.getByTestId("overlay-income").textContent).toContain("not net option profit");
  });
  it("charts decisions with proposals without implying fills or fabricating counts", () => {
    const { rerender } = render(<BehavioralPanel behavioral={{ n_decisions: 100, n_with_trade: 20 }} />);
    expect(screen.getByTestId("decision-activity-summary").textContent).toContain("20.0%");
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("20 trade-proposing decisions; 80 without a proposal");
    rerender(<BehavioralPanel behavioral={{ n_decisions: 0, n_with_trade: 0 }} />);
    expect(screen.getByTestId("decision-activity-summary").textContent).not.toMatch(/NaN|Infinity/);
    rerender(<BehavioralPanel behavioral={{ n_decisions: 1, n_with_trade: 2 }} />);
    expect(screen.queryByTestId("decision-activity-summary")).toBeNull();
  });
});
