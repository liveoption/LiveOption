import { useEffect, useMemo, useState } from "react";
import type { DecisionRecordRow } from "../../lib/contracts";
import { useLocale } from "../../lib/locale";

type Bar = { time: string; open: number; high: number; low: number; close: number; volume: number };
type ChartResponse = { symbol: string; resolution: string; source: string; bars: Bar[] };
const etClock = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });

export interface KlineChartProps {
  runId: string;
  universe: string[];
  startDay?: string | null;
  family?: string | null;
  dailyBars?: boolean;
  decisions: DecisionRecordRow[];
  selectedSeq?: number | null;
  onSelectDecision: (seq: number) => void;
}

function etLabel(time: string, daily: boolean): string {
  if (daily) return time.slice(5, 10);
  const ms = Date.parse(time);
  return Number.isFinite(ms) ? etClock.format(ms) : time.slice(11, 16);
}

function nearestIndex(bars: Bar[], timestamp: string): number {
  const target = Date.parse(timestamp);
  if (!Number.isFinite(target)) return -1;
  let best = -1;
  let bestDistance = Infinity;
  for (let index = 0; index < bars.length; index++) {
    const distance = Math.abs(Date.parse(bars[index].time) - target);
    if (distance < bestDistance) { bestDistance = distance; best = index; }
  }
  return bestDistance <= 30 * 60_000 ? best : -1;
}

function inRegularSession(time: string): boolean {
  const ms = Date.parse(time);
  if (!Number.isFinite(ms)) return false;
  const parts = etClock.formatToParts(ms);
  const hour = Number(parts.find((part) => part.type === "hour")?.value);
  const minute = Number(parts.find((part) => part.type === "minute")?.value);
  const clock = hour * 60 + minute;
  return clock >= 9 * 60 + 30 && clock <= 16 * 60;
}

