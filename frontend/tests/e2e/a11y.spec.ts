/**
 * axe-core accessibility sweep per page.
 *
 * Uses the browser-side axe-core build loaded from a CDN into each page to
 * avoid adding a new devDependency until Task 14's a11y baseline is green on
 * a real CI run. The check asserts *zero serious+critical* violations.
 *
 * TODO: once `@axe-core/playwright` is added to package.json, swap the
 * CDN injection for `new AxeBuilder({ page }).analyze()` and restrict to
 * tags ['wcag2a', 'wcag2aa'].
 */
import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures/test";

const AXE_CDN = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.8.4/axe.min.js";

async function runAxe(page: Page): Promise<Array<{ id: string; impact: string | null }>> {
  await page.addScriptTag({ url: AXE_CDN });
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: (ctx?: unknown) => Promise<{ violations: Array<{ id: string; impact: string | null }> }> } }).axe;
    const { violations } = await axe.run();
    return violations.map((v) => ({ id: v.id, impact: v.impact }));
  });
}

const PAGES: Array<{ label: string; path: string }> = [
  { label: "home", path: "/" },
  { label: "runs list", path: "/runs" },
  { label: "run detail", path: "/runs/run_alpha" },
  { label: "checkpoint detail", path: "/checkpoints/ckpt_1" },
  { label: "live (running run)", path: "/runs/run_beta" },
  { label: "compare", path: "/runs/run_alpha/compare/run_gamma" },
];

for (const p of PAGES) {
  test(`a11y: no serious/critical axe violations on ${p.label}`, async ({ page }) => {
    test.skip(
      !!process.env.E2E_SKIP_AXE,
      "axe scan skipped via E2E_SKIP_AXE (likely offline environment)",
    );
    await page.goto(p.path);
    // Allow the route to settle before scanning.
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const violations = await runAxe(page);
    const serious = violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(
      serious,
      `Serious/critical a11y violations on ${p.label}: ${serious.map((v) => v.id).join(", ")}`,
    ).toEqual([]);
  });
}
