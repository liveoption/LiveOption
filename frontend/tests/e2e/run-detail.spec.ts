/**
 * /runs/:runId — 3-pane loads, clicking a timeline row opens the decision
 * drawer (nested route `/runs/:id/decisions/:seq`).
 */
import { test, expect } from "./fixtures/test";

test("run detail renders 3-pane layout and timeline rows", async ({ page }) => {
  await page.goto("/runs/run_alpha");
  await page.getByTestId("result-section-timeline").click();
  // Timeline pane
  await expect(page.getByTestId("decision-timeline")).toBeVisible();
  // PnL chart pane
  await expect(page.getByTestId("pnl-chart")).toBeVisible();
  // At least one decision row from the fixture
  const rows = page.getByTestId("decision-row");
  await expect(rows.first()).toBeVisible();
});

test("clicking a decision row selects and renders the matching inspector", async ({ page }) => {
  await page.goto("/runs/run_alpha");
  await page.getByTestId("result-section-timeline").click();
  const rows = page.getByTestId("decision-row");
  await rows.first().click();
  // URL carries ?seq=N
  await expect(page).toHaveURL(/seq=1/);
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #1", exact: true })).toBeVisible();
  await expect(rows.first()).toHaveAttribute("data-active", "true");
});

test("a copied decision link opens its detail directly", async ({ page }) => {
  await page.goto("/runs/run_alpha/decisions/1");
  await expect(page.getByTestId("result-section-timeline")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #1", exact: true })).toBeVisible();
  await page.getByTestId("result-section-history").click();
  await expect(page).toHaveURL(/\/runs\/run_alpha\?section=history$/);
  await expect(page.getByTestId("result-section-history")).toHaveAttribute("aria-current", "page");
});

test("a result section link opens the selected panel", async ({ page }) => {
  await page.goto("/runs/run_alpha?section=history");
  await expect(page.getByTestId("result-section-history")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("agent-history")).toBeVisible();
});

test("chart checkpoint opens the matching agent record", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: {
    id: "run_alpha", scenario: "intraday_spy_2025_q1", status: "completed",
    kind: "batch", date_start: "2024-01-02", date_end: "2024-01-02",
    universe: ["SPY"], family: "intraday",
  } });
  routing.use({ method: "GET", path: "/v1/runs/:runId/timeline", body: { items: [{
    id: "dec_1", run_id: "run_alpha", seq: 1, trading_day: "2024-01-02",
    decision_ts: "2024-01-02T14:45:00Z", has_trade: true, model_id: "test-model",
  }] } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/chart", body: {
    symbol: "SPY", resolution: "1m", source: "qfinzero.upq", bars: [
      { time: "2024-01-02T14:30:00Z", open: 100, high: 101, low: 99, close: 100, volume: 100 },
      { time: "2024-01-02T14:45:00Z", open: 101, high: 102, low: 100, close: 101, volume: 200 },
    ],
  } });
  routing.use({ method: "GET", path: "/v1/decisions/:decisionId", body: {
    artifact_available: true, artifact: { prompt: { system: "test prompt" },
      response: { raw: "[]" }, trades_executed: [] },
  } });
  await page.goto("/runs/run_alpha");
  await page.getByRole("button", { name: /Checkpoint #1/ }).click();
  await expect(page.getByTestId("result-section-history")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#history-decision-1")).toBeVisible();
  await expect(page.getByText("Actual prompt", { exact: true })).toBeVisible();
});

test("researcher can inspect extended-hours decisions and filter the history", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: {
    id: "run_alpha", status: "completed", date_start: "2024-01-02", date_end: "2024-01-02",
    universe: ["SPY"], family: "intraday",
  } });
  routing.use({ method: "GET", path: "/v1/runs/:runId/timeline", body: { items: [
    { id: "dec_1", seq: 1, trading_day: "2024-01-02", decision_ts: "2024-01-02T13:00:00Z", has_trade: false, model_id: "m1" },
    { id: "dec_2", seq: 2, trading_day: "2024-01-02", decision_ts: "2024-01-02T14:45:00Z", has_trade: true, model_id: "m2" },
  ] } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/chart", body: {
    symbol: "SPY", resolution: "1m", source: "qfinzero.upq", bars: [
      { time: "2024-01-02T13:00:00Z", open: 99, high: 100, low: 98, close: 99, volume: 10 },
      { time: "2024-01-02T14:45:00Z", open: 100, high: 101, low: 99, close: 100, volume: 20 },
    ],
  } });
  routing.use({ method: "GET", path: "/v1/decisions/:decisionId", body: {
    artifact_available: true, artifact: { response: { analysis: { reasoning: "Liquidity improved after the open." } }, prompt: { system: "test" } },
  } });
  await page.goto("/runs/run_alpha");
  await expect(page.getByText(/1\/2 decisions aligned/)).toBeVisible();
  await page.getByLabel("Show extended hours").check();
  await expect(page.getByText(/2\/2 decisions aligned/)).toBeVisible();
  await page.getByLabel("Jump to decision").selectOption("2");
  await expect(page.getByTestId("result-section-history")).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("Liquidity improved after the open.", { exact: true })).toBeVisible();
  await page.getByRole("searchbox").fill("m2");
  await expect(page.locator("[id^='history-decision-']")).toHaveCount(1);
});
