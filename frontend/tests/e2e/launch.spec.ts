import { test, expect } from "./fixtures/test";
import type { Routing } from "./fixtures/routing";

const preset = {
  id: "intraday_spy_2025_q1", name: "Intraday", description: "Intraday options", family: "intraday",
  experiment: { start: "2025-01-06", end: "2025-01-10", initial_cash: 100000, universe: ["SPY"] },
  strategy_params: { decision_interval_minutes: 15, dte_min: 0, dte_max: 7, max_positions: 5 },
  risk: { max_daily_loss_pct: 0.02, flatten_before_close: true }, data_policy: { news_enabled: false, news_lookback_days: 7 },
  avsr: { moneyness_anchors: [0.95, 1, 1.05], expiry_anchors: [1, 7, 30], distance_lambda: 0.01 },
  prompt_templates: { system: "Trade options on {universe}. Return only an order JSON array or [] to hold.", user: "Account cash: {cash}\nAvailable options: {chain_sample_table}" },
  prompt_variables: { system: [{ name: "universe", description: "标的", description_en: "Underlyings", source: "任务设置", example: "SPY" }], user: [{ name: "cash", description: "可用现金", description_en: "Available cash", source: "账户", example: "100000" }, { name: "chain_sample_table", description: "候选期权", description_en: "Candidate options", source: "期权链", example: "SPY call" }] },
  context: "", execution: { option_fee_per_contract: 0.65, stock_fee_per_share: 0.0005, slippage_bps: 1, option_spread_pct: 0.05, order_type_policy: "strategy" },
};
function setup(routing: Routing) {
  routing.use({ method: "GET", path: "/v1/me", body: { can_launch: true } });
  routing.use({ method: "GET", path: "/v1/tasks/presets", body: { items: [preset, { ...preset, id: "overlay", family: "overlay" }, { ...preset, id: "earnings", family: "earnings" }], custom_types: [
    { ...preset, id: "custom_intraday", cadence: "intraday" },
    { ...preset, id: "custom_daily", cadence: "daily", family: "daily", strategy_params: { dte_min: 7, dte_max: 60, max_positions: 5 }, risk: { max_daily_loss_pct: 0.02, flatten_before_close: false } },
  ], supported_underlyings: ["SPY", "QQQ", "NVDA"] } });
  routing.use({ method: "GET", path: "/v1/model-settings", body: { models: [{ id: "m1", label: "Model 1", group_id: "g1" }], groups: [{ id: "g1", label: "Group 1", has_api_key: true }] } });
  routing.use({ method: "POST", path: "/v1/tasks", body: { run_id: "run_launch_42" } });
}

