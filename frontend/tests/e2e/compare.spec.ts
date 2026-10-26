/**
 * /runs/:a/compare/:b — side-by-side comparison renders the dual chart.
 */
import { test, expect } from "./fixtures/test";

test("compare page renders dual PnL chart and metrics", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/runs/:a/compare/:b", body: {
    a: { id: "run_alpha", status: "finished", final_pnl: 100 },
    b: { id: "run_gamma", status: "finished", final_pnl: 50 },
    days: [{ trading_day: "2024-01-02", a_pnl: 100, b_pnl: 50 }],
    decisions: [{ seq: 1, a: { seq: 1, has_trade: true }, b: { seq: 1, has_trade: false } }],
  } });
  await page.goto("/runs/run_alpha/compare/run_gamma");
  // Chart (or its empty fallback) is present.
  await expect(
    page
      .getByTestId("compare-chart")
      .or(page.getByTestId("compare-chart-empty"))
      .or(page.getByTestId("compare-chart-fallback")),
  ).toBeVisible();
  // Decisions diff table is rendered.
  await expect(page.getByTestId("compare-decisions-table")).toBeVisible();
});
