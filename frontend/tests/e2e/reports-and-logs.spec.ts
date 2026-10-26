import { test, expect } from "./fixtures/test";

test("Japanese results, returns and logs preserve original records", async ({ page, routing }) => {
  await page.addInitScript(() => localStorage.setItem("lo-locale", "ja"));
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: { id: "run_alpha", scenario: "custom_daily", status: "failed", kind: "batch", universe: ["SPY"], date_start: "2025-01-06", date_end: "2025-01-07" } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/logs", body: { available: true, text: "Traceback\nNameError: templates is not defined", error_message: "NameError: templates is not defined", exit_code: 1, start: 0, end: 100, total_bytes: 100, next_before: null } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/returns", body: { baseline_date: "2025-01-06", comparison_end: "2025-01-07", provisional: false, unavailable: [], warnings: [], series: ["SPY", "account"].map((id) => ({ id, label: id, kind: id === "account" ? "account" : "underlying", baseline_value: 100, points: [{ date: "2025-01-06", value: 100, return_pct: 0, index_value: 100 }, { date: "2025-01-07", value: 101, return_pct: 0.01, index_value: 101 }] })) } });
  await page.goto("/runs/run_alpha");
  await page.getByRole("tab", { name: "資産・口座のリターン" }).click();
  await expect(page.getByTestId("return-plot")).toHaveCount(1);
  await page.getByLabel("リターンの表示方法").selectOption("facets");
  await expect(page.getByTestId("return-plot")).toHaveCount(2);
  await expect(page.getByText("共通の基準日: 2025-01-06 終値 → 2025-01-07 · ET")).toBeVisible();
  await page.screenshot({ path: "/tmp/liveoption-ja-returns.png", fullPage: true });
  await page.getByRole("button", { name: "エラーログを表示" }).click();
  await expect(page.getByLabel("実行ログの内容")).toContainText("NameError: templates is not defined");
  await expect(page.getByRole("link", { name: "全ログをダウンロード" })).toBeVisible();
  await page.getByRole("button", { name: "結果・レポート", exact: true }).click();
  await expect(page.getByRole("heading", { name: "結果・レポート", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "エクスポート HTML レポート" })).toBeVisible();
});

test("results chart, pagination, report download and failed-run log entry", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: { id: "run_alpha", scenario: "custom_daily", status: "failed", kind: "batch", date_start: "2025-01-01", date_end: "2025-03-31", universe: ["SPY"], replay_frequency: "1m" } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/analysis", body: { metrics: { generic: {}, behavioral: {} }, paper_portfolio: { annualized_return: 0.12, max_drawdown_pct: 0.03, n_days: 45 } } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/report", body: { protocol_version: "liveoption.report.v1", background: { run_id: "run_alpha", status: "failed" }, settings: { configuration_source: "saved_effective_config", experiment: { universe: ["SPY"], start: "2025-01-01", end: "2025-03-31", frequency: "1m" }, model: { model_id: "test-model" }, decision_time_et: "10:30" }, results: { provisional: true, summary: { decisions: 50, trade_proposals: 20, fill_events: 8, total_pnl: 440, completed_days: 45 }, series: Array.from({ length: 45 }, (_, i) => ({ trading_day: new Date(Date.UTC(2025, 0, i + 1)).toISOString().slice(0, 10), portfolio_value: 100000 + i * 10, daily_pnl: 10, cum_pnl: i * 10, drawdown_pct: i % 5 ? -0.01 : 0, decisions: 1, trade_proposals: 0 })), activity: [{ trading_day: "2025-01-01", decisions: 50, trade_proposals: 20 }] } } });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/logs", body: { available: true, text: "Traceback\nNameError: templates is not defined", error_message: "NameError: templates is not defined", exit_code: 1, start: 0, end: 120, total_bytes: 120, next_before: null } });
  await page.goto("/runs/run_alpha?section=performance");
  await expect(page.getByTestId("result-visualizations")).toBeVisible();
  await page.getByRole("tab", { name: "Daily-equity drawdown" }).click();
  await expect(page.getByRole("group", { name: "Daily-equity drawdown" })).toBeVisible();
  await page.getByText("Daily results table (45)").click();
  await expect(page.locator("[data-testid=result-visualizations] tbody tr")).toHaveCount(20);
  await page.getByRole("button", { name: "Next page" }).click();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.locator("[data-testid=result-visualizations] tbody tr")).toHaveCount(5);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export HTML report" }).click();
  expect((await download).suggestedFilename()).toBe("run_alpha-report.html");
  await page.screenshot({ path: "/tmp/liveoption-results-report.png", fullPage: true });
  await page.getByRole("button", { name: "View error logs" }).click();
  await expect(page.getByTestId("run-logs")).toBeVisible();
  await expect(page.getByLabel("Worker log content")).toContainText("NameError");
  await expect(page.getByRole("link", { name: "Download full log" })).toBeVisible();
  await page.screenshot({ path: "/tmp/liveoption-failure-logs.png", fullPage: true });
});
