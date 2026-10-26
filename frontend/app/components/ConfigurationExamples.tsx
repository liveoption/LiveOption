import { useLocale } from "../lib/locale";
import { useState } from "react";

type Avsr = { moneyness_anchors: number[]; expiry_anchors: number[]; distance_lambda: number };
// Deliberately fixed teaching data, never presented as quotes from a run.
const chain = [0.92, 0.96, 1, 1.04, 1.08].flatMap((m) => [5, 14, 35, 75, 100].map((dte) => ({
  m, dte, iv: 0.2 + Math.abs(m - 1) * 1.2 + dte * 0.0003,
})));
export function AvsrExample({ value, zh }: { value: Avsr; zh: boolean }) {
  const { ja } = useLocale();
  const [cell, setCell] = useState([1, 1]);
  function nearest(m: number, dte: number) {
    return chain.reduce((best, item) => score(item) < score(best) ? item : best);
    function score(item: typeof chain[number]) { return Math.abs(item.m - m) + value.distance_lambda * Math.abs(item.dte - dte); }
  }
  const m = value.moneyness_anchors[cell[1]], dte = value.expiry_anchors[cell[0]];
  const selected = nearest(m, dte);
  return <figure className="mt-4 rounded-xl bg-[var(--color-canvas)] p-4" data-testid="avsr-example">
    <figcaption className="text-sm font-medium">{zh ? "可交互示例 · 25 份合约 → 3 × 3 波动率摘要" : ja("Interactive example · 25 contracts → 3 × 3 volatility summary")}</figcaption>
    <p className="mt-1 text-xs text-[var(--color-ink-soft)]">{zh ? "虚构数据，假设现价 $100。调整上方锚点或点击格子，查看选择结果。" : ja("Illustrative data; spot = $100. Change anchors or select a cell.")}</p>
    <div className="mt-3 grid grid-cols-[55px_repeat(3,minmax(0,1fr))] gap-1 text-center text-xs">
      <span>DTE ↓</span>{value.moneyness_anchors.map((anchor, i) => <span key={i}>K/S {(anchor * 100).toFixed(1)}%</span>)}
      {value.expiry_anchors.map((expiry, row) => <div className="contents" key={row}><span className="self-center">{expiry}d</span>{value.moneyness_anchors.map((anchor, col) => {
        const item = nearest(anchor, expiry);
        return <button key={col} type="button" aria-pressed={cell[0] === row && cell[1] === col} aria-label={`${expiry}d ${anchor * 100}% IV ${(item.iv * 100).toFixed(1)}%`} onClick={() => setCell([row, col])} className={`rounded border p-3 tabular-nums ${cell[0] === row && cell[1] === col ? "border-blue-600 ring-1 ring-blue-600" : "border-transparent"}`} style={{ backgroundColor: `rgba(45,104,190,${Math.min(0.32, item.iv * 0.7)})` }}>{(item.iv * 100).toFixed(1)}% IV</button>;
      })}</div>)}
    </div>
    <p className="mt-3 text-xs leading-6">{zh ? `目标：K/S ${(m * 100).toFixed(1)}%、${dte} 天 → 代表合约：行权价 $${selected.m * 100}、${selected.dte} 天、IV ${(selected.iv * 100).toFixed(1)}%。` : ja("Target: K/S {0}%, {1}d → contract: strike ${2}, {3}d, IV {4}%.", (m * 100).toFixed(1), dte, selected.m * 100, selected.dte, (selected.iv * 100).toFixed(1))}<br />{zh ? "距离 = |K/S − 目标比例| + λ × |剩余天数 − 目标天数|；选择距离最小的合约。多个格子可能选中同一合约。" : ja("Distance = |K/S − target| + λ × |DTE − target days|. Select the nearest contract; cells can share a contract.")}</p>
  </figure>;
}

