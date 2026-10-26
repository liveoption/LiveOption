/**
 * /runs/:runId — unified 4-panel layout for both live and terminal runs.
 * Replaces the dual-layout coverage previously in live.spec.ts.
 *
 * Live SSE streaming itself is hard to assert against page.route() (long-
 * lived event-stream bodies); this file relies on the testids and the
 * baseline DOM rendered by the React tree to verify structural parity.
 */
import { test, expect } from "./fixtures/test";

test.describe("run-detail unified layout", () => {
  test("running run shows all 4 panels with live event-stream chrome", async ({ page }) => {
    await page.goto("/runs/run_beta");
    await page.getByTestId("result-section-timeline").click();
    await expect(page.getByTestId("run-panels")).toBeVisible();
    await expect(page.getByTestId("timeline")).toBeVisible();
    await expect(page.getByTestId("inspector")).toBeVisible();
    await expect(page.getByTestId("pnl-chart")).toBeVisible();
    await expect(page.getByTestId("event-stream-panel")).toBeVisible();
    // Live indicator is rendered when run.status ∈ {pending, running}.
    await expect(page.getByTestId("event-stream-live-indicator")).toBeVisible();
    await expect(page.getByTestId("sse-indicator")).toBeVisible();
  });
});
