/**
 * Hermetic FastAPI response fixtures for Playwright E2E tests.
 *
 * Shape is MSW-compatible (`http.*`) so we can swap to MSW Service Worker
 * later, but the current install uses Playwright's `page.route()` via
 * `installRoutes()` in `fixtures/routing.ts`. Keep each handler pure — no
 * hidden state between specs.
 *
 * Envelope: Orval is configured to parse `{ status, data }` envelopes, but
 * the FastAPI app actually returns the bare payload. Orval's generated
 * clients wrap `res.data = <bare>` and `res.status` is read from the HTTP
 * status; tests therefore return bare JSON bodies here.
 */

export type JsonBody = Record<string, unknown> | unknown[];

export interface RouteHandler {
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  /** Path pattern, using `:param` placeholders. Matched against URL pathname. */
  path: string;
  status?: number;
  contentType?: string;
  body: JsonBody | ((params: Record<string, string>, url: URL) => JsonBody);
}

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

export const FIXTURE_RUNS = [
  {
    id: "run_alpha",
    scenario: "baseline_2024",
    status: "finished",
    kind: "batch",
    title: "Alpha run",
    date_start: "2024-01-02",
    date_end: "2024-01-05",
    is_featured: true,
  },
  {
    id: "run_beta",
    scenario: "baseline_2024",
    status: "running",
    kind: "live",
    title: "Beta run",
    date_start: "2024-01-02",
    date_end: "2024-01-05",
    is_featured: false,
  },
  {
    id: "run_gamma",
    scenario: "aggressive_2024",
    status: "finished",
    kind: "fork",
    title: "Gamma fork",
    date_start: "2024-02-01",
    date_end: "2024-02-03",
    is_featured: false,
  },
];

export const FIXTURE_TIMELINE = {
  items: [
    {
      id: "dec_1",
      seq: 1,
      trading_day: "2024-01-02",
      has_trade: true,
      summary: "open covered call",
    },
    {
      id: "dec_2",
      seq: 2,
      trading_day: "2024-01-03",
      has_trade: false,
      summary: "hold",
    },
  ],
  next_cursor: null,
};

export const FIXTURE_PNL = {
  points: [
    { trading_day: "2024-01-02", cum_pnl: 100.0, portfolio_value: 10100 },
    { trading_day: "2024-01-03", cum_pnl: 150.25, portfolio_value: 10150.25 },
  ],
};

export const FIXTURE_CHECKPOINT = {
  id: "ckpt_1",
  run_id: "run_alpha",
  decision_record_id: "dec_1",
  storage_dir: "/tmp/checkpoints/ckpt_1",
  fingerprint: "abc123",
  portfolio_summary: { cash: 10_000, positions: [] },
  promoted_at: "2024-01-02T10:00:00Z",
  promoted_by: "tester",
  status: "promoted",
  status_updated_at: "2024-01-02T10:00:00Z",
  label: "post-open",
};

// ---------------------------------------------------------------------------
// Handlers — one per API route exercised by the specs.
// ---------------------------------------------------------------------------

export const handlers: RouteHandler[] = [
  // Runs list
  {
    method: "GET",
    path: "/v1/runs",
    body: (_params, url) => {
      const scenario = url.searchParams.get("scenario");
      const status = url.searchParams.get("status");
      const kind = url.searchParams.get("kind");
      const featured = url.searchParams.get("featured");
      let items = [...FIXTURE_RUNS];
      if (scenario) items = items.filter((r) => r.scenario === scenario);
      if (status) items = items.filter((r) => r.status === status);
      if (kind) items = items.filter((r) => r.kind === kind);
      if (featured === "true") items = items.filter((r) => r.is_featured);
      return { items, next_cursor: null };
    },
  },

  // Run detail
  {
    method: "GET",
    path: "/v1/runs/:runId",
    body: (params) => {
      const run = FIXTURE_RUNS.find((r) => r.id === params.runId);
      return run ?? { id: params.runId, status: "unknown" };
    },
  },

  // Timeline
  {
    method: "GET",
    path: "/v1/runs/:runId/timeline",
    body: FIXTURE_TIMELINE,
  },

  // PnL series
  {
    method: "GET",
    path: "/v1/runs/:runId/pnl",
    body: FIXTURE_PNL,
  },

  // Decision detail
  {
    method: "GET",
    path: "/v1/runs/:runId/decisions/:seq",
    body: (params) => ({
      id: `dec_${params.seq}`,
      seq: Number(params.seq),
      run_id: params.runId,
      trading_day: "2024-01-02",
      summary: "open covered call",
      context: { prompt: "SPY looks range-bound" },
      trades: [
        { symbol: "SPY240119C00450000", side: "sell", qty: 1, price: 2.5 },
      ],
    }),
  },

  // Checkpoint detail
  {
    method: "GET",
    path: "/v1/checkpoints/:ckptId",
    body: (params) => ({ ...FIXTURE_CHECKPOINT, id: params.ckptId }),
  },

  // Checkpoint Q&A thread
  {
    method: "GET",
    path: "/v1/checkpoints/:ckptId/questions",
    body: {
      items: [
        {
          id: "q1",
          checkpoint_id: "ckpt_1",
          prompt: "What's the downside?",
          answer: "Max loss bounded by strike - premium.",
          status: "answered",
          created_at: "2024-01-02T10:05:00Z",
        },
      ],
      next_cursor: null,
    },
  },

  // Ask (POST)
  {
    method: "POST",
    path: "/v1/checkpoints/:ckptId/questions",
    status: 201,
    body: {
      id: "q_new",
      checkpoint_id: "ckpt_1",
      prompt: "stub",
      answer: null,
      status: "pending",
      created_at: "2024-01-02T10:06:00Z",
    },
  },

  // Fork (POST)
  {
    method: "POST",
    path: "/v1/checkpoints/:ckptId/forks",
    status: 202,
    body: { run_id: "run_fork_99", status: "scheduled" },
  },
];

