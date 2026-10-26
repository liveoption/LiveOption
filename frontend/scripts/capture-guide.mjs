/** Capture English documentation from an authenticated local sample instance.
 * No API stubs, auth bypasses, model calls or task submissions are installed.
 * Run from frontend/; see docs/images/web-guide/README.md.
 */
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import path from "node:path";

const { values } = parseArgs({ options: {
  url: { type: "string" }, "run-id": { type: "string" }, "token-env-file": { type: "string" },
  output: { type: "string", default: fileURLToPath(new URL("../../docs/images/web-guide", import.meta.url)) },
  "qfz-url": { type: "string" }, "qfz-login-file": { type: "string" },
  only: { type: "string" },
} });
function localUrl(value) {
  const url = new URL(value);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) throw new Error("Use an isolated loopback sample instance.");
  return url.origin;
}
if (!values.url || !values["run-id"] || !values["token-env-file"]) {
  throw new Error("Required: --url, --run-id, --token-env-file. See docs/images/web-guide/README.md.");
}
const base = localUrl(values.url);
const token = (await readFile(values["token-env-file"], "utf8")).match(/^LIVEOPTION_ACCESS_TOKEN=(.+)$/m)?.[1]?.trim();
if (!token) throw new Error("Access token is missing from the supplied local env file.");
await mkdir(values.output, { recursive: true });
const browser = await chromium.launch();
const manifest = [];
const settings = { viewport: { width: 1440, height: 1060 }, locale: "en-US", colorScheme: "light", deviceScaleFactor: 1 };
async function capture(page, name) {
  if (values.only && !values.only.split(",").includes(name)) return;
  await expect(page.locator("html")).toHaveAttribute("lang", /^en/);
  await page.evaluate(() => document.fonts.ready);
  // Never capture a credential that has been entered into a password control.
  if (await page.locator('input[type="password"]').evaluateAll(inputs => inputs.some(input => input.value))) {
    throw new Error("Refusing to capture a populated credential control.");
  }
  const fullPage = name === "liveoption-new-task.png";
  const png = await page.screenshot({ path: path.join(values.output, name), animations: "disabled", fullPage });
  manifest.push({ file: name, path: new URL(page.url()).pathname, locale: "en", width: png.readUInt32BE(16), height: png.readUInt32BE(20) });
  console.log(`Captured ${name}`);
}
try {
  const context = await browser.newContext(settings);
  await context.addInitScript(() => { localStorage.setItem("lo-locale", "en"); localStorage.setItem("lo-theme", "light"); });
  const session = await context.request.post(base + "/api/session", { data: { token } });
  if (!session.ok()) throw new Error("Local LiveOption authentication failed.");
  const runId = values["run-id"];
  const response = await context.request.get(base + "/api/tasks/" + encodeURIComponent(runId));
  if (!response.ok()) throw new Error("Sample run is unavailable.");
  const run = await response.json();
  if (run.status !== "completed" || !run.universe?.length || run.universe.some(t => !["QZSP", "ACME", "BOLT"].includes(t))) {
    throw new Error("Screenshots require a completed synthetic-sample run.");
  }
  const page = await context.newPage();
  await page.goto(base + "/settings/models");
  await expect(page.getByRole("heading", { name: "API & model settings" })).toBeVisible();
  await expect(page.getByText(/Models in this group ·/)).toBeVisible();
  await capture(page, "liveoption-model-settings.png");

  await page.goto(base + "/runs/new");
  await page.getByRole("tab", { name: "Custom task", exact: true }).click();
  await page.getByRole("button", { name: "Intraday", exact: true }).click();
  await page.getByLabel("Run name (optional)").fill("QFinZero sample walkthrough");
  await page.getByLabel("From", { exact: true }).fill("2025-03-24");
  await page.getByLabel("To", { exact: true }).fill("2025-03-24");
  await page.getByLabel("Underlyings (comma-separated)").fill("QZSP");
  await expect(page.getByLabel("Run model")).toBeEnabled();
  await capture(page, "liveoption-new-task.png");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Decision interval (min)").fill("60");
  await page.getByLabel("Order type policy").selectOption("market");
  await page.getByLabel("Slippage · bps").fill("0");
  await expect(page.getByTestId("trading-settings")).toBeVisible();
  await capture(page, "liveoption-trading-rules.png");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Trading context").fill("Use only the supplied market data. Explain the decision to trade or hold.");
  await expect(page.getByLabel("system prompt")).toBeVisible();
  await capture(page, "liveoption-agent-prompts.png");

  await page.goto(`${base}/runs/${encodeURIComponent(runId)}?section=chart`);
  await expect(page.getByTestId("kline-chart")).toBeVisible();
  await expect(page.getByTestId("kline-chart").locator("svg rect").first()).toBeVisible();
  await capture(page, "liveoption-run-chart.png");
  await page.goto(`${base}/runs/${encodeURIComponent(runId)}?section=history&seq=0`);
  const prompt = page.getByRole("button", { name: /^Actual prompt/ });
  await expect(prompt).toBeVisible();
  await prompt.click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "System", exact: true })).toBeVisible();
  await expect(page.getByTestId("prompt-pane-user")).toBeVisible();
  await capture(page, "liveoption-prompt-viewer.png");

  await page.goto(`${base}/runs/${encodeURIComponent(runId)}?section=performance`);
  await expect(page.getByRole("button", { name: "Export HTML report" })).toBeEnabled();
  await expect(page.getByTestId("result-visualizations")).toBeVisible();
  await capture(page, "liveoption-results-report.png");
  await page.goto(`${base}/runs/${encodeURIComponent(runId)}?section=logs`);
  await expect(page.getByLabel("Worker log content")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download full log" })).toBeVisible();
  await capture(page, "liveoption-run-logs.png");
  await context.close();

  if (values["qfz-url"] && values["qfz-login-file"]) {
    const hub = localUrl(values["qfz-url"]);
    const credentials = JSON.parse(await readFile(values["qfz-login-file"], "utf8"));
    const qfz = await browser.newContext(settings);
    await qfz.addCookies([{ name: "qfz_lang", value: "en", url: hub }]);
    const login = await qfz.request.post(hub + "/auth/login", { data: credentials });
    if (!login.ok()) throw new Error("Local QFinZero authentication failed.");
    const consolePage = await qfz.newPage();
    for (const [route, heading, file] of [
      ["/data-setup", "Data setup utility", "qfinzero-data-setup.png"],
      ["/availability", "Data availability", "qfinzero-data-availability.png"],
    ]) {
      const matrixLoaded = route === "/availability" ? consolePage.waitForResponse(response =>
        new URL(response.url()).pathname === "/api/coverage/matrix" && response.status() === 200,
        { timeout: 30_000 }) : null;
      const setupLoaded = route === "/data-setup" ? Promise.all(["status", "browse"].map(endpoint =>
        consolePage.waitForResponse(response => new URL(response.url()).pathname === `/api/data-admin/setup/${endpoint}`
          && response.status() === 200, { timeout: 30_000 }))) : null;
      await consolePage.goto(hub + route, { timeout: 120_000 });
      await expect(consolePage.getByRole("heading", { name: heading, exact: true })).toBeVisible({ timeout: 120_000 });
      // Coverage is loaded asynchronously; wait for its date controls.
      if (route === "/availability") {
        await matrixLoaded;
        await expect(consolePage.getByLabel("Start", { exact: true })).toHaveValue(/\d{4}-\d\d-\d\d/, { timeout: 30_000 });
        await expect(consolePage.getByText("QZSP", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
        await expect(consolePage.getByText("ACME", { exact: true }).first()).toBeVisible({ timeout: 30_000 });
        await expect(consolePage.getByText(/Some events are missing:/)).toHaveCount(0);
      }
      else {
        await setupLoaded;
        await expect(consolePage.getByText("Converts into", { exact: true })).toBeVisible({ timeout: 30_000 });
        await expect(consolePage.getByText("Loading...", { exact: true })).toHaveCount(0);
      }
      await capture(consolePage, file);
    }
    await qfz.close();
  }
  if (values.only) {
    try {
      const previous = JSON.parse(await readFile(path.join(values.output, "capture.json"), "utf8"));
      manifest.push(...previous.pages.filter(item => !manifest.some(current => current.file === item.file)));
    } catch { /* first capture */ }
  }
  await writeFile(path.join(values.output, "capture.json"), JSON.stringify({
    viewport: settings.viewport, data: "QFinZero synthetic sample", model: "local scripted HOLD fixture",
    pages: manifest,
  }, null, 2) + "\n");
} finally {
  await browser.close();
}
