import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * Single PnL-series point. Mirrors the backend output of
 * `liveoption_web/runs/service.py::get_pnl_series` — each `day.completed`
 * event is projected onto `{trading_day, cum_pnl, portfolio_value}`.
 */
export interface PnLPoint {
  trading_day?: string | null;
  cum_pnl?: number | null;
  portfolio_value?: number | null;
}

export interface PnLChartProps {
  points: PnLPoint[];
  /** Optional fixed height (px). Defaults to 240. */
  height?: number;
}

/**
 * Cumulative-PnL line chart rendered with Recharts.
 *
 * Pure: callers pass the raw series from
 * `GET /v1/runs/{run_id}/pnl`. We tolerate missing points (`cum_pnl=null`
 * becomes `undefined` so Recharts skips the segment) rather than crashing
 * when a run is still in progress.
 */
export default function PnLChart({ points, height = 240 }: PnLChartProps) {
  if (!points || points.length === 0) {
    return (
      <div
        data-testid="pnl-chart-empty"
        className="flex items-center justify-center rounded-md border border-dashed border-neutral-300 p-6 text-sm text-neutral-500 dark:border-neutral-700"
        style={{ height }}
      >
        No PnL data yet.
      </div>
    );
  }

  const data = points.map((p) => ({
    trading_day: p.trading_day ?? "",
    cum_pnl: typeof p.cum_pnl === "number" ? p.cum_pnl : undefined,
    portfolio_value:
      typeof p.portfolio_value === "number" ? p.portfolio_value : undefined,
  }));

  const last = data[data.length - 1]?.cum_pnl;
  const ariaLabel =
    typeof last === "number"
      ? `Cumulative PnL chart with ${data.length} points, latest ${last.toFixed(2)}`
      : `Cumulative PnL chart with ${data.length} points`;

  return (
    <div
      data-testid="pnl-chart"
      role="img"
      aria-label={ariaLabel}
      style={{ width: "100%", height }}
    >
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-neutral-200 dark:stroke-neutral-800" />
          <XAxis
            dataKey="trading_day"
            tick={{ fontSize: 11 }}
            minTickGap={24}
          />
          <YAxis
            tick={{ fontSize: 11 }}
            width={60}
            tickFormatter={(v: number) =>
              typeof v === "number"
                ? v.toLocaleString("en-US", { notation: "compact" })
                : String(v)
            }
          />
          <Tooltip
            formatter={(v: unknown) =>
              typeof v === "number"
                ? v.toLocaleString("en-US", { maximumFractionDigits: 2 })
                : String(v ?? "—")
            }
          />
          <Line
            type="monotone"
            dataKey="cum_pnl"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
