import type { FamilyFormProps, IntradayValues } from "./types";

/**
 * `IntradayForm` — Phase D launch subform for the intraday family.
 *
 * Mirrors `IntradayOverrides` (extends LLMOverridesMixin) in
 * `liveoption_web/runs/families.py`:
 *   - `decision_interval_minutes: 1 | 5 | 15 | 30`
 *   - `max_concurrent_positions: PositiveInt`
 *   - `dte_range: tuple[int, int]`
 *   - `llm_model`, `prompt_override` (LLM-driven strategy)
 */

const INTERVALS: Array<IntradayValues["decision_interval_minutes"]> = [
  1,
  5,
  15,
  30,
];

const PROMPT_MAX = 16_384;

export interface IntradayFormProps extends FamilyFormProps<IntradayValues> {
  llmChoices?: string[];
}

export default function IntradayForm({
  value,
  onChange,
  errors,
  llmChoices = [],
}: IntradayFormProps) {
  const [dteLo, dteHi] = value.dte_range ?? [0, 7];
  const promptOverride = value.prompt_override ?? "";

  return (
    <div
      className="flex flex-col gap-4 rounded-lg border border-[var(--color-rule)] p-4"
      data-testid="intraday-form"
    >
      {/* The legacy intraday preset fixes its underlying. */}
      <div
        className="rounded-lg border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 py-2 text-xs text-[color:var(--color-ink-soft)]"
        data-testid="intraday-underlying"
      >
        <span className="font-medium text-[color:var(--color-ink)]">Underlying:</span>{" "}
        SPY <span className="ml-1 text-[10px] uppercase tracking-wider">(preset)</span>
      </div>

      <fieldset className="flex flex-col gap-1">
        <legend className="lo-eyebrow">Decision interval (minutes)</legend>
        <div
          className="flex flex-wrap gap-2"
          role="radiogroup"
          aria-label="decision interval"
        >
          {INTERVALS.map((m) => (
            <label
              key={m}
              className={[
                "cursor-pointer rounded-full border px-3 py-1 text-xs",
                value.decision_interval_minutes === m
                  ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                  : "border-[var(--color-rule)] text-[color:var(--color-ink-soft)]",
              ].join(" ")}
            >
              <input
                type="radio"
                name="intraday-interval"
                value={m}
                checked={value.decision_interval_minutes === m}
                onChange={() =>
                  onChange({ ...value, decision_interval_minutes: m })
                }
                className="sr-only"
                data-testid={`intraday-interval-${m}`}
              />
              {m}m
            </label>
          ))}
        </div>
        {errors?.decision_interval_minutes && (
          <p className="text-xs text-red-600">
            {errors.decision_interval_minutes}
          </p>
        )}
      </fieldset>

      <label className="flex flex-col gap-1 text-sm">
        <span className="lo-eyebrow">Max concurrent positions</span>
        <input
          type="number"
          min="1"
          max="50"
          data-testid="intraday-max-positions"
          value={value.max_concurrent_positions ?? ""}
          onChange={(e) =>
            onChange({
              ...value,
              max_concurrent_positions:
                e.target.value === "" ? undefined : Number(e.target.value),
            })
          }
          className="w-24 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
        />
        {errors?.max_concurrent_positions && (
          <p className="text-xs text-red-600">
            {errors.max_concurrent_positions}
          </p>
        )}
      </label>

      <div className="flex flex-col gap-1">
        <span className="lo-eyebrow">DTE range</span>
        <div className="flex items-center gap-3">
          <input
            type="number"
            min="0"
            max="365"
            data-testid="intraday-dte-lo"
            aria-label="dte range low"
            value={dteLo}
            onChange={(e) =>
              onChange({ ...value, dte_range: [Number(e.target.value), dteHi] })
            }
            className="w-20 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
          />
          <span className="text-xs text-[color:var(--color-ink-soft)]">to</span>
          <input
            type="number"
            min="0"
            max="365"
            data-testid="intraday-dte-hi"
            aria-label="dte range high"
            value={dteHi}
            onChange={(e) =>
              onChange({ ...value, dte_range: [dteLo, Number(e.target.value)] })
            }
            className="w-20 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
          />
          <span className="text-xs text-[color:var(--color-ink-soft)]">
            days
          </span>
        </div>
        {errors?.dte_range && (
          <p className="text-xs text-red-600">{errors.dte_range}</p>
        )}
      </div>

      <label className="flex flex-col gap-1 text-sm">
        <span className="lo-eyebrow">LLM model</span>
        <select
          data-testid="intraday-llm-model"
          value={value.llm_model ?? ""}
          onChange={(e) =>
            onChange({ ...value, llm_model: e.target.value || undefined })
          }
          className="rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
        >
          <option value="">(use scenario default)</option>
          {llmChoices.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="lo-eyebrow">Prompt override</span>
        <textarea
          rows={4}
          maxLength={PROMPT_MAX}
          placeholder="Leave blank for scenario default."
          data-testid="intraday-prompt"
          value={promptOverride}
          onChange={(e) =>
            onChange({
              ...value,
              prompt_override: e.target.value || undefined,
            })
          }
          className="rounded border border-[var(--color-rule)] p-2 font-mono text-sm"
        />
        <span className="text-xs text-[color:var(--color-ink-soft)]">
          {promptOverride.length} / {PROMPT_MAX}
        </span>
      </label>
    </div>
  );
}
