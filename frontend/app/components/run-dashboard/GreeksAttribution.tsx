import { useState } from "react";
import { useLocale } from "../../lib/locale";
import { attributionPosition, attributionSummary, type GreekField } from "../../lib/attribution";

const definitions: Record<GreekField, [string, string]> = {
  delta_pnl: ["标的价格变动的一阶影响。", "First-order effect of underlying price changes."],
  gamma_pnl: ["标的价格变化引起的曲率影响。", "Curvature effect of underlying price changes."],
  theta_pnl: ["时间流逝对期权价值的影响。", "Effect of time passing on option value."],
  vega_pnl: ["隐含波动率变化的影响。", "Effect of changes in implied volatility."],
  vanna_pnl: ["标的价格与波动率共同变化的交叉影响。", "Cross-effect of underlying price and volatility changes."],
  volga_pnl: ["波动率变化的二阶影响。", "Second-order effect of volatility changes."],
  residual: ["上述近似分量未解释的剩余部分，不能直接视为手续费。", "Remainder not explained by the approximation above; not necessarily fees."],
};

export default function GreeksAttribution({ attribution, source }: { attribution: unknown; source: "family" | "behavioral" }) {
  const { zh, ja } = useLocale();
  const [smallValues, setSmallValues] = useState(false), [selected, setSelected] = useState<GreekField>("delta_pnl");
  const summary = attributionSummary(attribution);
  const money = (value: number | null) => value === null ? (zh ? "未记录" : ja("Not recorded")) : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
  const compact = (value: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(value);
  const precise = (value: number | null) => value === null ? money(null) : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 20 }).format(value);
  const name = (key: GreekField) => key === "residual" ? (zh ? "残差" : ja("Residual")) : key.replace("_pnl", "").replace(/^./, c => c.toUpperCase());
  const max = Math.max(0, ...summary.items.map(item => Math.abs(item.value ?? 0)));
  const active = summary.items.find(item => item.key === selected)!;
  return <section data-testid="greeks-attribution" className="lo-panel overflow-hidden">
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--color-rule)] p-5"><div><p className="lo-eyebrow">P&amp;L ATTRIBUTION / USD</p><h3 className="mt-1 text-lg font-semibold">{zh ? "Greeks 盈亏归因" : ja("Greeks P&L attribution")}</h3><p className="mt-2 max-w-2xl text-xs leading-5 text-[var(--color-ink-soft)]">{zh ? "观察各风险因子对盈亏的近似贡献。独立于权利金统计，不与上方指标重复相加。" : ja("Approximate P&L contributions by risk factor. Separate from premium statistics; do not add them to the summary cards.")}</p></div><div className="flex flex-wrap gap-1 rounded-lg bg-[var(--color-paper-2)] p-1" role="group" aria-label={zh ? "归因图比例" : ja("Attribution chart scale")}><button type="button" aria-pressed={!smallValues} onClick={() => setSmallValues(false)} className={`rounded px-3 py-2 text-xs ${!smallValues ? "bg-[var(--color-primary-600)] text-white" : ""}`}>{zh ? "金额比例" : ja("Linear amounts")}</button><button type="button" aria-pressed={smallValues} onClick={() => setSmallValues(true)} className={`rounded px-3 py-2 text-xs ${smallValues ? "bg-[var(--color-primary-600)] text-white" : ""}`}>{zh ? "查看小额分量" : ja("Inspect small components")}</button></div></header>
    {!summary.measured ? <p className="p-6 text-sm text-[var(--color-ink-soft)]">{zh ? "这次运行未记录可用的 Greeks 归因数据。" : ja("No measured Greeks attribution is available for this run.")}</p> : <>
      <div className="grid grid-cols-1 gap-3 border-b border-[var(--color-rule)] bg-[var(--color-paper-2)] p-5 sm:grid-cols-3">
        {[{ label: zh ? "正向贡献" : ja("Positive contributions"), value: summary.positive, color: "text-emerald-700 dark:text-emerald-400" }, { label: zh ? "负向贡献" : ja("Negative contributions"), value: summary.negative, color: "text-rose-700 dark:text-rose-400" }, { label: zh ? "已记录分量合计" : ja("Recorded components total"), value: summary.net, color: "" }].map(item => <div key={item.label} className="flex min-w-0 flex-wrap items-baseline justify-between gap-2 sm:block"><p className="text-xs text-[var(--color-ink-soft)]">{item.label}</p><p className={`break-words font-mono text-sm font-semibold tabular-nums sm:mt-2 sm:text-xl ${item.color}`}>{money(item.value)}</p></div>)}
      </div>
      <div className="grid min-w-0 gap-6 p-4 sm:p-5 xl:grid-cols-[minmax(0,1fr)_240px]">
        <div className="min-w-0">
          <div className="mb-3 grid grid-cols-[52px_minmax(0,1fr)_100px] gap-2 text-[10px] text-[var(--color-ink-soft)] sm:grid-cols-[72px_minmax(0,1fr)_128px]"><span>USD</span><div className="flex justify-between"><span>{smallValues ? "−" : compact(-max)}</span><span>0</span><span>{smallValues ? "+" : compact(max)}</span></div><span className="text-right">{zh ? "金额（USD）" : ja("Amount (USD)")}</span></div>
          <div role="group" aria-label={zh ? "Greeks 正负贡献图" : ja("Signed Greeks contribution chart")} className="space-y-2">{summary.items.map(item => {
            const position = attributionPosition(item.value ?? 0, max, smallValues);
            return <button type="button" key={item.key} data-testid={`attribution-${item.key}`} data-value={item.value ?? "missing"} onClick={() => setSelected(item.key)} onFocus={() => setSelected(item.key)} aria-pressed={selected === item.key} aria-label={`${name(item.key)}: ${money(item.value)}`} className={`grid w-full grid-cols-[52px_minmax(0,1fr)_100px] items-center gap-2 rounded-lg border px-1 py-3 text-left sm:grid-cols-[72px_minmax(0,1fr)_128px] sm:px-2 ${selected === item.key ? "border-[var(--color-primary-600)] bg-[var(--color-paper-2)]" : "border-transparent hover:bg-[var(--color-paper-2)]"}`}>
              <span className="text-xs font-medium sm:text-sm">{name(item.key)}</span><span className="relative h-7 rounded bg-[var(--color-paper-2)]" aria-hidden="true"><span className="absolute inset-y-0 left-1/2 border-l border-[var(--color-ink-soft)] opacity-40" />{item.value !== null && <span className={`absolute top-1 h-5 rounded-sm ${item.value < 0 ? "bg-rose-500" : item.value > 0 ? "bg-emerald-500" : "bg-slate-400"}`} style={{ left: `${50 + Math.min(0, position) * 50}%`, width: `${Math.abs(position) * 50}%`, minWidth: 1 }} />}</span><span className={`text-right font-mono text-[11px] tabular-nums sm:text-sm ${item.value === null ? "text-[var(--color-ink-soft)]" : item.value < 0 ? "text-rose-700 dark:text-rose-400" : item.value > 0 ? "text-emerald-700 dark:text-emerald-400" : ""}`}>{money(item.value)}</span>
            </button>;
          })}</div>
          <p className="mt-3 text-[11px] leading-5 text-[var(--color-ink-soft)]">{smallValues ? (zh ? "当前为对称对数比例：sign(x) × log(1 + |x|)，保留正负与零点，便于比较小额分量；条长不代表线性金额。" : ja("Symmetric log scale: sign(x) × log(1 + |x|). Signs and zero are preserved to inspect small components; lengths are not linear amounts.")) : (zh ? "左右使用相同金额比例。绿色为正，红色为负；零值与极小值以细线标记。选择一项查看说明。" : ja("Both sides share one amount scale. Green is positive, red is negative; zero and tiny values use a thin marker. Select a component for details."))}</p>
        </div>
        <aside className="min-w-0 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper-2)] p-4" aria-live="polite"><p className="lo-eyebrow">{zh ? "分量说明" : ja("Component detail")}</p><h4 className="mt-3 text-xl font-semibold">{name(active.key)}</h4><p className="mt-2 break-words font-mono text-xl tabular-nums">{money(active.value)}</p><p className="mt-4 text-sm leading-6">{zh ? definitions[active.key][0] : ja(definitions[active.key][1])}</p><code className="mt-4 block break-all text-xs text-[var(--color-ink-soft)]">{active.key}</code></aside>
      </div>
      <details className="border-t border-[var(--color-rule)] p-5"><summary className="cursor-pointer text-sm font-medium">{zh ? "查看归因明细" : ja("Attribution details table")}</summary><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th className="py-2">{zh ? "分量" : ja("Component")}</th><th>{zh ? "原始字段" : ja("Source field")}</th><th className="text-right">USD</th></tr></thead><tbody>{summary.items.map(item => <tr key={item.key} className="border-t border-[var(--color-rule)]"><td className="py-2">{name(item.key)}</td><td className="font-mono">{item.key}</td><td title={item.value === null ? undefined : String(item.value)} className="whitespace-nowrap text-right font-mono tabular-nums">{precise(item.value)}</td></tr>)}</tbody></table></div></details>
    </>}
    <footer className="border-t border-[var(--color-rule)] px-5 py-3 text-[11px] leading-5 text-[var(--color-ink-soft)]">{zh ? `数据来源：${source === "family" ? "场景指标" : "行为指标汇总"} · 已记录 ${summary.measured}/7 项。合计仅覆盖已记录分量，包含残差（若有）；不保证等于账户净盈亏。` : ja("Source: {0} · {1}/7 recorded. Total covers recorded components, including residual if present; it may differ from account net P&L.", source === "family" ? ja("Family metrics") : ja("Behavioral summary"), summary.measured)}</footer>
  </section>;
}
