import { test, expect } from "./fixtures/test";

const greeks = { delta_pnl: -105094.704, gamma_pnl: -1619.723, theta_pnl: 0.857, vega_pnl: -1.176, vanna_pnl: -604.06, volga_pnl: 104.748, residual: 384.059 };

test("strategy analysis shows signed attribution, exact values and decision activity", async ({ page, routing }) => {
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.addInitScript(() => localStorage.setItem("lo-locale", "zh"));
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: { id: "run_alpha", family: "overlay", status: "completed", kind: "batch", universe: ["QQQ"], scenario: "overlay", date_start: "2025-01-02", date_end: "2025-03-31" } });
  routing.use({ method: "GET", path: "/v1/runs/:runId/metrics", body: { generic: { total_pnl: 355250.2 }, family_specific: { overlay_income_total: 0, underlying_drift_pnl: 355250.2, greeks_attribution: greeks }, behavioral: { n_decisions: 100, n_with_trade: 20, intent_consistency: null, greeks_attribution_summary: greeks } } });
  await page.goto("/runs/run_alpha?section=strategy");
  await expect(page.getByTestId("overlay-net-pnl")).toContainText("$355,250.20");
  await expect(page.getByTestId("greeks-attribution")).toHaveCount(1);
  await expect(page.getByTestId("attribution-delta_pnl")).toContainText("-$105,094.70");
  await expect(page.getByTestId("decision-activity-summary")).toContainText("20.0%");
  await page.screenshot({ path: "/tmp/liveoption-strategy-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "查看小额分量", exact: true }).click();
  await expect(page.getByText(/当前为对称对数比例/)).toBeVisible();
  await expect(page.getByTestId("attribution-theta_pnl")).toHaveAttribute("data-value", "0.857");
  await page.getByTestId("attribution-theta_pnl").click();
  await expect(page.getByText("时间流逝对期权价值的影响。", { exact: true })).toBeVisible();
  await page.getByText("查看归因明细", { exact: true }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("table").getByText("-$105,094.704", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { localStorage.setItem("lo-theme", "dark"); window.dispatchEvent(new Event("lo-theme-change")); });
  await expect(page.locator("html")).toHaveClass(/dark/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "/tmp/liveoption-strategy-mobile-dark.png", fullPage: true });
});

test("missing family attribution falls back to saved summary without mixing sources", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: { id: "run_alpha", family: "overlay", status: "completed", kind: "batch" } });
  routing.use({ method: "GET", path: "/v1/runs/:runId/metrics", body: { generic: {}, family_specific: {}, behavioral: { greeks_attribution_summary: { delta_pnl: 0, theta_pnl: 12 } } } });
  await page.goto("/runs/run_alpha?section=strategy");
  await expect(page.getByTestId("attribution-delta_pnl")).toContainText("$0.00");
  await expect(page.getByTestId("attribution-gamma_pnl")).toContainText("Not recorded");
  await expect(page.getByTestId("greeks-attribution")).toContainText("Behavioral summary · 2/7 recorded");
});
