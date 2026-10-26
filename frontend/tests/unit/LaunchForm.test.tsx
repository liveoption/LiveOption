/**
 * Tests for `app/components/LaunchForm.tsx` — Task 11 / launch-redesign.
 *
 * Matches the ForkDrawer test pattern: MSW handlers hang off `/api/*`
 * (the browser-side httpClient rewrites `/v1/*` → `/api/*`), React
 * Query retries are disabled, and the component is rendered inside a
 * fresh QueryClientProvider per test so the scenario catalog fetch is
 * real (no hook-level mocks, just an intercepted network response).
 *
 * Redesign behaviours covered:
 *   1. Scenario options from `/v1/scenarios` render in the select.
 *   2. Progressive disclosure: extra fields hidden until scenario picked.
 *   3. Required-field markers (* and aria-required) present on required fields.
 *   4. LLM model is a 5-option dropdown (paper §3 models).
 *   5. Defaults: start=2025-01-01, end=2025-12-31, bankroll=25000, model=deepseek-chat.
 *   6. Submit emits `{scenario_id, overrides}` with populated overrides.
 *   7. Submit button honours the `disabled` prop.
 *   8. llm_model select is hidden when the scenario's `overridable` set
 *      does not include it.
 *   9. Prompt override is inside a <details> Advanced section.
 *  10. Title included / omitted per trimmed value.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LaunchForm } from "../../app/components/LaunchForm";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

type CatalogItem = {
  id: string;
  display_name: string;
  description: string;
  config_path: string;
  overridable: string[];
  family?: string;
};

function catalog(items: CatalogItem[], llmChoices: string[] = ["deepseek-chat", "gemini-2.5-flash"]) {
  return http.get("/api/scenarios", () =>
    HttpResponse.json({ items, llm_model_choices: llmChoices }),
  );
}

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const FULL_OVERRIDABLE = ["start", "end", "bankroll", "llm_model", "prompt_override"];

/** Pick a scenario in the scenario select, wait for progressive disclosure to open. */
async function pickScenario(scenarioId: string) {
  const select = (await screen.findByTestId("launch-scenario")) as HTMLSelectElement;
  await waitFor(() => expect(select.options.length).toBeGreaterThan(1)); // > 1 because option[0] is placeholder
  fireEvent.change(select, { target: { value: scenarioId } });
  // After picking, the title field (inside progressive-disclosure) should appear.
  await screen.findByTestId("launch-title");
}

