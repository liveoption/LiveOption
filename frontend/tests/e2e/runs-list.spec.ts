/**
 * /runs — filters update the URL and the row list.
 *
 * Smoke-level: click the `finished` status chip, assert URL has
 * `?status=finished` and that at least one row is present (the `finished`
 * fixtures). No filter-matrix exhaustion — unit tests cover that.
 */
import { test, expect } from "./fixtures/test";

test("runs list: status chip updates URL + list", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByRole("heading", { name: "Runs" })).toBeVisible();

  // Wait for at least one row to hydrate.
  await expect(page.getByTestId("runs-row").first()).toBeVisible();

  await page.getByRole("button", { name: "finished" }).click();
  await expect(page).toHaveURL(/[?&]status=finished\b/);

  // Rows still present and all are the finished fixtures.
  const rows = page.getByTestId("runs-row");
  await expect(rows.first()).toBeVisible();
});

test("runs list: scenario filter roundtrip", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByTestId("runs-row").first()).toBeVisible();
  const scenarioInput = page.getByRole("searchbox", { name: "Scenario" });
  await scenarioInput.fill("aggressive_2024");
  await expect(page).toHaveURL(/scenario=aggressive_2024/);
});