for (const [mode, locale, label] of [["hedge", "zh", "对冲"], ["covered_call", "en", "Covered Call"], ["hybrid", "ja", "Hybrid"]] as const) {
  test(`overlay ${mode} selects, preserves and submits the submode (${locale})`, async ({ page, routing }) => {
    setup(routing);
    await page.addInitScript((language) => localStorage.setItem("lo-locale", language), locale);
    let submitted: Record<string, unknown> | null = null;
    page.on("request", (request) => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/tasks") submitted = request.postDataJSON(); });
    await page.goto("/runs/new");
    const overlay = locale === "zh" ? "覆盖" : locale === "ja" ? "カバード戦略" : "Overlay";
    const intraday = locale === "zh" ? "日内" : locale === "ja" ? "日中" : "Intraday";
    const next = locale === "zh" ? "下一步" : locale === "ja" ? "次へ" : "Continue";
    await page.getByRole("button", { name: overlay, exact: true }).click();
    await expect(page.locator('input[value="covered_call"]')).toBeChecked();
    await page.getByRole("radio", { name: new RegExp(`^${label}`) }).check();
    await page.getByRole("button", { name: intraday, exact: true }).click();
    await expect(page.getByTestId("overlay-modes")).toHaveCount(0);
    await page.getByRole("button", { name: overlay, exact: true }).click();
    await expect(page.locator(`input[value="${mode}"]`)).toBeChecked();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (mode === "hybrid") await page.screenshot({ path: "/tmp/liveoption-overlay-submodes.png", fullPage: true });
    await page.getByRole("button", { name: next, exact: true }).click();
    await page.getByRole("button", { name: next, exact: true }).click();
    await expect(page.getByTestId("overlay-prompt-rules")).toContainText(label);
    await page.getByRole("button", { name: next, exact: true }).click();
    await expect(page.getByText(`${overlay} · ${label}`, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: locale === "zh" ? "启动测试" : locale === "ja" ? "テストを開始" : "Start run", exact: true }).click();
    await expect(page).toHaveURL(/\/runs\/run_launch_42$/);
    expect(submitted).toMatchObject({ preset_id: "overlay", overlay_mode: mode });
  });
}

test("Japanese setup preserves drafts, prompts and language across navigation", async ({ page, routing }) => {
  setup(routing);
  const hydrationErrors: string[] = [];
  page.on("console", (message) => { if (/hydration|hydrated|didn't match/i.test(message.text())) hydrationErrors.push(message.text()); });
  page.on("pageerror", (error) => { if (/hydration|hydrated|React error #418/i.test(error.message)) hydrationErrors.push(error.message); });
  await page.goto("/runs/new");
  await page.getByTestId("language-selector").selectOption("ja");
  await expect(page.getByRole("heading", { name: "オプションテストを作成" })).toBeVisible();
  await page.getByRole("tab", { name: "カスタムタスク" }).click();
  await page.getByRole("button", { name: "日次", exact: true }).click();
  await page.getByLabel("テスト名（任意）").fill("日本語で設定したテスト");
  await page.getByRole("button", { name: "次へ", exact: true }).click();
  await page.getByLabel("日次の判断時刻（米国東部時間）").fill("10:30");
  await page.getByTestId("language-selector").selectOption("en");
  await expect(page.getByLabel("Daily decision time (ET)")).toHaveValue("10:30");
  await page.getByTestId("language-selector").selectOption("ja");
  await page.getByRole("button", { name: "次へ", exact: true }).click();
  await expect(page.getByLabel("system prompt")).toHaveValue(preset.prompt_templates.system);
  await expect(page.getByRole("button", { name: "System・システム指示", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "戻る", exact: true }).click();
  await page.getByRole("button", { name: "戻る", exact: true }).click();
  await expect(page.getByLabel("テスト名（任意）")).toHaveValue("日本語で設定したテスト");
  await page.reload();
  await expect(page.getByTestId("language-selector")).toHaveValue("ja");
  await expect(page.locator("html")).toHaveAttribute("lang", "ja");
  await page.getByRole("link", { name: "API設定", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "API・モデル設定" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/liveoption-ja-settings.png", fullPage: true });
  expect(hydrationErrors).toEqual([]);
});

test("preset launch shows one step at a time and reviews before starting", async ({ page, routing }) => {
  setup(routing);
  let submitted: Record<string, unknown> | null = null;
  page.on("request", (request) => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/tasks") submitted = request.postDataJSON(); });
  await page.goto("/runs/new");
  await expect(page.getByRole("button", { name: "Intraday", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Overlay", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start run" })).toHaveCount(0);
  await expect(page.getByTestId("trading-settings")).toBeHidden();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByTestId("trading-settings")).toBeVisible();
  await page.getByLabel("Option fee / contract · USD").fill("1.25");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel("system prompt")).toHaveJSProperty("tagName", "PRE");
  await page.getByLabel("Trading context").fill("Hold cash when spreads widen.");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("$1.25 / contract", { exact: true })).toBeVisible();
  expect(submitted).toBeNull();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page).toHaveURL(/\/runs\/run_launch_42$/);
  expect(submitted).toMatchObject({ preset_id: preset.id, model_id: "m1", context: "Hold cash when spreads widen." });
});

test("launch discovers a group catalog and submits the newly selected model", async ({ page, routing }) => {
  setup(routing);
  const groups = [{ id: "g1", label: "Live gateway", has_api_key: true, models_updated_at: null }];
  const initial = [{ id: "m1", label: "Model 1", group_id: "g1", api_model: "model-1" }];
  routing.use({ method: "GET", path: "/v1/model-settings", body: { groups, models: initial } });
  routing.use({ method: "POST", path: "/v1/model-settings/:groupId/models/refresh", body: { groups: groups.map((g) => ({ ...g, models_updated_at: "2026-10-22T00:00:00Z" })), models: [...initial, { id: "discovered", label: "Discovered model", api_model: "discovered-model", group_id: "g1" }] } });
  let submitted: Record<string, unknown> | null = null;
  page.on("request", (request) => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/tasks") submitted = request.postDataJSON(); });
  await page.goto("/runs/new");
  await expect(page.getByRole("option", { name: /Discovered model/ })).toHaveCount(1);
  await expect(page.getByLabel("Run model")).toBeEnabled();
  await page.getByLabel("Run model").selectOption("discovered");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Discovered model · Live gateway", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page).toHaveURL(/\/runs\/run_launch_42$/);
  expect(submitted).toMatchObject({ model_id: "discovered" });
});

test("daily custom task has independent settings and no preset selection", async ({ page, routing }) => {
  setup(routing);
  let submitted: Record<string, unknown> | null = null;
  page.on("request", (request) => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/tasks") submitted = request.postDataJSON(); });
  await page.goto("/runs/new");
  await page.getByRole("tab", { name: "Custom task" }).click();
  await page.getByRole("button", { name: "Daily", exact: true }).click();
  await expect(page.getByText("Trading engine", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Overlay", exact: true })).toHaveCount(0);
  await page.getByLabel("Underlyings (comma-separated)").fill("QQQ");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel("Decision interval (min)")).toHaveCount(0);
  await expect(page.getByText(/Choose a daily decision time/)).toBeVisible();
  await page.getByLabel("Daily decision time (ET)").fill("10:30");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("system prompt").fill("My custom daily option rules.");
  await page.getByRole("button", { name: "User", exact: true }).click();
  await expect(page.getByRole("code").filter({ hasText: "{chain_sample_table}" })).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  expect(submitted).toBeNull();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page).toHaveURL(/\/runs\/run_launch_42$/);
  expect(submitted).toMatchObject({ mode: "custom", cadence: "daily", decision_time_et: "10:30", experiment: { universe: ["QQQ"] } });
  expect(submitted).not.toHaveProperty("preset_id");
});

test("advanced visual examples follow settings and fit mobile", async ({ page, routing }) => {
  setup(routing);
  await page.addInitScript(() => localStorage.setItem("lo-locale", "zh"));
  await page.goto("/runs/new");
  await page.getByRole("tab", { name: "自定义任务" }).click();
  await page.getByRole("button", { name: "日频", exact: true }).click();
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByLabel("每日决策时间（ET）").fill("10:30");
  await page.getByText("高级设置 · 风控、行情与 AVSR", { exact: true }).click();
  await page.getByText("AVSR · 期权链摘要", { exact: true }).click();
  await expect(page.getByTestId("avsr-example")).toBeVisible();
  await page.getByLabel("价内程度锚点 1", { exact: true }).fill("90");
  await expect(page.getByTestId("avsr-example")).toContainText("K/S 90.0%");
  await page.getByText("行情与期权链数据", { exact: true }).click();
  await expect(page.getByTestId("data-example")).toBeVisible();
  await page.screenshot({ path: "/tmp/liveoption-settings-examples.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("Chinese flow preserves drafts and reports invalid prompts before review", async ({ page, routing }) => {
  await page.addInitScript(() => localStorage.setItem("lo-locale", "zh")); setup(routing);
  await page.goto("/runs/new");
  await page.getByRole("tab", { name: "自定义任务" }).click();
  await page.getByLabel("名称（可选）").fill("我的日内测试");
  await page.getByRole("button", { name: "日频", exact: true }).click();
  await expect(page.getByLabel("名称（可选）")).toHaveValue("");
  await page.getByRole("button", { name: "日内", exact: true }).click();
  await expect(page.getByLabel("名称（可选）")).toHaveValue("我的日内测试");
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByRole("button", { name: "User · 决策输入", exact: true }).click();
  await page.getByLabel("user prompt").fill("使用 {unsupported}");
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page.getByRole("button", { name: "启动测试" })).toHaveCount(0);
  await expect(page.getByText("不支持的变量: {unsupported}", { exact: true })).toBeVisible();
});

test("mobile setup and variable panel fit the viewport", async ({ page, routing }) => {
  setup(routing); await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runs/new"); await page.getByRole("tab", { name: "Custom task" }).click();
  await page.getByRole("button", { name: "Continue" }).click(); await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByLabel("system prompt")).toBeEditable();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("launch permission is explained before showing setup", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/me", body: { can_launch: false } });
  await page.goto("/runs/new"); await expect(page.getByTestId("runs-new-denied")).toBeVisible();
  await expect(page.getByTestId("run-spec-editor")).toHaveCount(0);
});

test("concurrency errors remain visible on the review step", async ({ page, routing }) => {
  setup(routing); routing.use({ method: "POST", path: "/v1/tasks", status: 429, body: { detail: "internal concurrency error" } });
  await page.goto("/runs/new");
  for (let index = 0; index < 3; index++) await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("alert")).toHaveText("Too many runs are active. Try again later.");
});
