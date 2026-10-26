import { test, expect } from "./fixtures/test";

test("first-visit guide waits for home and describes the current result sections", async ({ page, routing }) => {
  routing.use({ method: "GET", path: "/v1/session", body: { authenticated: true } });
  await page.goto("/runs/run_alpha");
  await expect(page.getByTestId("result-section-chart")).toBeVisible();
  await expect(page.getByRole("dialog", { name: /Welcome to LiveOption/ })).toHaveCount(0);

  await page.goto("/");
  const guide = page.getByRole("dialog", { name: /Welcome to LiveOption/ });
  await expect(guide).toBeVisible();
  await guide.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Read the chart and checkpoints" })).toBeVisible();
});
