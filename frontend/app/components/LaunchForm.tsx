import { useEffect, useMemo, useState } from "react";
import { Button, Card } from "@heroui/react";

import { useListScenariosV1ScenariosGet } from "../lib/orval/catalog/catalog";
import OverlayForm from "./launch/OverlayForm";
import EarningsForm from "./launch/EarningsForm";
import IntradayForm from "./launch/IntradayForm";
import type {
  EarningsValues,
  Family,
  IntradayValues,
  OverlayValues,
} from "./launch/types";

/**
 * `LaunchForm` — router-free launch form (Task 11, Plan B Web Run Launcher).
 *
 * Fetches the scenario catalog via `GET /v1/scenarios`, renders a scenario
 * picker plus the overridable subset of fields allowed by the selected
 * scenario (start / end / bankroll / llm_model / prompt_override), and
 * emits a `{ scenario_id, overrides }` payload — the exact body of
 * `POST /v1/runs` — when the user presses submit.
 *
 * The component is deliberately router-free: the parent (/runs/new
 * route, Task 12) owns the mutation, error surfacing, and navigation.
 * That split keeps unit tests focused on form behaviour and lets us
 * reuse the form from other surfaces later (e.g. a modal).
 *
 * Field policy:
 *   * Only fields whose key appears in the selected scenario's
 *     `overridable` list are rendered AND included in the emitted
 *     payload. Switching scenarios resets stale override values so
 *     hidden fields never leak into subsequent submissions.
 *   * Empty override fields are omitted from the payload — the backend
 *     applies the scenario's base YAML when a key is absent.
 *   * `bankroll` is coerced to `number` (never string).
 *   * `prompt_override` is capped at 16 384 chars with a live counter.
 */

type ScenarioItem = {
  id: string;
  display_name: string;
  description?: string;
  config_path?: string;
  overridable: string[];
  family?: Family | null;
  defaults?: Record<string, unknown>;
};

type Catalog = {
  items: ScenarioItem[];
  llm_model_choices: string[];
  supported_underlyings: string[];
};

export interface LaunchFormPayload {
  scenario_id: string;
  overrides: {
    start?: string;
    end?: string;
    bankroll?: number;
    llm_model?: string;
    prompt_override?: string;
    // Overlay
    underlying_holdings?: Array<{
      symbol: string;
      shares: number;
      cost_basis: number;
    }>;
    overlay_dte_range?: [number, number];
    // Earnings
    tickers?: string[];
    event_window?: [number, number];
    quarter?: string;
    // Intraday
    decision_interval_minutes?: number;
    max_concurrent_positions?: number;
    dte_range?: [number, number];
  };
  title?: string;
}

const TITLE_MAX = 200;

export interface LaunchFormProps {
  onSubmit: (payload: LaunchFormPayload) => void;
  disabled?: boolean;
}

const PROMPT_MAX = 16_384;

