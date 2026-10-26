import { test, expect } from "./fixtures/test";
import { readFile } from "node:fs/promises";

const system = "## Role\nYou trade SPY options.";
const chain = "| Contract | Ask |\n|---|---|\n| O:SPY250117C00590000 | 2.10 |";
const user = `💹 ## Account\nCash: $1,250.00\n\n## Candidates\n${chain}\n<script>window.promptInjected=true</script>`;
const span = (text: string, value: string, variable: string) => {
  const start = Array.from(text.slice(0, text.indexOf(value))).length;
  return { start, end: start + Array.from(value).length, variable, format: "" };
};
const prompt = { calls: [
  { phase: "analyzer", system, user, _provenance: { version: 1, offset_encoding: "unicode-codepoints", system: [span(system, "SPY", "universe")], user: [span(user, "1,250.00", "cash"), span(user, chain, "chain_sample_table")] } },
  { phase: "executor", system: "Choose **one** action.", user: "Original legacy executor text." },
] };

test("prompt dialog separates roles, renders fields safely and downloads exact raw JSON", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/decisions/:decisionId", body: { artifact_available: true, artifact: { prompt, response: { raw: "[]" } } } });
  await page.goto("/runs/run_alpha?section=history&seq=1");
  const opener = page.getByRole("button", { name: /^Actual prompt/ });
  await opener.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "System", exact: true })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "User", exact: true })).toBeVisible();
  await expect(dialog.locator('[data-variable="cash"]')).toContainText("1,250.00");
  await expect(dialog.locator("table")).toBeVisible();
  await page.screenshot({ path: "/tmp/liveoption-prompt-viewer-desktop.png", fullPage: true });
  await expect(dialog.locator("script")).toHaveCount(0);
  expect(await page.evaluate(() => (window as Window & { promptInjected?: boolean }).promptInjected)).toBeUndefined();
  await dialog.getByRole("tab", { name: "Field data" }).click();
  await expect(dialog.getByText("{cash}", { exact: true })).toBeVisible();
  await expect(dialog.getByText("1,250.00", { exact: true })).toBeVisible();
  await dialog.getByRole("tab", { name: "Raw JSON", exact: true }).click();
  expect(JSON.parse(await dialog.getByTestId("prompt-raw-json").innerText())).toEqual(prompt);
  const downloading = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Download raw JSON" }).click();
  const download = await downloading;
  expect(JSON.parse(await readFile((await download.path())!, "utf8"))).toEqual(prompt);
  await dialog.getByRole("tab", { name: "Readable prompt" }).click();
  await dialog.getByLabel("Call phase").selectOption("1");
  await expect(dialog.getByTestId("prompt-pane-user")).toContainText("Original legacy executor text.");
  await expect(dialog.getByTestId("prompt-pane-user")).toContainText("Variable locations were not saved");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await opener.click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("prompt-pane-user")).toBeVisible();
  await expect(page.getByTestId("prompt-pane-system")).toBeHidden();
  await page.getByRole("button", { name: "System", exact: true }).click();
  await expect(page.getByTestId("prompt-pane-system")).toBeVisible();
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/liveoption-prompt-viewer-mobile.png", fullPage: true });
});

test("template variables stay highlighted while editing, inserting and viewing defaults", async ({ page, routing }) => {
  const preset = { id: "intraday", family: "intraday", name: "Intraday", description: "Test", context: "", experiment: { start: "2025-01-06", end: "2025-01-10", initial_cash: 100000, universe: ["SPY"] }, strategy_params: { decision_interval_minutes: 15, dte_min: 0, dte_max: 7 }, risk: {}, data_policy: {}, avsr: { moneyness_anchors: [0.95, 1, 1.05], expiry_anchors: [7, 30, 90], distance_lambda: 0.01 }, prompt_templates: { system: "## Role\nTrade {universe}.\nLiteral {{json}} is unchanged.", user: "Data: {chain_sample_table}" }, prompt_variables: { system: [{ name: "universe", description: "标的", description_en: "Underlyings", source: "Setting", example: "SPY" }], user: [{ name: "chain_sample_table", description: "Options", source: "Chain", example: "SPY call" }] } };
  routing.use({ method: "GET", path: "/v1/me", body: { can_launch: true } });
  routing.use({ method: "GET", path: "/v1/tasks/presets", body: { items: [preset], custom_types: [], supported_underlyings: ["SPY"] } });
  routing.use({ method: "GET", path: "/v1/model-settings", body: { models: [{ id: "m", label: "Model", group_id: "g" }], groups: [{ id: "g", label: "Gateway", has_api_key: true }] } });
  await page.goto("/runs/new");
  for (let i = 0; i < 2; i++) await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByLabel("system prompt").locator('mark[data-variable="universe"]')).toHaveText("{universe}");
  await page.getByLabel("Edit prompt", { exact: true }).check();
  const editor = page.getByLabel("system prompt");
  await editor.fill("Trade ");
  await editor.press("End");
  await page.getByRole("button", { name: "Insert {universe}" }).click();
  await expect(editor).toHaveValue("Trade {universe}");
  await expect(page.getByTestId("prompt-editor-highlights").locator("mark")).toHaveText("{universe}");
  await page.getByRole("button", { name: "Open template viewer" }).click();
  await expect(page.getByRole("dialog").locator('[data-variable="universe"]')).toHaveText("{universe}");
  await page.getByRole("button", { name: "Close prompt window" }).click();
  await expect(editor).toHaveValue("Trade {universe}");
  await page.getByRole("button", { name: "Restore example" }).click();
  await page.screenshot({ path: "/tmp/liveoption-prompt-editor.png", fullPage: true });
});
