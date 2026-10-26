import type {
  FamilyFormProps,
  HoldingSpecValue,
  OverlayValues,
} from "./types";

/**
 * `OverlayForm` — Phase D launch subform for the overlay family.
 *
 * Mirrors `OverlayOverrides` in `liveoption_web/runs/families.py`:
 *   - `underlying_holdings: list[HoldingSpec]`  (symbol, shares, cost_basis)
 *   - `overlay_dte_range: tuple[int, int]`
 *
 * Backend strategy is `overlay_profit` (rule-based, no LLM) so this form
 * deliberately omits `llm_model` / `prompt_override` — matches the typed
 * override schema which forbids those fields for overlay.
 *
 * Symbol is a <select> sourced from supportedUnderlyings.
 * Default 2 holdings: QQQ × 10000 + NVDA × 10000.
 */

/** Paper §E.1 default holdings. */
const PAPER_OVERLAY_HOLDINGS: HoldingSpecValue[] = [
  { symbol: "QQQ", shares: 10000, cost_basis: 0 },
  { symbol: "NVDA", shares: 10000, cost_basis: 0 },
];

export interface OverlayFormProps extends FamilyFormProps<OverlayValues> {
  supportedUnderlyings: string[];
}

export default function OverlayForm({
  value,
  onChange,
  errors,
  supportedUnderlyings,
}: OverlayFormProps) {
  // Seed with paper defaults when no holdings have been set yet.
  const holdings =
    value.underlying_holdings != null
      ? value.underlying_holdings
      : PAPER_OVERLAY_HOLDINGS;

  const [dteLo, dteHi] = value.overlay_dte_range ?? [7, 45];

  const updateHolding = (idx: number, patch: Partial<HoldingSpecValue>) => {
    const next = [...holdings];
    next[idx] = { ...next[idx], ...patch };
    onChange({ ...value, underlying_holdings: next });
  };

  const addHolding = () => {
    // Default new row to first symbol not yet used, fallback to first in list or "QQQ".
    const usedSymbols = new Set(holdings.map((h) => h.symbol));
    const defaultSymbol =
      supportedUnderlyings.find((s) => !usedSymbols.has(s)) ??
      supportedUnderlyings[0] ??
      "QQQ";
    onChange({
      ...value,
      underlying_holdings: [
        ...holdings,
        { symbol: defaultSymbol, shares: "", cost_basis: "" },
      ],
    });
  };

  const removeHolding = (idx: number) => {
    const next = holdings.filter((_, i) => i !== idx);
    onChange({ ...value, underlying_holdings: next });
  };

  return (
    <div
      className="flex flex-col gap-4 rounded-lg border border-[var(--color-rule)] p-4"
      data-testid="overlay-form"
    >
      <header className="flex items-center justify-between">
        <h3 className="lo-eyebrow">Overlay holdings</h3>
        <button
          type="button"
          onClick={addHolding}
          data-testid="overlay-add-holding"
          className="rounded border border-indigo-500 px-2 py-0.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50"
        >
          + Add holding
        </button>
      </header>

      {holdings.length === 0 ? (
        <p className="text-xs text-[color:var(--color-ink-soft)]">
          No holdings yet — add at least one underlying to overlay.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {holdings.map((h, idx) => (
            <li
              key={idx}
              data-testid={`overlay-holding-${idx}`}
              className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2"
            >
              {supportedUnderlyings.length > 0 ? (
                <select
                  aria-label={`holding ${idx} symbol`}
                  value={h.symbol}
                  onChange={(e) => updateHolding(idx, { symbol: e.target.value })}
                  className="rounded border border-[var(--color-rule)] px-2 py-1 text-sm bg-[var(--color-paper)]"
                >
                  {supportedUnderlyings.map((sym) => (
                    <option key={sym} value={sym}>
                      {sym}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  placeholder="QQQ"
                  aria-label={`holding ${idx} symbol`}
                  value={h.symbol}
                  onChange={(e) =>
                    updateHolding(idx, { symbol: e.target.value.toUpperCase() })
                  }
                  className="rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
                />
              )}
              <input
                type="number"
                min="0"
                placeholder="shares"
                aria-label={`holding ${idx} shares`}
                value={h.shares}
                onChange={(e) =>
                  updateHolding(idx, {
                    shares: e.target.value === "" ? "" : Number(e.target.value),
                  })
                }
                className="rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
              />
              <input
                type="number"
                step="0.01"
                placeholder="cost basis"
                aria-label={`holding ${idx} cost basis`}
                value={h.cost_basis}
                onChange={(e) =>
                  updateHolding(idx, {
                    cost_basis:
                      e.target.value === "" ? "" : Number(e.target.value),
                  })
                }
                className="rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
              />
              <button
                type="button"
                onClick={() => removeHolding(idx)}
                aria-label={`remove holding ${idx}`}
                className="rounded border border-red-300 px-2 text-xs text-red-600 hover:bg-red-50"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      {errors?.underlying_holdings && (
        <p className="text-xs text-red-600">{errors.underlying_holdings}</p>
      )}

      <div className="flex flex-col gap-1">
        <span className="lo-eyebrow">Overlay DTE range</span>
        <div className="flex items-center gap-3">
          <input
            type="number"
            min="0"
            max="365"
            data-testid="overlay-dte-lo"
            aria-label="overlay DTE low"
            value={dteLo}
            onChange={(e) =>
              onChange({
                ...value,
                overlay_dte_range: [Number(e.target.value), dteHi],
              })
            }
            className="w-20 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
          />
          <span className="text-xs text-[color:var(--color-ink-soft)]">to</span>
          <input
            type="number"
            min="0"
            max="365"
            data-testid="overlay-dte-hi"
            aria-label="overlay DTE high"
            value={dteHi}
            onChange={(e) =>
              onChange({
                ...value,
                overlay_dte_range: [dteLo, Number(e.target.value)],
              })
            }
            className="w-20 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
          />
          <span className="text-xs text-[color:var(--color-ink-soft)]">
            days
          </span>
        </div>
        {errors?.overlay_dte_range && (
          <p className="text-xs text-red-600">{errors.overlay_dte_range}</p>
        )}
      </div>
    </div>
  );
}