/** Legacy model choices; the current editor discovers models per API group. */
const LLM_MODELS = [
  { id: "deepseek-chat",     label: "DeepSeek-V3" },
  { id: "gpt-4.1-mini",      label: "GPT-4.1 Mini" },
  { id: "gemini-2.5-flash",  label: "Gemini 2.5 Flash" },
  { id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
  { id: "qwen2.5-max",       label: "Qwen 2.5 Max" },
] as const;

/** Default field values for the legacy launcher. */
const DEFAULTS = {
  start: "2025-01-01",
  end: "2025-12-31",
  bankroll: "25000",
  llm_model: "deepseek-chat",
} as const;

function toCatalog(body: unknown): Catalog | null {
  if (!body || typeof body !== "object") return null;
  const obj = body as Record<string, unknown>;
  const items = Array.isArray(obj.items) ? (obj.items as ScenarioItem[]) : null;
  const choices = Array.isArray(obj.llm_model_choices)
    ? (obj.llm_model_choices as string[])
    : [];
  if (!items) return null;
  const allowed = Array.isArray(obj.supported_underlyings)
    ? (obj.supported_underlyings as string[])
    : [];
  return { items, llm_model_choices: choices, supported_underlyings: allowed };
}

/** Small red asterisk for required fields. */
function Req() {
  return <span className="ml-0.5 text-red-600" aria-hidden="true">*</span>;
}

export function LaunchForm({ onSubmit, disabled = false }: LaunchFormProps) {
  const { data, isLoading } = useListScenariosV1ScenariosGet();

  const catalog = useMemo<Catalog | null>(() => {
    if (!data || data.status !== 200) return null;
    return toCatalog(data.data);
  }, [data]);

  const scenarios = catalog?.items ?? [];

  // No scenario selected by default — progressive disclosure.
  const [scenarioId, setScenarioId] = useState<string>("");
  const [title, setTitle] = useState<string>("");
  const [start, setStart] = useState<string>(DEFAULTS.start);
  const [end, setEnd] = useState<string>(DEFAULTS.end);
  const [bankroll, setBankroll] = useState<string>(DEFAULTS.bankroll);
  const [llmModel, setLlmModel] = useState<string>(DEFAULTS.llm_model);
  const [promptOverride, setPromptOverride] = useState<string>("");

  // Family-specific subform state. Each subform owns its own slice.
  const [overlayValues, setOverlayValues] = useState<OverlayValues>({});
  const [earningsValues, setEarningsValues] = useState<EarningsValues>({});
  const [intradayValues, setIntradayValues] = useState<IntradayValues>({});

  // Do NOT auto-select a scenario — leave picker blank until user picks.
  // (Previous auto-select logic removed per progressive-disclosure requirement.)

  const selected = scenarios.find((s) => s.id === scenarioId) ?? null;
  const overridable = new Set(selected?.overridable ?? []);

  // When the selected scenario changes, merge API defaults then reset
  // family state. We do NOT wipe start/end/bankroll/llm_model to the
  // built-in DEFAULTS here because the API may supply scenario-specific
  // defaults in `scenario.defaults`; we apply those instead.
  useEffect(() => {
    if (!selected) return;
    const apiDefaults = selected.defaults ?? {};

    setStart(typeof apiDefaults.start === "string" ? apiDefaults.start : DEFAULTS.start);
    setEnd(typeof apiDefaults.end === "string" ? apiDefaults.end : DEFAULTS.end);
    setBankroll(
      typeof apiDefaults.bankroll === "number"
        ? String(apiDefaults.bankroll)
        : DEFAULTS.bankroll,
    );
    setLlmModel(
      typeof apiDefaults.llm_model === "string"
        ? apiDefaults.llm_model
        : DEFAULTS.llm_model,
    );
    setPromptOverride("");
    setOverlayValues({});
    setEarningsValues({});
    setIntradayValues({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenarioId]);

  const family: Family | null = (selected?.family as Family | undefined) ?? null;

  const ready = !isLoading && scenarios.length > 0 && !!scenarioId;
  const submitDisabled = disabled || !ready;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    const overrides: LaunchFormPayload["overrides"] = {};
    if (overridable.has("start") && start) overrides.start = start;
    if (overridable.has("end") && end) overrides.end = end;
    if (overridable.has("bankroll") && bankroll !== "") {
      const n = Number(bankroll);
      if (!Number.isNaN(n)) overrides.bankroll = n;
    }
    if (overridable.has("llm_model") && llmModel) overrides.llm_model = llmModel;
    if (overridable.has("prompt_override") && promptOverride) {
      overrides.prompt_override = promptOverride;
    }

    // Family-specific overrides — only emit fields the user actually set.
    if (family === "overlay") {
      if (
        overlayValues.underlying_holdings &&
        overlayValues.underlying_holdings.length > 0
      ) {
        // Drop incomplete rows; coerce `shares`/`cost_basis` to numbers.
        const cleaned = overlayValues.underlying_holdings
          .filter((h) => h.symbol && h.shares !== "" && h.cost_basis !== "")
          .map((h) => ({
            symbol: h.symbol,
            shares: Number(h.shares),
            cost_basis: Number(h.cost_basis),
          }));
        if (cleaned.length > 0) overrides.underlying_holdings = cleaned;
      }
      if (overlayValues.overlay_dte_range) {
        overrides.overlay_dte_range = overlayValues.overlay_dte_range;
      }
    } else if (family === "earnings") {
      if (earningsValues.tickers && earningsValues.tickers.length > 0) {
        overrides.tickers = earningsValues.tickers;
      }
      if (earningsValues.event_window) {
        overrides.event_window = earningsValues.event_window;
      }
      if (earningsValues.quarter) overrides.quarter = earningsValues.quarter;
    } else if (family === "intraday") {
      if (intradayValues.decision_interval_minutes) {
        overrides.decision_interval_minutes =
          intradayValues.decision_interval_minutes;
      }
      if (intradayValues.max_concurrent_positions) {
        overrides.max_concurrent_positions =
          intradayValues.max_concurrent_positions;
      }
      if (intradayValues.dte_range) {
        overrides.dte_range = intradayValues.dte_range;
      }
      if (intradayValues.llm_model) overrides.llm_model = intradayValues.llm_model;
      if (intradayValues.prompt_override) {
        overrides.prompt_override = intradayValues.prompt_override;
      }
    }

    const trimmedTitle = title.trim();
    const payload: LaunchFormPayload = { scenario_id: scenarioId, overrides };
    if (trimmedTitle) payload.title = trimmedTitle;
    onSubmit(payload);
  };

  return (
    <Card className="!rounded-2xl !border !border-[var(--color-rule)] !bg-[var(--color-paper)]">
      <Card.Header className="border-b border-[var(--color-rule)] p-5">
        <Card.Title className="lo-display text-xl text-[color:var(--color-ink)]">
          Launch a scenario
        </Card.Title>
        <Card.Description className="mt-1 text-xs text-[color:var(--color-ink-soft)]">
          Pick a scenario from the catalog; adjust the permitted overrides;
          submit to start a batch run.
        </Card.Description>
      </Card.Header>
      <Card.Content className="p-5">
        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4"
          aria-label="Launch form"
          data-testid="launch-form"
        >
          {/* Required-field legend */}
          <p className="text-xs text-[color:var(--color-ink-soft)]">
            Fields marked <span className="text-red-600">*</span> are required.
          </p>

          {isLoading || scenarios.length === 0 ? (
            <p
              className="text-sm text-[color:var(--color-ink-soft)]"
              data-testid="launch-loading"
            >
              Loading scenarios…
            </p>
          ) : (
            <label className="flex flex-col gap-1 text-sm">
              <span className="lo-eyebrow">
                Scenario<Req />
              </span>
              <select
                data-testid="launch-scenario"
                value={scenarioId}
                onChange={(e) => setScenarioId(e.target.value)}
                aria-required="true"
                required
                className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm text-[color:var(--color-ink)]"
              >
                <option value="" disabled>
                  — select a scenario —
                </option>
                {scenarios.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.display_name}
                  </option>
                ))}
              </select>
              {selected?.description && (
                <span className="text-xs text-[color:var(--color-ink-soft)]">
                  {selected.description}
                </span>
              )}
            </label>
          )}

          {/* Progressive disclosure: nothing beyond scenario picker until a
              scenario is selected. */}
          {!scenarioId && (
            <p
              className="text-sm text-[color:var(--color-ink-soft)]"
              data-testid="launch-scenario-hint"
            >
              Select a scenario to continue.
            </p>
          )}

          {scenarioId && (
            <>
              <label className="flex flex-col gap-1 text-sm">
                <span className="lo-eyebrow">Title (optional)</span>
                <input
                  type="text"
                  data-testid="launch-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={TITLE_MAX}
                  placeholder="Recognizable name for this run"
                  className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm text-[color:var(--color-ink)]"
                />
              </label>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {overridable.has("start") && (
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="lo-eyebrow">
                      Start date<Req />
                    </span>
                    <input
                      type="date"
                      data-testid="launch-start"
                      value={start}
                      onChange={(e) => setStart(e.target.value)}
                      aria-required="true"
                      required
                      className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm"
                    />
                  </label>
                )}
                {overridable.has("end") && (
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="lo-eyebrow">
                      End date<Req />
                    </span>
                    <input
                      type="date"
                      data-testid="launch-end"
                      value={end}
                      onChange={(e) => setEnd(e.target.value)}
                      aria-required="true"
                      required
                      className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm"
                    />
                  </label>
                )}
                {overridable.has("bankroll") && (
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="lo-eyebrow">
                      Bankroll (USD)<Req />
                    </span>
                    <input
                      type="number"
                      inputMode="numeric"
                      min="0"
                      step="100"
                      data-testid="launch-bankroll"
                      value={bankroll}
                      onChange={(e) => setBankroll(e.target.value)}
                      aria-required="true"
                      required
                      placeholder="e.g. 25000"
                      className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm"
                    />
                  </label>
                )}
                {overridable.has("llm_model") && (
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="lo-eyebrow">
                      LLM model<Req />
                    </span>
                    <select
                      data-testid="launch-llm-model"
                      value={llmModel}
                      onChange={(e) => setLlmModel(e.target.value)}
                      aria-required="true"
                      required
                      className="rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1.5 text-sm"
                    >
                      {LLM_MODELS.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>

              {family === "overlay" && (
                <OverlayForm
                  value={overlayValues}
                  onChange={setOverlayValues}
                  supportedUnderlyings={catalog?.supported_underlyings ?? []}
                />
              )}
              {family === "earnings" && (
                <EarningsForm value={earningsValues} onChange={setEarningsValues} />
              )}
              {family === "intraday" && (
                <IntradayForm
                  value={intradayValues}
                  onChange={setIntradayValues}
                  llmChoices={LLM_MODELS.map((m) => m.id)}
                />
              )}

              {overridable.has("prompt_override") && (
                <details>
                  <summary className="cursor-pointer text-sm text-[color:var(--color-ink-soft)]">
                    Advanced (prompt override)
                  </summary>
                  <div className="mt-2 flex flex-col gap-1">
                    <textarea
                      data-testid="launch-prompt"
                      value={promptOverride}
                      onChange={(e) => setPromptOverride(e.target.value)}
                      rows={5}
                      maxLength={PROMPT_MAX}
                      placeholder="Leave blank to keep the scenario's prompt. Paste alternate instructions to steer the LLM."
                      className="w-full rounded border border-[var(--color-rule)] bg-[var(--color-paper)] p-2 font-mono text-sm"
                    />
                    <span className="text-xs text-[color:var(--color-ink-soft)]">
                      {promptOverride.length} / {PROMPT_MAX}
                    </span>
                  </div>
                </details>
              )}
            </>
          )}

          <div className="flex items-center gap-3 pt-2">
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isDisabled={submitDisabled}
              data-testid="launch-submit"
            >
              Launch run
            </Button>
            {disabled && (
              <span className="lo-mono text-[11px] uppercase tracking-[0.14em] text-[color:var(--color-ink-soft)]">
                Submitting…
              </span>
            )}
          </div>
        </form>
      </Card.Content>
    </Card>
  );
}

export default LaunchForm;
