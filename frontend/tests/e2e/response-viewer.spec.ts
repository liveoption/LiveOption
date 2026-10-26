import { test, expect } from "./fixtures/test";
import { readFile } from "node:fs/promises";

const response = { calls: [
  { phase: "analyzer", raw: '```json\n{"intent":"bearish","confidence":0.85,"reasoning":"Protect the **stock position**. <script>window.responseInjected=true</script>","target_instruments":["BUY PUT K=500–510 DTE=20–30"]}\n```' },
  { phase: "executor", raw: "[]" },
  { phase: "executor", raw: '[{"action":"BUY","symbol":"O:QQQ250221P00510000","quantity":2,"limit_price":1.25},{"action":"SELL","symbol":"O:QQQ250221P00500000","quantity":2,"limit_price":0.50}]' },
  { phase: "malformed", raw: '```json\n{"broken":}\n```' },
  { phase: "legacy", analysis: { reasoning: "Saved structured data only." } },
  { phase: "many_orders", raw: JSON.stringify(Array.from({ length: 25 }, (_, index) => ({ symbol: `SPY-${index}`, quantity: 1 }))) },
] , intents_count: 0 };

test("response viewer parses fenced JSON, separates stages and preserves raw downloads", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/decisions/:decisionId", body: { artifact_available: true, artifact: { response } } });
  await page.goto("/runs/run_alpha?section=history&seq=1");
  const opener = page.getByRole("button", { name: /^Model response/ });
  await opener.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("85% (0.85)", { exact: true })).toBeVisible();
  await expect(dialog.getByText("BUY PUT K=500–510 DTE=20–30", { exact: true })).toBeVisible();
  await expect(dialog.locator("strong")).toContainText("stock position");
  await expect(dialog.locator("script")).toHaveCount(0);
  expect(await page.evaluate(() => (window as Window & { responseInjected?: boolean }).responseInjected)).toBeUndefined();
  await page.screenshot({ path: "/tmp/liveoption-response-viewer.png", fullPage: true });
  await dialog.getByRole("tab", { name: "Original response text", exact: true }).click();
  await expect(dialog.getByTestId("response-original-text")).toHaveText(response.calls[0].raw!);
  await dialog.getByRole("tab", { name: "Raw JSON", exact: true }).click();
  expect(JSON.parse(await dialog.getByTestId("response-raw-json").innerText())).toEqual(response);
  const downloading = page.waitForEvent("download"); await dialog.getByRole("button", { name: "Download raw JSON" }).click();
  const download = await downloading; expect(JSON.parse(await readFile((await download.path())!, "utf8"))).toEqual(response);
  await dialog.getByRole("tab", { name: "Readable response", exact: true }).click();
  await dialog.getByLabel("Response call phase").selectOption("1");
  await expect(dialog.getByText("Empty list (0 items)")).toBeVisible();
  await dialog.getByLabel("Response call phase").selectOption("2");
  await expect(dialog.getByRole("table")).toBeVisible();
  await expect(dialog.getByRole("cell", { name: "O:QQQ250221P00510000", exact: true })).toBeVisible();
  await dialog.getByLabel("Response call phase").selectOption("3");
  await expect(dialog.getByText(/The response is not a complete JSON/)).toBeVisible();
  await expect(dialog.getByTestId("response-readable")).toContainText('{"broken":}');
  await dialog.getByLabel("Response call phase").selectOption("4");
  await expect(dialog.getByText("Saved structured data only.")).toBeVisible();
  await dialog.getByRole("tab", { name: "Original response text", exact: true }).click();
  await expect(dialog.getByTestId("response-original-text")).toContainText("was not saved");
  await dialog.getByRole("tab", { name: "Readable response", exact: true }).click();
  await dialog.getByLabel("Response call phase").selectOption("5");
  await dialog.getByRole("button", { name: "Next", exact: true }).click();
  await expect(dialog.getByRole("cell", { name: "SPY-24", exact: true })).toBeVisible();
  await dialog.getByLabel("Response call phase").selectOption("2");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/liveoption-response-viewer-mobile.png", fullPage: true });
  await page.keyboard.press("Escape"); await expect(dialog).toHaveCount(0); await expect(opener).toBeFocused();
});
