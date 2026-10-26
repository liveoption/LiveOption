/**
 * /checkpoints/:ckptId/fork — fork form validation + successful fork navigates
 * (or at least reports) the new run id.
 */
import { test, expect } from "./fixtures/test";

test("fork form: submit disabled until an override is set", async ({ page }) => {
  await page.goto("/checkpoints/ckpt_1/fork");
  await expect(page.getByTestId("fork-drawer")).toBeVisible();
  const submit = page.getByTestId("fork-submit");
  await expect(submit).toBeDisabled();
  await expect(page.getByTestId("fork-validation-msg")).toBeVisible();

  await page.getByTestId("fork-prompt-override").fill("Try a more conservative play.");
  await expect(submit).toBeEnabled();
});

test("fork form: successful POST schedules a new run", async ({ page }) => {
  await page.goto("/checkpoints/ckpt_1/fork");
  await page.getByTestId("fork-prompt-override").fill("Sell covered calls instead.");
  await page.getByTestId("fork-submit").click();
  // Stub returns run_id=run_fork_99. Acceptable end-state: drawer closes, or
  // the page navigates to /runs/run_fork_99 — either indicates success.
  await expect
    .poll(async () => {
      if (page.url().includes("/runs/run_fork_99")) return "navigated";
      if (!(await page.getByTestId("fork-drawer").isVisible().catch(() => false)))
        return "closed";
      return "pending";
    }, { timeout: 5_000 })
    .not.toBe("pending");
});
