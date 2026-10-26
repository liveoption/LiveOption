import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for LiveOption frontend E2E tests (Plan C Task 15).
 *
 * Prerequisites for running locally:
 *   - `pnpm install` (playwright + browsers via `pnpm exec playwright install chromium`)
 *   - `pnpm dev` running the vinxi dev server on http://127.0.0.1:3000
 *   - (Optional) Plan B FastAPI on http://127.0.0.1:19720 for non-hermetic runs
 *
 * The test harness defaults to a hermetic mode: FastAPI responses are stubbed
 * via Playwright's `page.route()` using handlers from
 * `tests/e2e/fixtures/handlers.ts`. Specs that require a real SSE pipe or a
 * real Plan B server are gated with `test.skip()` + a TODO referencing the
 * `E2E_REAL_BACKEND` env var.
 *
 * Clerk: production Clerk is NOT hit. The app renders `<SignedOut>` content
 * by default when no Clerk session is present; the signed-in flows are
 * exercised by installing a dev-mode `__session` cookie via the `signedIn`
 * fixture (see `tests/e2e/fixtures/auth.ts`) and stubbing Clerk JWKS.
 */

const PORT = Number(process.env.E2E_PORT ?? 13555);
const HOST = process.env.E2E_HOST ?? "127.0.0.1";
const BASE_URL = process.env.E2E_BASE_URL ?? `http://${HOST}:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 5_000 },

  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Start the dev server automatically unless the caller brought their own.
  // NOTE: When bringing your own dev server (E2E_NO_WEBSERVER=1), it MUST be
  // launched with `E2E_TEST_AUTH=1 E2E_PLAYWRIGHT=1` so the server-side auth
  // guard short-circuits Clerk verification (see app/lib/auth-guard.ts).
  webServer: process.env.E2E_NO_WEBSERVER
    ? undefined
    : {
        command: `pnpm dev --host ${HOST} --port ${PORT} --strictPort`,
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        stdout: "pipe",
        stderr: "pipe",
        env: {
          E2E_TEST_AUTH: "1",
          E2E_PLAYWRIGHT: "1",
        },
      },
});
