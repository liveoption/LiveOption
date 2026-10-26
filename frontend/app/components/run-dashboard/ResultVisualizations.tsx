import { useLocale } from "../../lib/locale";
import { useState } from "react";

export type ResultPoint = { trading_day: string; portfolio_value: number | null; daily_pnl: number | null; cum_pnl: number | null; drawdown_pct: number | null; decisions: number; trade_proposals: number };
type Metric = "portfolio_value" | "cum_pnl" | "daily_pnl" | "drawdown_pct";
export default function ResultVisualizations({ points, activity = [], zh }: { points: ResultPoint[]; activity?: { trading_day: string; decisions: number; trade_proposals: number }[]; zh: boolean }) {
  const { ja } = useLocale();
  const [metric, setMetric] = useState<Metric>("portfolio_value");
  const [selected, setSelected] = useState<string>();
  const [page, setPage] = useState(0);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const labels: Record<Metric, string> = { portfolio_value: zh ? "账户权益" : ja("Account equity"), cum_pnl: zh ? "累计盈亏" : ja("Cumulative P&L"), daily_pnl: zh ? "每日盈亏" : ja("Daily P&L"), drawdown_pct: zh ? "日终权益回撤" : ja("Daily-equity drawdown") };
  const filtered = points.filter((p) => (!start || p.trading_day >= start) && (!end || p.trading_day <= end));
  const valid = filtered.filter((p) => typeof p[metric] === "number" && Number.isFinite(p[metric]));
  const values = valid.map((p) => p[metric] as number);
  const low = Math.min(...values, ...(metric === "portfolio_value" ? [] : [0]));
  const high = Math.max(...values, ...(metric === "portfolio_value" ? [] : [0]));
  const span = high - low || Math.max(Math.abs(high) * 0.01, 1);
  const x = (i: number) => 72 + i / Math.max(filtered.length - 1, 1) * 770;
  const y = (v: number) => 208 - (v - low) / span * 160;
  const format = (v: number | null, percent = false) => v === null ? (zh ? "未记录" : ja("Not recorded")) : percent ? `${(v * 100).toFixed(2)}%` : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(v);
  const segments: string[] = [];
  let segment = "";
  filtered.forEach((p, i) => { const v = p[metric]; if (v === null || !Number.isFinite(v)) { if (segment) segments.push(segment); segment = ""; } else segment += `${x(i)},${y(v)} `; });
  if (segment) segments.push(segment);
  const active = filtered.find((p) => p.trading_day === selected) ?? valid[valid.length - 1];
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const currentPage = Math.min(page, pages - 1);
  const activityFiltered = activity.filter((p) => (!start || p.trading_day >= start) && (!end || p.trading_day <= end));
  const activityMax = Math.max(1, ...activityFiltered.map((p) => p.decisions));
  return <section className="mt-6 space-y-4" data-testid="result-visualizations">
    <div className="flex flex-wrap items-end justify-between gap-3"><h3 className="font-semibold">{zh ? "结果图表" : ja("Result charts")}</h3><div className="flex flex-wrap gap-3 text-xs"><label className="lo-field"><span>{zh ? "从" : ja("From")}</span><input aria-label={zh ? "图表开始日期" : ja("Chart start date")} type="date" value={start} onChange={(e) => { setStart(e.target.value); setPage(0); }} /></label><label className="lo-field"><span>{zh ? "至" : ja("To")}</span><input aria-label={zh ? "图表结束日期" : ja("Chart end date")} type="date" value={end} onChange={(e) => { setEnd(e.target.value); setPage(0); }} /></label></div></div>
    <div className="flex flex-wrap gap-2" role="tablist" aria-label={zh ? "结果图表指标" : ja("Result chart metric")}>{(Object.keys(labels) as Metric[]).map((key) => <button type="button" role="tab" key={key} aria-selected={metric === key} onClick={() => setMetric(key)} className={`rounded-lg border px-3 py-2 text-sm ${metric === key ? "border-blue-600 bg-blue-50 text-blue-800" : "border-[var(--color-rule)]"}`}>{labels[key]}</button>)}</div>
    <div className="rounded-xl border border-[var(--color-rule)] p-3">
      {!valid.length ? <p className="p-6 text-sm text-[var(--color-ink-soft)]">{zh ? "该日期范围暂无完整交易日数据。" : ja("No completed-day observations in this range.")}</p> : <>
        <p aria-live="polite" className="min-h-6 text-sm tabular-nums">{active?.trading_day} · {labels[metric]} {format(active?.[metric] ?? null, metric === "drawdown_pct")}</p>
        <svg viewBox="0 0 880 250" role="group" aria-label={labels[metric]} className="w-full overflow-visible">
          {[0, 0.5, 1].map((part) => { const value = low + span * part; return <g key={part}><line x1="72" y1={y(value)} x2="842" y2={y(value)} stroke="#d9dee7" /><text x="64" y={y(value) + 4} textAnchor="end" fontSize="11" fill="currentColor">{metric === "drawdown_pct" ? `${(value * 100).toFixed(1)}%` : new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value)}</text></g>; })}
          {metric !== "daily_pnl" && segments.map((p, i) => <polyline key={i} points={p} fill="none" stroke="#245db5" strokeWidth="2" />)}
          {filtered.map((p, i) => { const v = p[metric]; if (v === null || !Number.isFinite(v)) return null; return <g key={`${p.trading_day}-${i}`}>
            {metric === "daily_pnl" && <rect x={x(i) - 3} y={Math.min(y(v), y(0))} width="6" height={Math.max(1, Math.abs(y(v) - y(0)))} fill={v < 0 ? "#d36b5e" : "#439b7e"} />}
            <circle role="button" tabIndex={0} aria-label={`${p.trading_day}: ${format(v, metric === "drawdown_pct")}`} cx={x(i)} cy={y(v)} r={active === p ? 5 : 3} fill="#245db5" onMouseEnter={() => setSelected(p.trading_day)} onFocus={() => setSelected(p.trading_day)} onClick={() => setSelected(p.trading_day)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(p.trading_day); } }}><title>{p.trading_day}: {format(v, metric === "drawdown_pct")}</title></circle>
          </g>; })}
          <text x="72" y="238" fontSize="11" fill="currentColor">{filtered[0]?.trading_day}</text><text x="842" y="238" textAnchor="end" fontSize="11" fill="currentColor">{filtered[filtered.length - 1]?.trading_day}</text>
        </svg>
      </>}
    </div>
    <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "金额单位：美元。回撤按已完成交易日的权益高点计算；缺失数据不连线。点击或键盘选中节点查看数值。" : ja("Amounts in USD. Drawdown uses completed-day equity peaks; missing values are not joined. Select points with mouse or keyboard.")}</p>
    {activityFiltered.length > 0 && <div className="rounded-xl border border-[var(--color-rule)] p-4"><h4 className="text-sm font-medium">{zh ? "决策活动 · 蓝色为全部决策，绿色为有交易意图的决策" : ja("Decision activity · blue = all decisions, green = trade proposals")}</h4><svg viewBox="0 0 880 130" role="img" aria-label={zh ? "每日决策活动" : ja("Decisions by day")} className="mt-2 w-full">{activityFiltered.map((p, i) => { const barWidth = Math.max(1, Math.min(14, 740 / activityFiltered.length)); const xpos = 72 + i / Math.max(1, activityFiltered.length - 1) * 770; return <g key={p.trading_day}><rect x={xpos - barWidth / 2} y={95 - p.decisions / activityMax * 80} width={barWidth} height={p.decisions / activityMax * 80} fill="#245db5"><title>{p.trading_day}: {p.decisions} {zh ? "决策" : ja("decisions")}, {p.trade_proposals} {zh ? "交易意图" : ja("proposals")}</title></rect><rect x={xpos - barWidth / 2} y={95 - p.trade_proposals / activityMax * 80} width={barWidth} height={p.trade_proposals / activityMax * 80} fill="#439b7e" /></g>; })}<text x="64" y="18" fontSize="11" textAnchor="end" fill="currentColor">{activityMax}</text><text x="72" y="120" fontSize="11" fill="currentColor">{activityFiltered[0].trading_day}</text><text x="842" y="120" textAnchor="end" fontSize="11" fill="currentColor">{activityFiltered[activityFiltered.length - 1].trading_day}</text></svg></div>}
    <details><summary className="cursor-pointer text-sm font-medium">{zh ? "查看每日明细" : ja("Daily results table")} ({filtered.length})</summary><div className="mt-3 overflow-x-auto"><table className="w-full whitespace-nowrap text-left text-xs"><thead><tr>{[zh ? "交易日（ET）" : ja("Trading day (ET)"), labels.portfolio_value, labels.daily_pnl, labels.cum_pnl, labels.drawdown_pct, zh ? "决策 / 交易意图" : ja("Decisions / proposals")].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead><tbody>{filtered.slice(currentPage * 20, currentPage * 20 + 20).map((p, i) => <tr key={i} className="border-t border-[var(--color-rule)]"><td className="p-2">{p.trading_day}</td><td>{format(p.portfolio_value)}</td><td>{format(p.daily_pnl)}</td><td>{format(p.cum_pnl)}</td><td>{format(p.drawdown_pct, true)}</td><td>{p.decisions} / {p.trade_proposals}</td></tr>)}</tbody></table></div><div className="mt-3 flex items-center justify-between text-sm"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{zh ? "上一页" : ja("Previous page")}</button><span>{currentPage + 1} / {pages}</span><button type="button" disabled={currentPage >= pages - 1} onClick={() => setPage(currentPage + 1)}>{zh ? "下一页" : ja("Next page")}</button></div></details>
  </section>;
}