describe("LaunchForm", () => {
  it("renders scenario options from the API", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
        {
          id: "overlay_spy_qqq_2025_q1",
          display_name: "Overlay SPY+QQQ 2025 Q1",
          description: "overlay",
          config_path: "configs/overlay.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);

    // Pre-load: loading text
    expect(screen.queryByText(/Loading scenarios/i)).toBeTruthy();

    const select = (await screen.findByTestId(
      "launch-scenario",
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBeGreaterThan(1));
    expect(screen.getByText("Intraday SPY 2025 Q1 (0DTE)")).toBeTruthy();
    expect(screen.getByText("Overlay SPY+QQQ 2025 Q1")).toBeTruthy();
  });

  it("shows hint and hides form fields until a scenario is picked (progressive disclosure)", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);

    // Wait for scenario picker to appear.
    await screen.findByTestId("launch-scenario");

    // Before picking: hint text visible, form fields hidden.
    expect(screen.getByTestId("launch-scenario-hint")).toBeTruthy();
    expect(screen.queryByTestId("launch-title")).toBeNull();
    expect(screen.queryByTestId("launch-start")).toBeNull();
    expect(screen.queryByTestId("launch-end")).toBeNull();
    expect(screen.queryByTestId("launch-bankroll")).toBeNull();

    // Pick a scenario.
    await pickScenario("intraday_spy_2025_q1");

    // After picking: hint gone, fields visible.
    expect(screen.queryByTestId("launch-scenario-hint")).toBeNull();
    expect(screen.getByTestId("launch-title")).toBeTruthy();
    expect(screen.getByTestId("launch-start")).toBeTruthy();
    expect(screen.getByTestId("launch-end")).toBeTruthy();
    expect(screen.getByTestId("launch-bankroll")).toBeTruthy();
  });

  it("required fields have aria-required=true", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);
    await pickScenario("intraday_spy_2025_q1");

    const startInput = screen.getByTestId("launch-start") as HTMLInputElement;
    const endInput = screen.getByTestId("launch-end") as HTMLInputElement;
    const bankrollInput = screen.getByTestId("launch-bankroll") as HTMLInputElement;
    const llmSelect = screen.getByTestId("launch-llm-model") as HTMLSelectElement;

    expect(startInput.getAttribute("aria-required")).toBe("true");
    expect(endInput.getAttribute("aria-required")).toBe("true");
    expect(bankrollInput.getAttribute("aria-required")).toBe("true");
    expect(llmSelect.getAttribute("aria-required")).toBe("true");
  });

  it("defaults: start=2025-01-01, end=2025-12-31, bankroll=25000, llm_model=deepseek-chat", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);
    await pickScenario("intraday_spy_2025_q1");

    const startInput = screen.getByTestId("launch-start") as HTMLInputElement;
    const endInput = screen.getByTestId("launch-end") as HTMLInputElement;
    const bankrollInput = screen.getByTestId("launch-bankroll") as HTMLInputElement;
    const llmSelect = screen.getByTestId("launch-llm-model") as HTMLSelectElement;

    expect(startInput.value).toBe("2025-01-01");
    expect(endInput.value).toBe("2025-12-31");
    expect(bankrollInput.value).toBe("25000");
    expect(llmSelect.value).toBe("deepseek-chat");
  });

  it("LLM model dropdown has exactly 5 paper-listed options", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);
    await pickScenario("intraday_spy_2025_q1");

    const llmSelect = screen.getByTestId("launch-llm-model") as HTMLSelectElement;
    // Exactly 5 options (no placeholder — all are valid selectable models).
    expect(llmSelect.options.length).toBe(5);

    const ids = Array.from(llmSelect.options).map((o) => o.value);
    expect(ids).toContain("deepseek-chat");
    expect(ids).toContain("gpt-4.1-mini");
    expect(ids).toContain("gemini-2.5-flash");
    expect(ids).toContain("claude-sonnet-4-5");
    expect(ids).toContain("qwen2.5-max");
  });

  it("prompt override is inside a <details> Advanced section", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);
    await pickScenario("intraday_spy_2025_q1");

    // The summary label with "Advanced" text should be present.
    const summary = screen.getByText(/Advanced \(prompt override\)/i);
    expect(summary).toBeTruthy();
    // The textarea is inside the details block (may need to open it).
    const details = summary.closest("details");
    expect(details).toBeTruthy();
  });

  it("calls onSubmit with {scenario_id, overrides} on submit", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    const onSubmit = vi.fn();
    renderWithClient(<LaunchForm onSubmit={onSubmit} />);
    await pickScenario("intraday_spy_2025_q1");

    fireEvent.change(screen.getByTestId("launch-start"), {
      target: { value: "2025-01-05" },
    });
    fireEvent.change(screen.getByTestId("launch-end"), {
      target: { value: "2025-01-12" },
    });
    fireEvent.change(screen.getByTestId("launch-bankroll"), {
      target: { value: "25000" },
    });
    fireEvent.change(screen.getByTestId("launch-llm-model"), {
      target: { value: "deepseek-chat" },
    });

    // Open details and type in the prompt override textarea.
    const summary = screen.getByText(/Advanced \(prompt override\)/i);
    const details = summary.closest("details") as HTMLDetailsElement;
    details.open = true;
    fireEvent.change(screen.getByTestId("launch-prompt"), {
      target: { value: "Prefer covered calls." },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("launch-submit"));
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith({
      scenario_id: "intraday_spy_2025_q1",
      overrides: {
        start: "2025-01-05",
        end: "2025-01-12",
        bankroll: 25000,
        llm_model: "deepseek-chat",
        prompt_override: "Prefer covered calls.",
      },
    });
  });

  it("includes a trimmed title in the payload when filled", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    const onSubmit = vi.fn();
    renderWithClient(<LaunchForm onSubmit={onSubmit} />);
    await pickScenario("intraday_spy_2025_q1");

    fireEvent.change(screen.getByTestId("launch-title"), {
      target: { value: "  My pilot run  " },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("launch-submit"));
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.scenario_id).toBe("intraday_spy_2025_q1");
    expect(payload.title).toBe("My pilot run");
  });

  it("omits title from payload when blank/whitespace", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday SPY 2025 Q1 (0DTE)",
          description: "intraday",
          config_path: "configs/intraday.yaml",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    const onSubmit = vi.fn();
    renderWithClient(<LaunchForm onSubmit={onSubmit} />);
    await pickScenario("intraday_spy_2025_q1");

    fireEvent.change(screen.getByTestId("launch-title"), {
      target: { value: "   " },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("launch-submit"));
    });

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.title).toBeUndefined();
  });

  it("disables submit when disabled prop true", async () => {
    server.use(
      catalog([
        {
          id: "intraday_spy_2025_q1",
          display_name: "Intraday",
          description: "",
          config_path: "x",
          overridable: FULL_OVERRIDABLE,
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} disabled />);
    const submit = (await screen.findByTestId(
      "launch-submit",
    )) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("hides llm_model select when scenario lacks it in overridable", async () => {
    server.use(
      catalog([
        {
          id: "locked_model",
          display_name: "Locked model",
          description: "",
          config_path: "x",
          overridable: ["start", "end", "bankroll", "prompt_override"],
        },
      ]),
    );

    renderWithClient(<LaunchForm onSubmit={() => {}} />);
    await pickScenario("locked_model");

    expect(screen.queryByTestId("launch-llm-model")).toBeNull();
    // Prompt override details block should still exist.
    expect(screen.getByText(/Advanced \(prompt override\)/i)).toBeTruthy();
  });
});
