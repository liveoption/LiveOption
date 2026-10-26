/**
 * /runs/:runId — RunErrorBanner + status pill + last-error header tests.
 *
 * Seeds a fixture run in `completed_with_errors` state with two worker.error
 * events covering both `error` and `warning` severity, plus historical
 * events delivered via `/v1/runs/:id/events/history`. Asserts:
 *   - Banner renders with one row per category and a count.
 *   - Header status pill shows `Completed (N errors)` in red text.
 *   - Header shows the truncated `Last error: …` link.
 *   - The list page renders the row with the ⚠ marker.
 */
import { test, expect } from "./fixtures/test";

const RUN_ID = "run_with_errors";

const RUN_FIXTURE = {
  id: RUN_ID,
  scenario: "baseline_2024",
  status: "completed_with_errors",
  kind: "batch",
  title: "Run with errors",
  date_start: "2024-01-02",
  date_end: "2024-01-05",
  is_featured: false,
  error_message: "Qwen API returned 401",
};

const EVENTS_HISTORY = {
  events: [
    {
      id: 1,
      kind: "worker.error",
      payload: {
        severity: "error",
        category: "auth",
        message: "Qwen API returned 401",
        count: 17,
        first_ts: "2026-04-27T07:18:42.123Z",
        last_ts: "2026-04-27T07:42:11.901Z",
        context: { provider: "qwen", model_id: "qwen-plus" },
      },
      occurred_at: "2026-04-27T07:42:11.901Z",
    },
    {
      id: 2,
      kind: "worker.error",
      payload: {
        severity: "warning",
        category: "parse",
        message: "Provider returned non-JSON content",
        count: 3,
        first_ts: "2026-04-27T07:20:10.000Z",
        last_ts: "2026-04-27T07:30:00.000Z",
        context: { decision_seq: 4 },
      },
      occurred_at: "2026-04-27T07:30:00.000Z",
    },
  ],
  total: 2,
  truncated: false,
  next_cursor: null,
};

test.describe("RunErrorBanner + completed_with_errors UX", () => {
  test.beforeEach(async ({ routing }) => {
    routing.use({
      method: "GET",
      path: "/v1/runs/:runId",
      body: (params) =>
        params.runId === RUN_ID
          ? RUN_FIXTURE
          : { id: params.runId, status: "unknown" },
    });
    routing.use({
      method: "GET",
      path: "/v1/runs/:runId/events/history",
      body: (params) => (params.runId === RUN_ID ? EVENTS_HISTORY : { events: [], total: 0, truncated: false, next_cursor: null }),
    });
    routing.use({
      method: "GET",
      path: "/v1/runs",
      body: () => ({
        items: [RUN_FIXTURE],
        next_cursor: null,
      }),
    });
  });

  test("detail page shows banner, last-error link, and Completed (N errors) pill", async ({ page }) => {
    await page.goto(`/runs/${RUN_ID}`);

    // Banner present with both rows.
    const banner = page.getByTestId("run-error-banner");
    await expect(banner).toBeVisible();
    const rows = page.getByTestId("run-error-banner-row");
    await expect(rows).toHaveCount(2);

    // The auth row carries severity=error and a count of 17.
    const authRow = page.locator(
      '[data-testid="run-error-banner-row"][data-category="auth"]',
    );
    await expect(authRow).toHaveAttribute("data-severity", "error");
    await expect(authRow).toContainText("17 occurrences");
    await expect(authRow).toContainText("Qwen API returned 401");

    // The parse row is severity=warning.
    const parseRow = page.locator(
      '[data-testid="run-error-banner-row"][data-category="parse"]',
    );
    await expect(parseRow).toHaveAttribute("data-severity", "warning");

    // Status pill says Completed (N errors). N = 1 (one distinct event row of severity=error).
    const pill = page.getByTestId("run-status-pill");
    await expect(pill).toContainText(/Completed \(\d+ errors?\)/);
    await expect(pill).toHaveAttribute("data-status", "completed_with_errors");

    // Header surfaces last error message.
    const lastErr = page.getByTestId("run-last-error");
    await expect(lastErr).toBeVisible();
    await expect(lastErr).toContainText("Qwen API returned 401");

    // View context expander.
    await authRow.getByTestId("run-error-banner-toggle").click();
    await expect(authRow.getByTestId("run-error-banner-context")).toBeVisible();
    await expect(authRow.getByTestId("run-error-banner-context")).toContainText("qwen-plus");
  });

  test("list page shows ⚠ marker for completed_with_errors runs", async ({ page }) => {
    await page.goto("/runs");
    const status = page
      .getByTestId("runs-row-status")
      .filter({ hasText: "Completed" });
    await expect(status.first()).toBeVisible();
    await expect(status.first()).toHaveAttribute("data-status", "completed_with_errors");
    await expect(status.first()).toContainText("\u26A0");
  });

  test("list page exposes a Completed w/ errors filter chip", async ({ page }) => {
    await page.goto("/runs");
    // Wait for rows to render so we know React has hydrated and onClick
    // handlers are attached before we click the chip.
    await expect(page.getByTestId("runs-row").first()).toBeVisible();
    const chip = page.getByRole("button", { name: "Completed w/ errors" });
    await expect(chip).toBeVisible();
    await chip.click();
    await expect(page).toHaveURL(/status=completed_with_errors/);
  });
});
