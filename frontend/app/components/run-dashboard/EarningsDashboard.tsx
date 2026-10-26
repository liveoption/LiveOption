import { MetricCard, MetricValue, pluck } from "./cards";
import { useLocale } from "../../lib/locale";

/**
 * `EarningsDashboard` — earnings family run dashboard (Phase D).
 *
 * Spec §6.2: per-event waterfall, vega contribution, win rate by ticker.
 */
export interface EarningsDashboardProps {
  familySpecific: unknown;
}

interface PerTickerRow {
  ticker?: string;
  win_rate?: number;
  pnl?: number;
}

export default function EarningsDashboard({
  familySpecific,
}: EarningsDashboardProps) {
  const {zh, ja } = useLocale();
  const f = familySpecific ?? {};

  const events = pluck(f, "event_waterfall");
  const eventList = Array.isArray(events) ? (events as Array<Record<string, unknown>>) : [];

  const perTicker = pluck(f, "per_ticker");
  const notional = pluck(f, "per_ticker_notional");
  const notionalRows: PerTickerRow[] = notional && typeof notional === "object" && !Array.isArray(notional)
    ? Object.entries(notional as Record<string, number>).map(([ticker, pnl]) => ({ ticker, pnl })) : [];
  const tickerList = notionalRows.length ? notionalRows : Array.isArray(perTicker)
    ? (perTicker as PerTickerRow[])
    : [];

  return (
    <div data-testid="earnings-dashboard" className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MetricCard label={zh ? "Vega 盈亏归因" : ja("Vega P&L attribution")} testId="earnings-vega">
          <MetricValue value={pluck(f, "vega_pnl_total") ?? pluck(f, "vega_contribution")} format="money" />
        </MetricCard>
        <MetricCard label={zh ? "Delta 盈亏归因" : ja("Delta P&L attribution")} testId="earnings-events-traded">
          <MetricValue value={pluck(f, "greeks_attribution", "delta_pnl")} format="money" />
        </MetricCard>
        <MetricCard label={zh ? "Theta 盈亏归因" : ja("Theta P&L attribution")} testId="earnings-win-rate">
          <MetricValue value={pluck(f, "greeks_attribution", "theta_pnl")} format="money" />
        </MetricCard>
      </div>

      {eventList.length > 0 && (
        <div className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950">
          <h3 className="mb-2 text-xs uppercase tracking-wide text-neutral-500">
            {zh ? "逐事件盈亏" : ja("Per-event waterfall")}
          </h3>
          <table
            data-testid="earnings-waterfall"
            className="w-full text-xs tabular-nums"
          >
            <thead>
              <tr className="text-left text-neutral-500">
                <th className="py-1">{zh ? "事件" : ja("Event")}</th>
                <th>{zh ? "入场" : ja("Entry")}</th>
                <th>{zh ? "事件时" : ja("At event")}</th>
                <th>{zh ? "出场" : ja("Exit")}</th>
                <th>PnL</th>
              </tr>
            </thead>
            <tbody>
              {eventList.map((ev, idx) => (
                <tr key={idx} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="py-1 font-mono">{String(ev.label ?? ev.ticker ?? idx)}</td>
                  <td><MetricValue value={ev.entry} format="money" /></td>
                  <td><MetricValue value={ev.at_event} format="money" /></td>
                  <td><MetricValue value={ev.exit} format="money" /></td>
                  <td><MetricValue value={ev.pnl} format="money" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tickerList.length > 0 && (
        <div className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950">
          <h3 className="mb-2 text-xs uppercase tracking-wide text-neutral-500">
            {notionalRows.length ? (zh ? "按标的成交价×数量（代理值）" : ja("Per-ticker fill price × quantity proxy")) : (zh ? "按标的胜率" : ja("Win rate by ticker"))}
          </h3>
          <ul data-testid="earnings-per-ticker" className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {tickerList.map((t, i) => (
              <li key={i} className="flex flex-col">
                <span className="font-mono">{t.ticker ?? "?"}</span>
                <MetricValue value={notionalRows.length ? t.pnl : t.win_rate} format={notionalRows.length ? "number" : "percent"} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
