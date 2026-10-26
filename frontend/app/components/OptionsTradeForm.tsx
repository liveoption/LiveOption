import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@heroui/react";
import { useListScenariosV1ScenariosGet } from "../lib/orval/catalog/catalog";
import type { LaunchFormPayload } from "./LaunchForm";

const SCENARIO_ID = "intraday_spy_2025_q1";
const MODEL_LABELS: Record<string, string> = {
  "gemini-3-flash": "Gemini 3 Flash · Anthropic gateway",
  "gpt-5.6-terra": "GPT-5.6 Terra · OpenAI gateway",
  "claude-sonnet-4-5": "Claude Sonnet 4.5",
  "deepseek-chat": "DeepSeek Chat",
  "gemini-2.5-flash": "Gemini 2.5 Flash",
  "gpt-4.1-mini": "GPT-4.1 Mini",
  "qwen2.5-max": "Qwen 2.5 Max",
};

type Catalog = {
  items?: Array<{ id: string }>;
  llm_model_choices?: string[];
};

export function OptionsTradeForm({
  onSubmit,
  disabled = false,
}: {
  onSubmit: (payload: LaunchFormPayload) => void;
  disabled?: boolean;
}) {
  const catalogQuery = useListScenariosV1ScenariosGet();
  const catalog = catalogQuery.data?.status === 200
    ? catalogQuery.data.data as Catalog
    : null;
  const available = catalog?.items?.some((item) => item.id === SCENARIO_ID) ?? false;
  const models = useMemo(() => {
    const choices = catalog?.llm_model_choices ?? [];
    return Object.keys(MODEL_LABELS).filter((id) => choices.includes(id));
  }, [catalog?.llm_model_choices]);

  const [title, setTitle] = useState("");
  const [start, setStart] = useState("2025-01-06");
  const [end, setEnd] = useState("2025-01-06");
  const [bankroll, setBankroll] = useState("25000");
  const [model, setModel] = useState("gemini-3-flash");
  const [context, setContext] = useState("");
  const [interval, setInterval] = useState(15);
  const [maxPositions, setMaxPositions] = useState(5);
  const [dteMin, setDteMin] = useState(0);
  const [dteMax, setDteMax] = useState(7);

  const effectiveModel = models.includes(model) ? model : models[0];
  const valid = available && !!effectiveModel && start <= end &&
    Number(bankroll) >= 1000 && Number(bankroll) < 10_000_000 &&
    Number.isInteger(maxPositions) && maxPositions >= 1 && maxPositions <= 50 &&
    Number.isInteger(dteMin) && Number.isInteger(dteMax) &&
    dteMin >= 0 && dteMin <= dteMax && dteMax <= 365;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!valid || !effectiveModel) return;
    const payload: LaunchFormPayload = {
      scenario_id: SCENARIO_ID,
      overrides: {
        start,
        end,
        bankroll: Number(bankroll),
        llm_model: effectiveModel,
        decision_interval_minutes: interval,
        max_concurrent_positions: maxPositions,
        dte_range: [dteMin, dteMax],
        ...(context.trim() ? { prompt_override: context.trim() } : {}),
      },
    };
    if (title.trim()) payload.title = title.trim();
    onSubmit(payload);
  };

  return (
    <form onSubmit={submit} data-testid="options-trade-form" className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-5">
        <section className="lo-panel p-6">
          <div className="mb-5 flex items-start justify-between gap-4 border-b border-[var(--color-rule)] pb-4">
            <div>
              <p className="lo-eyebrow">01 / Market</p>
              <h2 className="mt-2 text-lg font-semibold">SPY options</h2>
              <p className="mt-1 text-sm text-[var(--color-ink-soft)]">Historical replay · option contracts only · no live orders</p>
            </div>
            <span className="lo-status">REPLAY</span>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Run name" className="sm:col-span-3">
              <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="My SPY options test" />
            </Field>
            <Field label="From">
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
            </Field>
            <Field label="To">
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} required />
            </Field>
            <Field label="Starting cash · USD">
              <input type="number" min={1000} max={9999999} step={100} value={bankroll} onChange={(e) => setBankroll(e.target.value)} required />
            </Field>
          </div>
          {start > end && <p className="mt-3 text-sm text-red-600">End date must be on or after start date.</p>}
        </section>

        <section className="lo-panel p-6">
          <p className="lo-eyebrow">02 / Your context</p>
          <h2 className="mt-2 text-lg font-semibold">Design the trading context</h2>
          <p className="mt-1 text-sm text-[var(--color-ink-soft)]">Describe your thesis, entry criteria, exit preferences, and what the model should watch. This is added to every decision alongside market data; risk rules remain in force.</p>
          <textarea
            aria-label="Trading context"
            data-testid="options-context"
            className="mt-4 min-h-56 w-full resize-y"
            maxLength={16000}
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder="Example: Only open a position when the option spread is tight and the recent SPY price trend agrees with the direction. Prefer holding cash when evidence is mixed."
          />
          <div className="mt-2 text-right font-mono text-xs text-[var(--color-ink-soft)]">{context.length} / 16000</div>
        </section>
      </div>

      <aside className="space-y-5">
        <section className="lo-panel p-6">
          <p className="lo-eyebrow">03 / Settings</p>
          <h2 className="mt-2 text-lg font-semibold">Decision controls</h2>
          <div className="mt-5 space-y-4">
            <Field label="Model">
              <select data-testid="options-model" value={effectiveModel ?? ""} onChange={(e) => setModel(e.target.value)} disabled={!models.length}>
                {models.map((id) => <option key={id} value={id}>{MODEL_LABELS[id]}</option>)}
              </select>
            </Field>
            <Field label="Decision interval">
              <select value={interval} onChange={(e) => setInterval(Number(e.target.value))}>
                {[1, 5, 15, 30].map((n) => <option key={n} value={n}>Every {n} min</option>)}
              </select>
            </Field>
            <Field label="Max open positions">
              <input type="number" min={1} max={50} value={maxPositions} onChange={(e) => setMaxPositions(Number(e.target.value))} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Min DTE"><input type="number" min={0} max={365} value={dteMin} onChange={(e) => setDteMin(Number(e.target.value))} /></Field>
              <Field label="Max DTE"><input type="number" min={0} max={365} value={dteMax} onChange={(e) => setDteMax(Number(e.target.value))} /></Field>
            </div>
          </div>
        </section>
        <section className="lo-panel p-6">
          <p className="lo-eyebrow">RUN SUMMARY</p>
          <p className="mt-3 text-sm text-[var(--color-ink-soft)]">SPY option chain · {start === end ? start : `${start} → ${end}`} · ${Number(bankroll || 0).toLocaleString()} starting cash</p>
          <p className="mt-2 text-xs text-[var(--color-ink-soft)]">Historical simulation. No brokerage connection or live execution.</p>
          <Button type="submit" variant="primary" className="mt-5 w-full !bg-[var(--color-primary-600)] !text-white hover:!bg-[var(--color-primary-700)]" isDisabled={disabled || !valid} data-testid="options-launch-submit">
            {disabled ? "Starting…" : "Start options run"}
          </Button>
          {!catalogQuery.isLoading && !available && <p className="mt-3 text-xs text-red-600">Options scenario is unavailable in this deployment.</p>}
        </section>
      </aside>
    </form>
  );
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return <label className={`lo-field ${className}`}><span>{label}</span>{children}</label>;
}
