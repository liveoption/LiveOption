import { test, expect } from "./fixtures/test";

test.beforeEach(async ({ routing }) => {
  routing.use({ method: "GET", path: "/v1/runs/:runId/timeline", body: { items: [
    { id: "dec_0", seq: 0, trading_day: "2025-04-02", decision_ts: "2025-04-02T13:44:00Z", model_id: "gemini-3-flash", provider: "anthropic", has_trade: false },
    { id: "dec_1", seq: 1, trading_day: "2025-04-02", decision_ts: "2025-04-02T13:59:00Z", model_id: "gemini-3-flash", provider: "anthropic", has_trade: true },
  ] } });
  routing.use({ method: "GET", path: "/v1/decisions/:decisionId", body: { artifact_available: true, artifact: { prompt: { system: "You trade options.", user: "Cash: $100000" }, response: { raw: '{"reasoning":"Wait for better liquidity.","actions":[]}' }, trades_executed: [] } } });
});

test("selection, full record, prompt and response viewers form a working navigation path", async ({ page }) => {
  await page.goto("/runs/run_alpha?section=timeline");
  const rows = page.getByTestId("decision-row");
  await rows.first().click();
  await expect(page).toHaveURL(/section=timeline.*seq=0|seq=0.*section=timeline/);
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #0", exact: true })).toBeVisible();
  await expect(rows.first()).toHaveAttribute("data-active", "true");
  await rows.nth(1).click();
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #1", exact: true })).toBeVisible();
  await expect(rows.nth(1)).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("decision-inspector")).toHaveCount(1);
  await page.screenshot({ path: "/tmp/liveoption-timeline-selected.png", fullPage: true });
  await page.getByTestId("open-decision-record").click();
  await expect(page.getByTestId("result-section-history")).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: /^Actual prompt/ })).toBeVisible();
  await page.getByRole("button", { name: /^Actual prompt/ }).click();
  await expect(page.getByRole("dialog").getByTestId("prompt-pane-system")).toContainText("You trade options.");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /^Model response/ }).click();
  await expect(page.getByRole("dialog")).toContainText("Wait for better liquidity.");
  await page.keyboard.press("Escape");
  await page.goBack();
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #1", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #0", exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #1", exact: true })).toBeVisible();
});

test("query-only and legacy path links both render sequence zero and close correctly", async ({ page }) => {
  await page.goto("/runs/run_alpha?section=timeline&seq=0");
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #0", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #0", exact: true })).toBeVisible();
  await page.goto("/runs/run_alpha/decisions/0");
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #0", exact: true })).toBeVisible();
  await expect(page.getByTestId("decision-row").first()).toHaveAttribute("data-active", "true");
  await page.getByRole("button", { name: "Close inspector" }).click();
  await expect(page).toHaveURL(/\?section=timeline$/);
  await expect(page.getByTestId("decision-inspector-empty")).toBeVisible();
  await page.goto("/runs/run_alpha?section=timeline&seq=999");
  await expect(page.getByTestId("inspector")).toContainText("Decision #999 was not found");
});

test("mobile selection reveals the inspector and rows stay inside the timeline", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runs/run_alpha?section=timeline");
  await page.getByTestId("decision-row").first().click();
  await expect(page.getByTestId("inspector").getByRole("heading", { name: "Decision #0", exact: true })).toBeInViewport();
  expect(await page.getByTestId("timeline").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByTestId("open-decision-record").click();
  await expect(page.getByRole("button", { name: /^Actual prompt/ })).toBeVisible();
});
