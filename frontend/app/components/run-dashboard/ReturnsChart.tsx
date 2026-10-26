import { useEffect, useMemo, useState } from "react";
import { useLocale } from "../../lib/locale";

type Point = { date: string; value: number | null; return_pct: number | null; index_value: number | null };
export type ReturnSeries = { id: string; label: string; kind: "account" | "underlying"; baseline_value: number; points: Point[] };
type Returns = { baseline_date: string | null; comparison_end: string | null; series: ReturnSeries[]; unavailable: string[]; provisional: boolean; warnings: string[] };
const palette = ["#245db5", "#cb7925", "#16846c", "#8b5bb5", "#cb4764", "#3986a5", "#776b20", "#b04a8b", "#4c8061", "#6854a0", "#a35a42", "#327e85"];
export default function ReturnsChart({ runId, universe, status }: { runId: string; universe: string[]; status?: string | null }) {
  const {zh, ja } = useLocale();
  const [selected, setSelected] = useState<string[]>([]);
  const [account, setAccount] = useState(true);
  const [layout, setLayout] = useState<"overlay" | "facets">("overlay");
  const [axis, setAxis] = useState<"return_pct" | "index_value">("return_pct");
  const [data, setData] = useState<Returns | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tablePage, setTablePage] = useState(0);
  const symbolsKey = universe.join(",");
  useEffect(() => { setSelected(symbolsKey.split(",").filter(Boolean)); setAccount(true); setData(null); }, [runId, symbolsKey]);
  const selectedKey = selected.join(",");
  useEffect(() => {
    if (!selectedKey && !account) { setData(null); setLoading(false); return; }
    let active = true;
    const controller = new AbortController();
    const read = async () => {
      setLoading(true); setError("");
      try {
        const query = new URLSearchParams({ symbols: selectedKey, account: String(account) });
        const response = await fetch(`/api/tasks/${encodeURIComponent(runId)}/returns?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const value = await response.json();
        if (active) { setData(value); setTablePage(0); }
      } catch (cause) { if (active && !controller.signal.aborted) setError(String(cause)); }
      finally { if (active) setLoading(false); }
    };
    setData(null); read();
    const timer = ["running", "pending"].includes(status ?? "") ? window.setInterval(read, 15000) : undefined;
    return () => { active = false; controller.abort(); if (timer) window.clearInterval(timer); };
  }, [runId, selectedKey, account, status]);
  const name = (series: ReturnSeries) => series.kind === "account" ? (zh ? "账户" : ja("Account")) : series.label;
  const color = (series: ReturnSeries) => series.kind === "account" ? "var(--color-ink)" : palette[Math.max(0, universe.indexOf(series.id)) % palette.length];
  const dates = data?.series[0]?.points.map((point) => point.date) ?? [];
  const pageCount = Math.max(1, Math.ceil(dates.length / 20));
  const page = Math.min(tablePage, pageCount - 1);
  return <section className="lo-panel min-w-0 space-y-4 p-5" data-testid="returns-chart">
    <header><p className="lo-eyebrow">QFINZERO / RETURN COMPARISON</p><h2 className="mt-1 text-lg font-semibold">{zh ? "标的与账户收益率" : ja("Asset and account returns")}</h2><p className="mt-2 text-xs text-[var(--color-ink-soft)]">{zh ? "全回放期间的日终对比；账户曲线基于已记录的日终权益。" : ja("Daily-close comparison over the replay; the account curve uses recorded end-of-day equity.")}</p></header>
    <fieldset className="flex flex-wrap gap-x-5 gap-y-2 rounded-xl border border-[var(--color-rule)] p-3 text-sm"><legend className="px-1 text-xs">{zh ? "对比序列" : ja("Series")}</legend><label className="flex items-center gap-2"><input type="checkbox" checked={account} onChange={(event) => setAccount(event.target.checked)} />{zh ? "账户" : ja("Account")}</label>{universe.map((symbol) => <label className="flex items-center gap-2" key={symbol}><input type="checkbox" checked={selected.includes(symbol)} onChange={(event) => setSelected(event.target.checked ? universe.filter((item) => selected.includes(item) || item === symbol) : selected.filter((item) => item !== symbol))} /><span style={{ color: palette[universe.indexOf(symbol) % palette.length] }}>{symbol}</span></label>)}</fieldset>
    <div className="flex flex-wrap gap-3 text-sm"><label className="lo-field"><span>{zh ? "收益图显示方式" : ja("Returns layout")}</span><select aria-label={zh ? "收益图显示方式" : ja("Returns layout")} value={layout} onChange={(event) => setLayout(event.target.value as "overlay" | "facets")}><option value="overlay">{zh ? "同轴叠加" : ja("Overlay on one axis")}</option><option value="facets">{zh ? "按资产分面（相同刻度）" : ja("Separate panels (shared scale)")}</option></select></label><label className="lo-field"><span>{zh ? "纵轴" : ja("Y axis")}</span><select aria-label={zh ? "纵轴" : ja("Y axis")} value={axis} onChange={(event) => setAxis(event.target.value as "return_pct" | "index_value")}><option value="return_pct">{zh ? "累计收益率（%）" : ja("Cumulative return (%)")}</option><option value="index_value">{zh ? "归一化净值（基准 = 100）" : ja("Indexed value (baseline = 100)")}</option></select></label></div>
    {loading && <p className="text-sm">{zh ? "计算收益率对比…" : ja("Computing return comparison…")}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{zh ? "收益率读取失败" : ja("Returns unavailable")}: {error}</p>}
    {!selected.length && !account && <p className="text-sm">{zh ? "请选择至少一个标的或账户。" : ja("Select at least one asset or the account.")}</p>}
    {data && <>
      <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-900">{data.baseline_date ? `${zh ? "共同基准" : ja("Shared baseline")}: ${data.baseline_date} ${zh ? "收盘" : ja("close")} → ${data.comparison_end} · ET` : (zh ? "尚无共同的有效基准日。" : ja("No shared valid baseline date yet."))}{data.provisional ? ` · ${zh ? "部分结果" : ja("Partial results")}` : ""}</p>
      {data.unavailable.length > 0 && <p className="text-sm text-amber-700">{zh ? "缺少观测数据" : ja("No observations")}: {data.unavailable.map((key) => key === "account" ? (zh ? "账户" : ja("Account")) : key).join(", ")}</p>}
      {data.series.length > 0 && (layout === "overlay" ? <ReturnPlot series={data.series} domainSeries={data.series} axis={axis} name={name} color={color} zh={zh} /> : <div className="grid min-w-0 gap-4 xl:grid-cols-2">{data.series.map((series) => <ReturnPlot key={series.id} series={[series]} domainSeries={data.series} axis={axis} name={name} color={color} zh={zh} />)}</div>)}
      <p className="text-xs leading-6 text-[var(--color-ink-soft)]">{zh ? "收益率 = 当日收盘值 ÷ 共同基准日收盘值 − 1；切换所选资产会重新计算共同基准。标的为价格收益，不计分红；账户权益包含交易成本。缺失值留空且不连线。此处比较区间收益，与「结果总览」中从任务期初计算的总收益可能不同。" : ja("Return = daily closing value / shared baseline close − 1. Selecting different series recalculates the shared baseline. Assets show price returns without dividends; account equity includes trading costs. Missing values remain gaps. These comparison-period returns may differ from inception-to-date returns in Results.")}</p>
      {!!dates.length && <details><summary className="cursor-pointer text-sm font-medium">{zh ? "查看收益率明细" : ja("Return values table")} ({dates.length})</summary><div className="mt-3 overflow-x-auto"><table className="w-full whitespace-nowrap text-left text-xs"><thead><tr><th className="p-2">{zh ? "日期（ET）" : ja("Date (ET)")}</th>{data.series.map((series) => <th className="p-2" key={series.id}>{name(series)} (%)</th>)}</tr></thead><tbody>{dates.slice(page * 20, page * 20 + 20).map((date, offset) => <tr key={date} className="border-t border-[var(--color-rule)]"><td className="p-2">{date}</td>{data.series.map((series) => { const value = series.points[page * 20 + offset]?.return_pct; return <td key={series.id} className="p-2 tabular-nums">{value == null ? "—" : `${(value * 100).toFixed(2)}%`}</td>; })}</tr>)}</tbody></table></div><div className="mt-3 flex justify-between text-xs"><button type="button" disabled={page === 0} onClick={() => setTablePage(page - 1)}>{zh ? "上一页" : ja("Previous page")}</button><span>{page + 1} / {pageCount}</span><button type="button" disabled={page === pageCount - 1} onClick={() => setTablePage(page + 1)}>{zh ? "下一页" : ja("Next page")}</button></div></details>}
    </>}
  </section>;
}

function ReturnPlot({ series, domainSeries, axis, name, color, zh }: { series: ReturnSeries[]; domainSeries: ReturnSeries[]; axis: "return_pct" | "index_value"; name: (series: ReturnSeries) => string; color: (series: ReturnSeries) => string; zh: boolean }) {
  const { ja } = useLocale();
  const [selected, setSelected] = useState<number | null>(null);
  const points = series[0]?.points ?? [];
  const numbers = domainSeries.flatMap((item) => item.points.map((point) => point[axis]).filter((value): value is number => value != null && Number.isFinite(value)));
  const base = axis === "return_pct" ? 0 : 100;
  const low = Math.min(base, ...numbers), high = Math.max(base, ...numbers);
  const span = high - low || (axis === "return_pct" ? 0.01 : 1);
  const compact = series.length === 1 && domainSeries.length > 1;
  const left = 70, right = compact ? 450 : 800, svgWidth = compact ? 490 : 840;
  const x = (i: number) => left + i / Math.max(1, points.length - 1) * (right - left);
  const y = (value: number) => 220 - (value - low) / span * 170;
  const format = (value: number | null) => value == null ? "—" : axis === "return_pct" ? `${(value * 100).toFixed(2)}%` : value.toFixed(2);
  const index = selected !== null && selected < points.length ? selected : points.length - 1;
  const label = series.map(name).join(" / ");
  const paths = useMemo(() => series.map((item) => {
    const segments: string[] = []; let current = "";
    item.points.forEach((point, i) => { const value = point[axis]; if (value === null || !Number.isFinite(value)) { if (current) segments.push(current); current = ""; } else current += `${x(i)},${y(value)} `; });
    if (current) segments.push(current);
    return { item, segments };
  }), [series, axis, low, span, points.length, compact]);
  return <figure className="min-w-0 rounded-xl border border-[var(--color-rule)] p-3" data-testid="return-plot">
    <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"><span className="font-medium">{points[index]?.date}</span>{series.map((item) => <span key={item.id} style={{ color: color(item) }}>{name(item)}: {format(item.points[index]?.[axis] ?? null)}</span>)}</figcaption>
    <svg viewBox={`0 0 ${svgWidth} 264`} role="group" aria-label={`${label} ${zh ? "收益率对比图" : ja("return comparison chart")}`} className="mt-2 w-full" onMouseMove={(event) => { const rect = event.currentTarget.getBoundingClientRect(); const position = (event.clientX - rect.left) / rect.width * svgWidth; setSelected(Math.min(points.length - 1, Math.max(0, Math.round((position - left) / (right - left) * (points.length - 1))))); }}>
      {[0, 0.5, 1].map((part) => { const value = low + part * span; return <g key={part}><line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke="currentColor" opacity="0.15" /><text x="62" y={y(value) + 4} textAnchor="end" fontSize="11" fill="currentColor">{format(value)}</text></g>; })}
      <line x1={x(index)} x2={x(index)} y1="40" y2="220" stroke="#8996aa" strokeDasharray="3 3" opacity="0.6" />
      {paths.map(({ item, segments }) => <g key={item.id}>{segments.map((segment, i) => <polyline key={i} points={segment} fill="none" stroke={color(item)} strokeWidth={item.kind === "account" ? 3 : 2} strokeDasharray={item.kind === "account" || domainSeries.indexOf(item) < 12 ? undefined : "5 3"} />)}{item.points.map((point, i) => point[axis] == null ? null : <circle key={point.date} role="button" tabIndex={0} aria-label={`${name(item)} ${point.date}: ${format(point[axis])}`} cx={x(i)} cy={y(point[axis]!)} r={i === index ? 4 : 2} fill={color(item)} onFocus={() => setSelected(i)} onClick={() => setSelected(i)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(i); } }}><title>{name(item)} {point.date}: {format(point[axis])}</title></circle>)}</g>)}
      <text x={left} y="252" fontSize="11" fill="currentColor">{points[0]?.date}</text><text x={right} y="252" fontSize="11" fill="currentColor" textAnchor="end">{points[points.length - 1]?.date}</text>
    </svg>
  </figure>;
}
