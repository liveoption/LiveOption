import type { EarningsValues, FamilyFormProps } from "./types";

/**
 * `EarningsForm` — Phase D launch subform for the earnings family.
 *
 * Mirrors `EarningsOverrides` in `liveoption_web/runs/families.py`:
 *   - `tickers: list[str]` — chip-style multi-select
 *   - `event_window: tuple[int, int]` (defaults [-2, 2])
 *   - `quarter: Literal["2025Q1", ..., "2025Q4"]`
 *
 * Backend strategy is `earnings_bet` (rule-based, no LLM) — no LLM fields.
 *
 * The preset ticker list is grouped
 * by sector for visual clarity. Default selection = all 24.
 */

/** Paper §E.2 Table 5 — 24 earnings tickers grouped by sector. */
const EARNINGS_PAPER_TICKERS_BY_SECTOR = [
  { sector: "Mega-cap Tech",        tickers: ["AAPL", "MSFT", "TSLA", "AMZN", "META", "NVDA"] },
  { sector: "Semiconductor",        tickers: ["AMD", "INTC", "MU", "AVGO"] },
  { sector: "Consumer & Streaming", tickers: ["NFLX", "DIS", "NKE"] },
  { sector: "Cloud / SaaS",         tickers: ["CRM", "SNOW", "PLTR"] },
  { sector: "Fintech & Crypto-adj", tickers: ["HOOD", "COIN", "SOFI", "PYPL"] },
  { sector: "High Beta / Growth",   tickers: ["SHOP", "RKLB", "SMCI", "CELH"] },
] as const;

export const EARNINGS_PAPER_TICKERS: string[] =
  EARNINGS_PAPER_TICKERS_BY_SECTOR.flatMap((s) => [...s.tickers]);
// 6+4+3+3+4+4 = 24 tickers

const QUARTERS: Array<EarningsValues["quarter"]> = [
  "2025Q1",
  "2025Q2",
  "2025Q3",
  "2025Q4",
];

export default function EarningsForm({
  value,
  onChange,
  errors,
}: FamilyFormProps<EarningsValues>) {
  // Default selection = all 24 paper tickers.
  const selected: string[] = value.tickers ?? EARNINGS_PAPER_TICKERS;
  const [winLo, winHi] = value.event_window ?? [-2, 2];

  const toggleTicker = (ticker: string) => {
    const next = selected.includes(ticker)
      ? selected.filter((t) => t !== ticker)
      : [...selected, ticker];
    onChange({ ...value, tickers: next });
  };

  const selectAll = () => onChange({ ...value, tickers: [...EARNINGS_PAPER_TICKERS] });
  const clearAll = () => onChange({ ...value, tickers: [] });
  const resetToPaper = () => onChange({ ...value, tickers: [...EARNINGS_PAPER_TICKERS] });

  return (
    <div
      className="flex flex-col gap-4 rounded-lg border border-[var(--color-rule)] p-4"
      data-testid="earnings-form"
    >
      {/* Chip multi-select grouped by sector */}
      <div className="flex flex-col gap-3">
        <span className="lo-eyebrow">
          Tickers — {selected.length} / {EARNINGS_PAPER_TICKERS.length} selected
        </span>

        {EARNINGS_PAPER_TICKERS_BY_SECTOR.map(({ sector, tickers }) => (
          <fieldset
            key={sector}
            className="border-t border-[var(--color-rule)] pt-3"
          >
            <legend className="text-xs uppercase tracking-wide text-[color:var(--color-ink-soft)]">
              {sector}
            </legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {tickers.map((t) => {
                const isSelected = selected.includes(t);
                return (
                  <button
                    type="button"
                    key={t}
                    onClick={() => toggleTicker(t)}
                    aria-pressed={isSelected}
                    data-testid={`earnings-chip-${t}`}
                    className={
                      "rounded-full border px-3 py-1 text-xs " +
                      (isSelected
                        ? "border-[var(--color-accent)] bg-[var(--color-accent)]/10 text-[color:var(--color-accent)]"
                        : "border-[var(--color-rule)] text-[color:var(--color-ink-soft)] hover:bg-[var(--color-paper-2)]")
                    }
                  >
                    {t}
                  </button>
                );
              })}
            </div>
          </fieldset>
        ))}

        {/* Utility buttons */}
        <div className="flex gap-3 pt-1">
          <button
            type="button"
            onClick={selectAll}
            data-testid="earnings-select-all"
            className="text-xs text-[color:var(--color-accent)] hover:underline"
          >
            Select all (24)
          </button>
          <button
            type="button"
            onClick={clearAll}
            data-testid="earnings-clear-all"
            className="text-xs text-[color:var(--color-ink-soft)] hover:underline"
          >
            Clear all
          </button>
          <button
            type="button"
            onClick={resetToPaper}
            data-testid="earnings-reset"
            className="text-xs text-[color:var(--color-ink-soft)] hover:underline"
          >
            Reset to paper
          </button>
        </div>

        {errors?.tickers && (
          <p className="text-xs text-red-600">{errors.tickers}</p>
        )}
      </div>

      {/* Hidden textarea kept for backwards-compat with existing tests */}
      <textarea
        rows={1}
        placeholder="AAPL, MSFT, NVDA"
        data-testid="earnings-tickers"
        value={selected.join(", ")}
        onChange={(e) => {
          const parsed = e.target.value
            .split(/[,\s]+/)
            .map((t) => t.trim().toUpperCase())
            .filter(Boolean);
          onChange({ ...value, tickers: parsed });
        }}
        className="sr-only"
        aria-hidden="true"
      />

      <div className="flex flex-col gap-1">
        <span className="lo-eyebrow">Event window (days from earnings)</span>
        <div className="flex items-center gap-3">
          <input
            type="number"
            data-testid="earnings-window-lo"
            aria-label="event window low"
            value={winLo}
            onChange={(e) =>
              onChange({
                ...value,
                event_window: [Number(e.target.value), winHi],
              })
            }
            className="w-20 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
          />
          <span className="text-xs text-[color:var(--color-ink-soft)]">to</span>
          <input
            type="number"
            data-testid="earnings-window-hi"
            aria-label="event window high"
            value={winHi}
            onChange={(e) =>
              onChange({
                ...value,
                event_window: [winLo, Number(e.target.value)],
              })
            }
            className="w-20 rounded border border-[var(--color-rule)] px-2 py-1 text-sm"
          />
        </div>
        {errors?.event_window && (
          <p className="text-xs text-red-600">{errors.event_window}</p>
        )}
      </div>

      <fieldset className="flex flex-col gap-1">
        <legend className="lo-eyebrow">Quarter</legend>
        <div
          className="flex flex-wrap gap-2"
          role="radiogroup"
          aria-label="quarter"
        >
          {QUARTERS.map((q) => (
            <label
              key={q}
              className={[
                "cursor-pointer rounded-full border px-3 py-1 text-xs",
                value.quarter === q
                  ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                  : "border-[var(--color-rule)] text-[color:var(--color-ink-soft)]",
              ].join(" ")}
            >
              <input
                type="radio"
                name="earnings-quarter"
                value={q}
                checked={value.quarter === q}
                onChange={() => onChange({ ...value, quarter: q })}
                className="sr-only"
                data-testid={`earnings-quarter-${q}`}
              />
              {q}
            </label>
          ))}
        </div>
        {errors?.quarter && (
          <p className="text-xs text-red-600">{errors.quarter}</p>
        )}
      </fieldset>
    </div>
  );
}
