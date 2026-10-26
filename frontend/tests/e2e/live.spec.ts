/**
 * /runs/:runId — when the run is in `pending`/`running` state, the page
 * auto-mounts the SSE indicator + event stream + live PnL panes.
 *
 * Full SSE streaming (open/connected/heartbeat/reconnect) is gated on a real
 * backend: Playwright's `page.route()` cannot easily emulate a long-lived
 * `text/event-stream` body. The `connected` transition test therefore skips
 * unless `E2E_REAL_BACKEND=1` is set and Plan B is running on :19720.
 */
import { test, expect } from "./fixtures/test";

test("live mode on /runs/:id: SSEIndicator + event stream pane render", async ({ page }) => {
  // run_beta fixture status === "running" → page enters live mode.
  await page.goto("/runs/run_beta");
  await expect(page.getByTestId("result-section-timeline")).toHaveAttribute("aria-current", "page");
  // Indicator is the status pill — always present in live mode.
  await expect(page.getByTestId("sse-indicator")).toBeVisible();
  // Either the empty state or the stream ul should be in the DOM.
  await expect(
    page.getByTestId("event-stream").or(page.getByTestId("event-stream-empty")),
  ).toBeVisible();
  // The unified PnL panel is available while the run is still active.
  await expect(page.getByTestId("pnl-chart")).toBeVisible();
  // SSE indicator near the run header is shown for live runs.
  await expect(page.getByTestId("sse-indicator")).toBeVisible();
});

test("live mode on /runs/:id: SSE transitions to open/connected", async ({ page }) => {
  test.skip(
    process.env.E2E_REAL_BACKEND !== "1",
    "TODO: requires Plan B FastAPI SSE pipe — set E2E_REAL_BACKEND=1 to run.",
  );
  await page.goto("/runs/run_beta");
  await expect(page.getByTestId("sse-indicator")).toHaveAttribute(
    "data-status",
    /open|connected/,
  );
});
