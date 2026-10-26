import { MetricValue } from "../run-dashboard/cards";

/**
 * `OverlayView` — overlay family checkpoint detail (Phase D §6.3).
 *
 * Reads `family_view` (kind=overlay) from the checkpoint's
 * `portfolio_summary`. Spec §4.1: underlying_holdings (with cost basis &
 * MV), overlay_legs (income, days_to_expiry), delta_hedge_status.
 */
export interface OverlayViewProps {
  view: Record<string, unknown> | null | undefined;
}

interface Holding {
  symbol?: string;
  shares?: number;
  cost_basis?: number;
  market_value?: number;
}

interface OverlayLeg {
  contract?: string;
  income?: number;
  days_to_expiry?: number;
}

export default function OverlayView({ view }: OverlayViewProps) {
  const holdings = (view?.underlying_holdings ?? []) as Holding[];
  const legs = (view?.overlay_legs ?? []) as OverlayLeg[];
  const hedgeStatus = view?.delta_hedge_status as
    | Record<string, unknown>
    | null
    | undefined;

  return (
    <section
      data-testid="overlay-view"
      className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Overlay state
      </h2>

      <div>
        <h3 className="mb-1 text-xs uppercase text-neutral-500">Underlying holdings</h3>
        {holdings.length === 0 ? (
          <p className="text-xs text-neutral-500">No underlying holdings.</p>
        ) : (
          <table className="w-full text-xs tabular-nums" data-testid="overlay-holdings">
            <thead>
              <tr className="text-left text-neutral-500">
                <th className="py-1">Symbol</th>
                <th>Shares</th>
                <th>Cost basis</th>
                <th>Market value</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h, i) => (
                <tr key={i} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="py-1 font-mono">{h.symbol ?? "?"}</td>
                  <td><MetricValue value={h.shares} format="number" /></td>
                  <td><MetricValue value={h.cost_basis} format="money" /></td>
                  <td><MetricValue value={h.market_value} format="money" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div>
        <h3 className="mb-1 text-xs uppercase text-neutral-500">Overlay legs</h3>
        {legs.length === 0 ? (
          <p className="text-xs text-neutral-500">No active overlay legs.</p>
        ) : (
          <ul data-testid="overlay-legs" className="flex flex-col gap-1 text-xs">
            {legs.map((l, i) => (
              <li key={i} className="flex justify-between font-mono">
                <span>{l.contract ?? "?"}</span>
                <span>
                  income <MetricValue value={l.income} format="money" /> · DTE{" "}
                  <MetricValue value={l.days_to_expiry} format="number" />
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {hedgeStatus && (
        <div>
          <h3 className="mb-1 text-xs uppercase text-neutral-500">
            Delta hedge status
          </h3>
          <pre
            data-testid="overlay-hedge-status"
            className="max-h-40 overflow-auto rounded bg-neutral-100 p-2 text-xs dark:bg-neutral-900"
          >
            {JSON.stringify(hedgeStatus, null, 2)}
          </pre>
        </div>
      )}
    </section>
  );
}
