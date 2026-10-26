import { MetricCard, MetricValue } from "../run-dashboard/cards";

/**
 * `IntradayView` — intraday family checkpoint detail (Phase D §6.3).
 *
 * Reads `family_view` (kind=intraday) per spec §4.1:
 *   - minutes_to_close
 *   - same_day_open_count
 *   - gamma_exposure
 *   - intraday_realized_today
 */
export interface IntradayViewProps {
  view: Record<string, unknown> | null | undefined;
}

export default function IntradayView({ view }: IntradayViewProps) {
  const v = view ?? {};
  return (
    <section
      data-testid="intraday-view"
      className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Intraday state
      </h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="Minutes to close" testId="intraday-mtc">
          <MetricValue value={v.minutes_to_close} format="number" />
        </MetricCard>
        <MetricCard label="Same-day opens" testId="intraday-same-day-opens">
          <MetricValue value={v.same_day_open_count} format="number" />
        </MetricCard>
        <MetricCard label="Gamma exposure" testId="intraday-gamma-exposure">
          <MetricValue value={v.gamma_exposure} format="number" />
        </MetricCard>
        <MetricCard label="Realized today" testId="intraday-realized-today">
          <MetricValue value={v.intraday_realized_today} format="money" />
        </MetricCard>
      </div>
    </section>
  );
}