const candidates = [
  { id: "C1", right: "C", dte: 7, delta: 0.35, volume: 900 },
  { id: "C2", right: "C", dte: 14, delta: 0.52, volume: 650 },
  { id: "C3", right: "C", dte: 35, delta: 0.72, volume: 400 },
  { id: "P1", right: "P", dte: 7, delta: -0.28, volume: 850 },
  { id: "P2", right: "P", dte: 30, delta: -0.45, volume: 500 },
  { id: "P3", right: "P", dte: 90, delta: -0.15, volume: 200 },
];
export function DataExample({ values, dteMin, dteMax, zh }: { values: Record<string, unknown>; dteMin: number; dteMax: number; zh: boolean }) {
  const { ja } = useLocale();
  const min = Number(values.chain_filter_delta_min ?? 0), max = Number(values.chain_filter_delta_max ?? 1);
  const cap = Number(values.chain_max_contracts_per_ticker ?? 10);
  const accepted = candidates.filter((c) => c.dte >= dteMin && c.dte <= dteMax && Math.abs(c.delta) >= min && Math.abs(c.delta) <= max);
  const selected = accepted.filter((c) => accepted.filter((a) => a.right === c.right && a.volume > c.volume).length < cap);
  const stale = Number(values.max_stale_minutes ?? 10);
  const lookback = Number(values.lookback_bars ?? 60);
  return <figure className="mt-4 rounded-xl bg-[var(--color-canvas)] p-4" data-testid="data-example">
    <figcaption className="text-sm font-medium">{zh ? "筛选与数据窗口示例 · 虚构数据" : ja("Filtering and context-window example · illustrative data")}</figcaption>
    <p className="mt-1 text-xs text-[var(--color-ink-soft)]">{zh ? `DTE ${dteMin}–${dteMax} 天 → |Delta| ${min}–${max} → Call / Put 分别按成交量最多保留 ${cap} 份候选合约。` : ja("DTE {0}–{1} → |Delta| {2}–{3} → retain up to {4} candidates per Call / Put side by volume.", dteMin, dteMax, min, max, cap)}</p>
    <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>{zh ? "合约" : ja("Contract")}</th><th>DTE</th><th>Delta</th><th>{zh ? "成交量" : ja("Volume")}</th><th>{zh ? "筛选结果" : ja("Result")}</th></tr></thead><tbody>{candidates.map((c) => {
      const keep = selected.includes(c);
      const reason = c.dte < dteMin || c.dte > dteMax ? "DTE" : Math.abs(c.delta) < min || Math.abs(c.delta) > max ? "Delta" : zh ? "数量上限" : ja("Candidate cap");
      return <tr key={c.id} className="border-t border-[var(--color-rule)]"><td className="py-2">{c.id}</td><td>{c.dte}</td><td>{c.delta}</td><td>{c.volume}</td><td className={keep ? "text-green-700" : "text-[var(--color-ink-soft)]"}>{keep ? (zh ? "保留" : ja("Keep")) : `${zh ? "排除" : ja("Exclude")} · ${reason}`}</td></tr>;
    })}</tbody></table></div>
    <div className="mt-4 grid gap-3 text-xs sm:grid-cols-2"><div className="rounded border border-[var(--color-rule)] p-3"><p>{zh ? "模型可见的行情窗口" : ja("Price context window")}</p><div className="my-2 flex h-10 items-end gap-1" aria-hidden="true">{[20, 28, 23, 35, 30, 42, 38, 47, 40, 53, 50, 61].map((h, i) => <span key={i} className={`flex-1 rounded-t ${i < 4 ? "bg-slate-200" : "bg-blue-500"}`} style={{ height: `${h}%` }} />)}</div><p>{zh ? `蓝色代表最近 ${lookback} 根 K 线；每根的周期由回放频率决定。` : ja("Blue represents the latest {0} bars; bar duration follows the replay frequency.", lookback)}</p></div><div className="rounded border border-[var(--color-rule)] p-3"><p>{zh ? "行情缺失时的订单检查" : ja("Order checks when quotes are missing")}</p><p className="mt-2">{zh ? `最后报价 → 沿用前值并标记过期 → 超过 ${stale} 分钟则拒绝依赖该报价的订单。` : ja("Last quote → forward-fill + stale flag → reject dependent orders after {0} minutes.", stale)}</p><p className="mt-2 text-[var(--color-ink-soft)]">{zh ? "保留候选合约不代表订单一定成交。示意图仅解释规则。" : ja("A retained candidate does not guarantee a fill. This is a rule illustration.")}</p></div></div>
  </figure>;
}

export function DecisionSchedule({ daily, time, interval, onTime, zh }: { daily: boolean; time: string; interval: number; onTime: (value: string) => void; zh: boolean }) {
  const { ja } = useLocale();
  const minute = (value: string) => { const [h, m] = value.split(":").map(Number); return h * 60 + m; };
  const label = (n: number) => `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
  // Intraday engine counts regular-session minute bars from the first available bar.
  const ticks = daily ? [minute(time)] : Array.from({ length: Math.floor(390 / Math.max(1, interval)) }, (_, i) => 570 + (i + 1) * Math.max(1, interval) - 1);
  return <section className="rounded-xl border border-[var(--color-rule)] p-4" data-testid="decision-schedule">
    {daily && <label className="lo-field"><span>{zh ? "每日决策时间（ET）" : ja("Daily decision time (ET)")}</span><input aria-label={zh ? "每日决策时间（ET）" : ja("Daily decision time (ET)")} type="time" required min="09:30" max="15:59" step="60" value={time} onChange={(event) => onTime(event.target.value)} /></label>}
    <p className="mt-2 text-sm font-medium">{daily ? `${zh ? "每日" : ja("Daily")} ${time} ET` : (zh ? `每 ${interval} 分钟决策一次` : ja("One decision every {0} minutes", interval))}</p>
    <svg viewBox="0 0 600 72" role="img" aria-label={zh ? "一个完整交易日的决策时间示例" : ja("Decision times over a full trading day")} className="mt-2 w-full"><line x1="25" y1="24" x2="575" y2="24" stroke="#cbd5e1" strokeWidth="3" />{ticks.map((t, i) => <circle key={i} cx={25 + (t - 570) / 390 * 550} cy="24" r={ticks.length > 60 ? 1.5 : 4} fill="#245db5"><title>{label(t)} ET</title></circle>)}<text x="25" y="57" fontSize="12" fill="currentColor">09:30</text><text x="300" y="57" textAnchor="middle" fontSize="12" fill="currentColor">ET · America/New_York</text><text x="575" y="57" textAnchor="end" fontSize="12" fill="currentColor">16:00</text></svg>
    <p className="text-xs leading-5 text-[var(--color-ink-soft)]">{daily ? (zh ? "按纽约时间自动处理夏令时。仅使用该时刻已可见的数据，订单在后续分钟 K 线撮合。若当天没有该时刻的回放步骤（如提前收盘），则跳过当天决策。" : ja("New York time handles DST. Only data available at this time is used; orders are evaluated on subsequent minute bars. If that replay minute is absent (e.g. early close), the decision is skipped.")) : (zh ? `完整交易日示例：${ticks.slice(0, 4).map(label).join("、")}${ticks.length > 4 ? "…" : ""} ET。引擎从首根常规时段分钟 K 线开始，每 ${interval} 根调用模型；实际时间以回放步骤为准。决策不等于成交。` : ja("Full-session example: {0}{1} ET. Calls occur every {2} regular-session minute bars from the first available bar. Actual replay steps determine timestamps; decisions are not fills.", ticks.slice(0, 4).map(label).join(", "), ticks.length > 4 ? "…" : "", interval))}</p>
  </section>;
}
