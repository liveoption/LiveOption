import { useLocale } from "../../lib/locale";
import { MetricCard, MetricValue, pluck } from "./cards";

export interface IntradayDashboardProps { familySpecific: unknown }

export default function IntradayDashboard({ familySpecific }: IntradayDashboardProps) {
  const {zh, ja } = useLocale();
  const f = familySpecific ?? {};
  const histogram = pluck(f, "hold_time_histogram");
  const bins = Array.isArray(histogram) ? histogram as Array<{ bucket?: string; count?: number }> : [];
  const maxCount = bins.reduce((max, bin) => Math.max(max, typeof bin.count === "number" ? bin.count : 0), 0);
  return <div data-testid="intraday-dashboard" className="flex flex-col gap-4">
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <MetricCard label={zh ? "每日成交笔数" : ja("Fills per day")} testId="intraday-efficiency">
        <MetricValue value={pluck(f, "trades_per_day") ?? pluck(f, "scalping_efficiency")} format="number" />
      </MetricCard>
      <MetricCard label={zh ? "Gamma 盈亏" : ja("Gamma P&L")} testId="intraday-hold-avg">
        <MetricValue value={pluck(f, "gamma_pnl_total")} format="money" />
      </MetricCard>
      <MetricCard label={zh ? "Theta 盈亏" : ja("Theta P&L")} testId="intraday-round-trips">
        <MetricValue value={pluck(f, "theta_pnl_total")} format="money" />
      </MetricCard>
      <MetricCard label={zh ? "Delta 盈亏" : ja("Delta P&L")} testId="intraday-gamma">
        <MetricValue value={pluck(f, "greeks_attribution", "delta_pnl")} format="money" />
      </MetricCard>
      <MetricCard label={zh ? "Vega 盈亏" : ja("Vega P&L")} testId="intraday-realized">
        <MetricValue value={pluck(f, "greeks_attribution", "vega_pnl")} format="money" />
      </MetricCard>
      <MetricCard label={zh ? "归因残差" : ja("Attribution residual")} testId="intraday-decisions-per-day">
        <MetricValue value={pluck(f, "greeks_attribution", "residual")} format="money" />
      </MetricCard>
    </div>
    {bins.length > 0 && <div className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950">
      <h3 className="mb-2 text-xs uppercase tracking-wide text-neutral-500">{zh ? "持仓时间分布" : ja("Hold-time histogram")}</h3>
      <ul data-testid="intraday-histogram" className="flex flex-col gap-1 text-xs">
        {bins.map((bin, index) => {
          const count = typeof bin.count === "number" ? bin.count : 0;
          const width = maxCount > 0 ? count / maxCount * 100 : 0;
          return <li key={index} className="flex items-center gap-2">
            <span className="w-20 font-mono text-neutral-500">{bin.bucket ?? "?"}</span>
            <span className="relative h-2 flex-1 overflow-hidden rounded bg-neutral-100 dark:bg-neutral-800"><span className="absolute left-0 top-0 h-full bg-indigo-400" style={{ width: `${width}%` }} /></span>
            <span className="w-10 text-right font-mono">{count}</span>
          </li>;
        })}
      </ul>
    </div>}
  </div>;
}
