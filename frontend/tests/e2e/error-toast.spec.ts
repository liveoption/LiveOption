/**
 * <ErrorToastHost /> — global toast region for worker.error events.
 *
 * Note on file location: the spec asks for `frontend/tests/error-toast.spec.ts`,
 * but Playwright's `testDir` is configured to `./tests/e2e` (see
 * `playwright.config.ts`). Placing the file under `tests/e2e/` is the only
 * way the runner picks it up.
 *
 * Note on event injection: the existing SSE pipe is a long-lived
 * `EventSource`; Playwright's `page.route()` cannot easily push frames into
 * an open stream. Phase 3's store therefore exposes
 * `window.__LO_ERROR_TOASTS__.push(...)` as a deliberate test seam — the
 * SSE handler in `useRunData.ts` calls the same `pushError()` underneath,
 * so this exercises the full production path minus the SSE transport
 * itself (which is covered by `useRunData`'s onEvent test elsewhere).
 */
import { test, expect } from "./fixtures/test";

interface PushArg {
  runId: string;
  category: string;
  severity: "error" | "warning" | "info";
  count: number;
  message: string;
  firstTs: string;
  lastTs: string;
}

async function pushToast(page: import("@playwright/test").Page, arg: PushArg) {
  await page.evaluate((a) => {
    window.__LO_ERROR_TOASTS__?.push(a);
  }, arg);
}

test.describe("ErrorToastHost", () => {
  test("dedups by (runId, category) and shows incrementing count", async ({
    page,
  }) => {
    // The toast host is mounted at the root layout level, so any route
    // works. We use `/` because some test envs SSR-fail on /runs (Clerk
    // `getAuth` requires a server-fn ctx that the dev harness doesn't
    // wire up); the toast region is identical regardless.
    await page.goto("/");
    await page.waitForFunction(
      () => Boolean(window.__LO_ERROR_TOASTS__),
    );
    await page.evaluate(() => window.__LO_ERROR_TOASTS__?.dismissAll());

    // Five same-key events -> 1 toast, count badge shows 5.
    for (let i = 1; i <= 5; i++) {
      await pushToast(page, {
        runId: "run_alpha",
        category: "auth",
        severity: "error",
        count: i,
        message: "Qwen API returned 401",
        firstTs: "2026-04-27T07:18:42.000Z",
        lastTs: `2026-04-27T07:18:${42 + i}.000Z`,
      });
    }

    const toasts = page.getByTestId("error-toast");
    await expect(toasts).toHaveCount(1);
    await expect(
      toasts.first().getByTestId("error-toast-count"),
    ).toHaveText("5");

    // Sixth event with different category -> 2 toasts.
    await pushToast(page, {
      runId: "run_alpha",
      category: "rate_limit",
      severity: "error",
      count: 1,
      message: "Throttled (429)",
      firstTs: "2026-04-27T07:19:00.000Z",
      lastTs: "2026-04-27T07:19:00.000Z",
    });
    await expect(toasts).toHaveCount(2);
  });

  test("auto-dismisses after the severity timeout", async ({ page }) => {
    // The toast host is mounted at the root layout level, so any route
    // works. We use `/` because some test envs SSR-fail on /runs (Clerk
    // `getAuth` requires a server-fn ctx that the dev harness doesn't
    // wire up); the toast region is identical regardless.
    await page.goto("/");
    await page.waitForFunction(() => Boolean(window.__LO_ERROR_TOASTS__));
    await page.evaluate(() => window.__LO_ERROR_TOASTS__?.dismissAll());

    await pushToast(page, {
      runId: "run_alpha",
      category: "internal",
      severity: "error",
      count: 1,
      message: "boom",
      firstTs: "2026-04-27T07:20:00.000Z",
      lastTs: "2026-04-27T07:20:00.000Z",
    });

    await expect(page.getByTestId("error-toast")).toHaveCount(1);
    // 8s error timeout; allow a 2s margin.
    await expect(page.getByTestId("error-toast")).toHaveCount(0, {
      timeout: 12_000,
    });
  });
});
