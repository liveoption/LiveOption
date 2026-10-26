import { test, expect } from "./fixtures/test";

test("saved dark mode is applied by a head script before hydration", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("lo-theme", "dark"));
  await page.goto("/");
  await expect(page.locator("html")).toHaveClass(/dark/);
  expect(await page.evaluate(() => window.__LO_THEME_INITIAL__)).toBe("dark");
  expect(await page.locator('meta[tagName="script"]').count()).toBe(0);
  const html = await (await page.request.get("/")).text();
  expect(html).toContain("/\\bdark\\b/g");
});