export default function KlineChart({ runId, universe, startDay, family, dailyBars, decisions, selectedSeq, onSelectDecision }: KlineChartProps) {
  const {zh, ja } = useLocale();
  const [symbol, setSymbol] = useState("");
  const [day, setDay] = useState("");
  const [response, setResponse] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [checkpointIndex, setCheckpointIndex] = useState<number | null>(null);
  const [regularOnly, setRegularOnly] = useState(true);
  const [manualDay, setManualDay] = useState(false);
  const daily = family === "overlay" || Boolean(dailyBars);
  const availableDays = useMemo(() => Array.from(new Set(decisions.map((item) => item.trading_day).filter((value): value is string => Boolean(value)))).sort(), [decisions]);
  useEffect(() => {
    setSymbol(universe[0] ?? "");
    setDay("");
    setManualDay(false);
    setResponse(null);
    setCheckpointIndex(null);
  }, [runId]);
  useEffect(() => { if (universe.length && !universe.includes(symbol)) setSymbol(universe[0]); }, [universe, symbol]);
  useEffect(() => {
    if (manualDay) return;
    const selectedDay = decisions.find((item) => item.seq === selectedSeq)?.trading_day;
    const preferred = selectedDay ?? availableDays[0] ?? startDay?.slice(0, 10) ?? "";
    if (preferred && day !== preferred) setDay(preferred);
  }, [day, manualDay, selectedSeq, decisions, availableDays, startDay]);
  useEffect(() => {
    if (!symbol || (!daily && !day)) return;
    const controller = new AbortController();
    setLoading(true); setError(""); setResponse(null); setCheckpointIndex(null);
    const query = new URLSearchParams({ symbol });
    if (!daily) query.set("day", day);
    fetch(`/api/tasks/${encodeURIComponent(runId)}/chart?${query}`, { signal: controller.signal })
      .then(async (result) => { if (!result.ok) throw new Error(`HTTP ${result.status}`); return result.json() as Promise<ChartResponse>; })
      .then((value) => setResponse(value))
      .catch((cause) => { if (!controller.signal.aborted) setError(String(cause)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, symbol, day, daily]);

  const bars = useMemo(() => {
    const sorted = [...(response?.bars ?? [])].sort((left, right) => left.time.localeCompare(right.time));
    return daily || !regularOnly ? sorted : sorted.filter((bar) => inRegularSession(bar.time));
  }, [response, daily, regularOnly]);
  const prices = bars.flatMap((bar) => [bar.low, bar.high]);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const span = Number.isFinite(high - low) && high > low ? high - low : 1;
  const maxVolume = Math.max(1, ...bars.map((bar) => bar.volume));
  const gap = bars.length > 500 ? 5 : bars.length > 160 ? 7 : bars.length > 100 ? 11 : Math.max(11, Math.floor(720 / Math.max(1, bars.length - 1)));
  const width = Math.max(820, 144 + Math.max(0, bars.length - 1) * gap);
  const plotTop = 38;
  const plotHeight = 270;
  const volumeTop = 326;
  const y = (price: number) => plotTop + (high - price) / span * plotHeight;
  const markerMap = new Map<number, DecisionRecordRow[]>();
  const dayDecisions = decisions.filter((decision) => daily || decision.trading_day === day);
  for (const decision of dayDecisions) {
    const index = daily ? bars.findIndex((bar) => bar.time.slice(0, 10) === decision.trading_day) : nearestIndex(bars, decision.decision_ts ?? "");
    if (index < 0) continue;
    markerMap.set(index, [...(markerMap.get(index) ?? []), decision]);
  }
  const checkpointCount = Array.from(markerMap.values()).reduce((count, items) => count + items.length, 0);
  const selectedCheckpoint = checkpointIndex == null ? [] : markerMap.get(checkpointIndex) ?? [];

  return <section className="lo-panel p-5" data-testid="kline-chart">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><p className="lo-eyebrow">QFINZERO / UPQ</p><h2 className="mt-1 text-lg font-semibold">{zh ? "K 线与交易检查点" : ja("Candles and decision checkpoints")}</h2></div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label>{zh ? "标的" : ja("Symbol")} <select className="ml-2 rounded border border-[var(--color-rule)] p-2" value={symbol} onChange={(event) => setSymbol(event.target.value)}>{universe.map((item) => <option key={item}>{item}</option>)}</select></label>
        {!daily && <label>{zh ? "日期" : ja("Day")} <input className="ml-2 rounded border border-[var(--color-rule)] p-2" type="date" value={day} onChange={(event) => { setManualDay(true); setDay(event.target.value); }} /></label>}
      </div>
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
      {!daily && <label className="flex items-center gap-2"><input type="checkbox" checked={!regularOnly} onChange={(event) => setRegularOnly(!event.target.checked)} />{zh ? "显示盘前盘后" : ja("Show extended hours")}</label>}
      {!!dayDecisions.length && <label className="flex items-center gap-2">{zh ? "跳到决策" : ja("Jump to decision")}<select aria-label={zh ? "跳到决策" : ja("Jump to decision")} value="" onChange={(event) => { const seq = Number(event.target.value); if (Number.isFinite(seq)) onSelectDecision(seq); }} className="max-w-64 rounded border border-[var(--color-rule)] p-2">
        <option value="">{zh ? `选择记录（${dayDecisions.length}）` : ja("Choose record ({0})", dayDecisions.length)}</option>
        {dayDecisions.map((decision) => <option key={decision.id} value={decision.seq}>#{decision.seq} · {decision.decision_ts ? etLabel(decision.decision_ts, daily) : decision.trading_day} · {decision.has_trade ? (zh ? "交易意图" : ja("Trade")) : (zh ? "未交易" : ja("Hold"))}</option>)}
      </select></label>}
    </div>
    <p className="mt-3 text-xs text-[var(--color-ink-soft)]">{zh ? `美东时间；${checkpointCount}/${dayDecisions.length} 次决策已对齐到当前 K 线。绿色：有交易意图；橙色：未交易。点击时间轴标记查看记录。标记是任务级决策，不表示已对该标的成交。` : ja("Eastern Time; {0}/{1} decisions aligned to these candles. Green: trade proposed; amber: no trade. Select a time-axis marker for details. Markers are run-level decisions, not confirmed fills for this symbol.", checkpointCount, dayDecisions.length)}</p>
    {loading && <p className="mt-6 text-sm">{zh ? "读取历史行情…" : ja("Loading historical bars…")}</p>}
    {error && <p role="alert" className="mt-6 text-sm text-red-600">{zh ? "行情读取失败" : ja("Price data unavailable")}: {error}</p>}
    {!loading && !error && response && !bars.length && <p className="mt-6 text-sm text-[var(--color-ink-soft)]">{zh ? "所选时段没有 K 线。可切换盘前盘后，或通过上方列表查看决策。" : ja("No candles in this session. Try extended hours or use the decision selector above.")}</p>}
    {!!bars.length && <div className="mt-5 overflow-x-auto rounded border border-[var(--color-rule)]" role="region" aria-label={zh ? "可横向滚动的 K 线图" : ja("Scrollable candlestick chart")}>
      <svg width={width} height={455} aria-label={`${symbol} ${response?.resolution} OHLC chart with ${bars.length} bars and ${checkpointCount} decision checkpoints`}>
        {[0, 1, 2, 3, 4].map((line) => {
          const py = plotTop + line * plotHeight / 4;
          return <g key={line}><line x1={64} x2={width - 8} y1={py} y2={py} stroke="currentColor" opacity={0.12} /><text x={4} y={py + 4} fontSize="11" fill="currentColor" opacity={0.55}>{(high - line * span / 4).toFixed(2)}</text></g>;
        })}
        {bars.map((bar, index) => {
          const x = 72 + index * gap;
          const up = bar.close >= bar.open;
          const color = up ? "#159b71" : "#d65c51";
          const bodyTop = Math.min(y(bar.open), y(bar.close));
          const bodyHeight = Math.max(1, Math.abs(y(bar.open) - y(bar.close)));
          return <g key={`${bar.time}-${index}`}><title>{`${etLabel(bar.time, daily)} O ${bar.open.toFixed(2)} H ${bar.high.toFixed(2)} L ${bar.low.toFixed(2)} C ${bar.close.toFixed(2)} V ${bar.volume.toLocaleString()}`}</title>
            <line x1={x} x2={x} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth={1.5} />
            <rect x={x - Math.min(6, Math.max(2, gap * 0.32))} y={bodyTop} width={Math.min(12, Math.max(4, gap * 0.64))} height={bodyHeight} fill={color} />
            <rect x={x - Math.min(6, Math.max(2, gap * 0.32))} y={volumeTop + 52 * (1 - bar.volume / maxVolume)} width={Math.min(12, Math.max(4, gap * 0.64))} height={52 * bar.volume / maxVolume} fill={color} opacity={0.45} />
            {(index === 0 || index === bars.length - 1 || index % Math.max(1, Math.floor(105 / gap)) === 0) && <text x={x} y={442} textAnchor="middle" fontSize="10" fill="currentColor" opacity={0.55}>{etLabel(bar.time, daily)}</text>}
          </g>;
        })}
        <line x1={64} x2={width - 8} y1={401} y2={401} stroke="currentColor" opacity={0.3} />
        {Array.from(markerMap.entries()).map(([index, items]) => {
          const x = 72 + index * gap;
          const active = items.some((item) => item.seq === selectedSeq) || checkpointIndex === index;
          const traded = items.some((item) => item.has_trade);
          const open = () => items.length === 1 ? onSelectDecision(items[0].seq) : setCheckpointIndex(index);
          return <g key={index} tabIndex={0} role="button" aria-label={`${zh ? "检查点" : ja("Checkpoint")} ${items.map((item) => `#${item.seq}`).join(", ")}, ${etLabel(bars[index].time, daily)}`} onClick={open} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }} className="cursor-pointer">
            <title>{`${items.map((item) => `#${item.seq}`).join(", ")} · ${items[0].decision_ts ?? bars[index].time} · ${traded ? "TRADE" : "HOLD"}`}</title>
            <line x1={x} x2={x} y1={383} y2={401} stroke={active ? "#635bce" : traded ? "#159b71" : "#d99a31"} strokeWidth={2} />
            <circle cx={x} cy={401} r={active ? 8 : 6} fill={active ? "#635bce" : traded ? "#159b71" : "#d99a31"} stroke="white" strokeWidth={1.5} />
            {items.length > 1 && <text x={x} y={387} textAnchor="middle" fontSize="10" fill="currentColor">{items.length}</text>}
          </g>;
        })}
      </svg>
    </div>}
    {selectedCheckpoint.length > 1 && <div className="mt-3 rounded border border-[var(--color-rule)] p-3 text-sm">
      <p className="font-semibold">{zh ? "该时间点的决策" : ja("Decisions at this time")}</p>
      <div className="mt-2 flex flex-wrap gap-2">{selectedCheckpoint.map((decision) => <button type="button" key={decision.id} onClick={() => onSelectDecision(decision.seq)} className="rounded border border-[var(--color-rule)] px-3 py-1 hover:bg-[var(--color-surface)]">#{decision.seq} · {decision.has_trade ? (zh ? "有交易意图" : ja("Trade")) : (zh ? "未交易" : ja("Hold"))}</button>)}</div>
    </div>}
    {response && <p className="mt-3 text-xs text-[var(--color-ink-soft)]">{zh ? "行情来源" : ja("Market data")}: {response.source} · {response.resolution} · {zh ? "K 线为标的价格，期权交易详见决策记录" : ja("Candles show the underlying; option actions are in the decision record")}</p>}
  </section>;
}
