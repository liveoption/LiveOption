import { MetricValue } from "../run-dashboard/cards";

/**
 * `EarningsView` — earnings family checkpoint detail (Phase D §6.3).
 *
 * Reads `family_view` (kind=earnings) per spec §4.1:
 *   - active_event_windows (with phase: pre / in / post)
 *   - pending_events
 *   - post_event_realized
 */
export interface EarningsViewProps {
  view: Record<string, unknown> | null | undefined;
}

interface EventRow {
  ticker?: string;
  event_date?: string;
  phase?: "pre" | "in" | "post" | string;
}

export default function EarningsView({ view }: EarningsViewProps) {
  const active = (view?.active_event_windows ?? []) as EventRow[];
  const pending = (view?.pending_events ?? []) as EventRow[];
  const postRealized = view?.post_event_realized;

  return (
    <section
      data-testid="earnings-view"
      className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Earnings state
      </h2>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="Active event windows" value={active.length} />
        <Stat label="Pending events" value={pending.length} />
        <div className="rounded border border-neutral-200 p-2 dark:border-neutral-800">
          <span className="text-xs uppercase text-neutral-500">
            Post-event realized
          </span>
          <div className="text-sm">
            <MetricValue value={postRealized} format="money" />
          </div>
        </div>
      </div>

      {active.length > 0 && (
        <div>
          <h3 className="mb-1 text-xs uppercase text-neutral-500">
            Active windows
          </h3>
          <ul data-testid="earnings-active" className="flex flex-col gap-1 text-xs font-mono">
            {active.map((ev, i) => (
              <li key={i} className="flex justify-between">
                <span>
                  {ev.ticker ?? "?"} — {ev.event_date ?? "?"}
                </span>
                <PhasePill phase={ev.phase} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {pending.length > 0 && (
        <div>
          <h3 className="mb-1 text-xs uppercase text-neutral-500">
            Pending
          </h3>
          <ul data-testid="earnings-pending" className="flex flex-col gap-1 text-xs font-mono">
            {pending.map((ev, i) => (
              <li key={i} className="flex justify-between">
                <span>{ev.ticker ?? "?"}</span>
                <span>{ev.event_date ?? "?"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded border border-neutral-200 p-2 dark:border-neutral-800">
      <span className="text-xs uppercase text-neutral-500">{label}</span>
      <div className="text-sm font-mono">{value}</div>
    </div>
  );
}

function PhasePill({ phase }: { phase: string | undefined }) {
  const cls =
    phase === "pre"
      ? "bg-amber-100 text-amber-800"
      : phase === "in"
        ? "bg-sky-100 text-sky-800"
        : phase === "post"
          ? "bg-emerald-100 text-emerald-800"
          : "bg-neutral-200 text-neutral-700";
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase ${cls}`}>
      {phase ?? "?"}
    </span>
  );
}
