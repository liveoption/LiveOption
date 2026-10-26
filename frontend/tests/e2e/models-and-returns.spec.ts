import { test, expect } from "./fixtures/test";

test("API group fetches all models and paginates its searchable catalog", async ({ page, routing }) => {
  const groups = [{ id: "gateway", label: "My gateway", provider: "openai_compatible", base_url: "https://api.example.invalid/v1", has_api_key: true }];
  const models = Array.from({ length: 48 }, (_, i) => ({ id: `m${i}`, label: `Model ${i}`, api_model: `model-${i}`, group_id: "gateway" }));
  routing.use({ method: "GET", path: "/v1/model-settings", body: { groups, models: [] } });
  routing.use({ method: "PUT", path: "/v1/model-settings", body: { groups, models: [] } });
  routing.use({ method: "POST", path: "/v1/model-settings/:groupId/models/refresh", body: { groups: groups.map((g) => ({ ...g, models_updated_at: "2026-10-22T00:00:00Z" })), models } });
  await page.goto("/settings/models");
  await page.getByRole("button", { name: "Save and fetch models /models" }).click();
  const group = page.getByTestId("model-group-gateway");
  await expect(group.getByText("Models in this group · 48")).toBeVisible();
  await expect(group.locator("li")).toHaveCount(20);
  await group.getByRole("button", { name: "Next", exact: true }).click();
  await expect(group.getByText("model-20", { exact: true })).toBeVisible();
  await group.getByLabel("Search group models").fill("model-47");
  await expect(group.locator("li")).toHaveCount(1);
  await page.screenshot({ path: "/tmp/liveoption-model-discovery.png", fullPage: true });
});

test("multiple assets support candle panels, overlay returns, indexed values and mobile", async ({ page, routing }) => {
  await page.addInitScript(() => localStorage.setItem("lo-locale", "zh"));
  routing.use({ method: "GET", path: "/v1/runs/:runId", body: { id: "run_alpha", scenario: "overlay_spy_qqq_2025_q1", status: "completed", kind: "batch", date_start: "2025-01-06", date_end: "2025-01-10", universe: ["QQQ", "NVDA"], family: "overlay" } });
  const dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"];
  routing.use({ method: "GET", path: "/v1/tasks/:runId/chart", body: (_, url) => ({ symbol: url.searchParams.get("symbol"), resolution: "1d", source: "qfinzero.upq", bars: dates.map((time, i) => ({ time, open: 100 + i, high: 102 + i, low: 99 + i, close: 101 + i, volume: 1000 })) }) });
  routing.use({ method: "GET", path: "/v1/tasks/:runId/returns", body: (_, url) => ({ protocol_version: "liveoption.returns.v1", baseline_date: dates[0], comparison_end: dates[4], provisional: false, unavailable: [], warnings: [], series: [...(url.searchParams.get("symbols") ?? "").split(",").filter(Boolean), ...(url.searchParams.get("account") === "true" ? ["account"] : [])].map((id, index) => ({ id, label: id, kind: id === "account" ? "account" : "underlying", baseline_value: 100, points: dates.map((date, i) => ({ date, value: 100 + i * (index + 1), return_pct: i * (index + 1) * 0.01, index_value: 100 + i * (index + 1) })) })) }) });
  await page.goto("/runs/run_alpha");
  await page.getByLabel("显示方式", { exact: true }).selectOption("facets");
  await expect(page.getByTestId("kline-chart")).toHaveCount(2);
  await page.getByRole("tab", { name: "标的与账户收益率" }).click();
  await expect(page.getByText("共同基准: 2025-01-06 收盘 → 2025-01-10 · ET")).toBeVisible();
  await expect(page.getByTestId("return-plot")).toHaveCount(1);
  await page.screenshot({ path: "/tmp/liveoption-return-overlay.png", fullPage: true });
  await page.getByLabel("收益图显示方式").selectOption("facets");
  await expect(page.getByTestId("return-plot")).toHaveCount(3);
  await page.getByLabel("纵轴", { exact: true }).selectOption("index_value");
  await expect(page.getByTestId("return-plot").first()).toContainText("104.00");
  await page.screenshot({ path: "/tmp/liveoption-return-facets.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByLabel("NVDA", { exact: true }).uncheck();
  await expect(page.getByTestId("return-plot")).toHaveCount(2);
});