/** Handlers that return an error — opt-in per spec via `routing.use()`. */
export const errorHandlers = {
  forkConflict: {
    method: "POST" as const,
    path: "/v1/checkpoints/:ckptId/forks",
    status: 409,
    contentType: "application/problem+json",
    body: {
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "Max 4 concurrent runs in flight.",
    },
  } satisfies RouteHandler,
};

// ---------------------------------------------------------------------------
// Task 13 — /runs/new launch-form helpers.
//
// These install Playwright route stubs directly on a `Page` using the
// `**/api/...` glob pattern, which matches the URL the browser actually
// hits (httpClient rewrites `/v1/*` → `/api/*` before fetching). They wrap
// the bare JSON body in the HTTP response; the httpClient mutator turns
// that back into the `{ data, status, headers }` envelope that Orval's
// generated hooks + LaunchForm / /runs/new read downstream.
//
// The helpers are page-scoped (not context-scoped) and win over the
// default `Routing` fallthrough by registering later.
// ---------------------------------------------------------------------------

import type { Page } from "@playwright/test";

export interface ScenarioFixture {
  id: string;
  display_name: string;
  description?: string;
  config_path?: string;
  overridable: string[];
}

export const DEFAULT_SCENARIOS: ScenarioFixture[] = [
  {
    id: "intraday_spy_2025_q1",
    display_name: "SPY Options · Intraday",
    description: "SPY options replay.",
    config_path: "configs/scenarios/intraday_spy_2025_q1.yaml",
    overridable: ["start", "end", "bankroll", "llm_model", "prompt_override", "decision_interval_minutes", "max_concurrent_positions", "dte_range"],
  },
];

export const DEFAULT_SCENARIO_CATALOG = {
  items: DEFAULT_SCENARIOS,
  llm_model_choices: ["gemini-3-flash", "deepseek-chat"],
};

export const DEFAULT_ALLOWLISTED_ME = {
  email: "haochenluo02@gmail.com",
  can_launch: true,
};

export const DEFAULT_DENIED_ME = {
  email: "stranger@example.com",
  can_launch: false,
};

/** Wire `GET **\/api/me` with the given body. */
export async function mockMeEndpoint(
  page: Page,
  body: Record<string, unknown>,
): Promise<void> {
  await page.route("**/api/me", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

/** Wire `GET **\/api/scenarios` with the given catalog. */
export async function mockScenariosEndpoint(
  page: Page,
  catalog: { items: ScenarioFixture[]; llm_model_choices: string[] } = DEFAULT_SCENARIO_CATALOG,
): Promise<void> {
  await page.route("**/api/scenarios", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(catalog),
    });
  });
}

export interface CapturedCreateRun {
  bodies: unknown[];
}

/**
 * Wire `POST **\/api/runs` returning a 202 with `{run_id}`. Returns a
 * handle whose `bodies` array collects each request's parsed JSON body so
 * the spec can assert the POST contract.
 */
export async function mockCreateRun(
  page: Page,
  opts: { runId?: string; status?: number; body?: Record<string, unknown> } = {},
): Promise<CapturedCreateRun> {
  const captured: CapturedCreateRun = { bodies: [] };
  const runId = opts.runId ?? "run_launch_42";
  const status = opts.status ?? 202;
  const body = opts.body ?? { run_id: runId, status: "scheduled" };

  await page.route("**/api/runs", async (route) => {
    const req = route.request();
    if (req.method() !== "POST") {
      await route.fallback();
      return;
    }
    const raw = req.postData() ?? "";
    try {
      captured.bodies.push(raw ? JSON.parse(raw) : null);
    } catch {
      captured.bodies.push(raw);
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  return captured;
}
