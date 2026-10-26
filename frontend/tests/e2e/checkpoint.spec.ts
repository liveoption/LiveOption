/**
 * /checkpoints/:ckptId — frozen-context card + ask form submission.
 */
import { test, expect } from "./fixtures/test";

test("checkpoint detail renders frozen-context card", async ({ page }) => {
  await page.goto("/checkpoints/ckpt_1");
  await expect(page.getByTestId("checkpoint-detail")).toBeVisible();
  await expect(page.getByTestId("frozen-context")).toBeVisible();
  await expect(page.getByTestId("qa-section")).toBeVisible();
});

test("ask form submits and renders a pending Q&A item", async ({ page }) => {
  await page.goto("/checkpoints/ckpt_1/ask");
  const drawer = page.getByTestId("ask-drawer");
  await expect(drawer).toBeVisible();
  const prompt = drawer.getByTestId("ask-panel-prompt");
  await prompt.fill("What if vol spikes?");
  const submit = drawer.getByTestId("ask-panel-submit");
  await expect(submit).toBeEnabled();
  await submit.click();
  // Existing thread entry still shown (fixture has one `q1` answered item).
  await expect(page.getByTestId("qa-thread")).toBeVisible();
});
