import { MetricCard, MetricValue, pluck } from "./cards";
import { useLocale } from "../../lib/locale";
import { finiteMetric } from "../../lib/attribution";

/**
 * `BehavioralPanel` — shared "Behavioral Analysis" surface (Phase D §6.2).
 *
 * Populated from `data.behavioral` on the metrics response. Fields per
 * Fields include intent_consistency, directional_bias,
 * confidence_calibration, position_sizing, stop_loss_adherence,
 * leverage_utilization. Greeks attribution has its own shared chart outside
 * this panel; decision counts here do not imply executed fills.
 */
export interface BehavioralPanelProps {
  behavioral: unknown;
}

export default function BehavioralPanel({ behavioral }: BehavioralPanelProps) {
  const {zh, ja } = useLocale();
  const b = behavioral ?? {};
  const decisions = finiteMetric(pluck(b, "n_decisions"));
  const proposed = finiteMetric(pluck(b, "n_with_trade"));
  const hasCounts = decisions !== null && proposed !== null && Number.isInteger(decisions) && Number.isInteger(proposed) && decisions >= 0 && proposed >= 0 && proposed <= decisions;
  const measured = [
    { key: "intent_consistency", label: zh ? "意图一致性（近似）" : ja("Intent consistency (proxy)"), format: "percent" as const },
    { key: "directional_bias", label: zh ? "方向偏好" : ja("Directional bias"), format: "number" as const },
    { key: "confidence_calibration", label: zh ? "置信度校准" : ja("Confidence calibration"), format: "number" as const },
    { key: "position_sizing", label: zh ? "仓位管理" : ja("Position sizing"), format: "number" as const },
    { key: "stop_loss_adherence", label: zh ? "止损执行" : ja("Stop-loss adherence"), format: "percent" as const },
    { key: "leverage_utilization", label: zh ? "杠杆使用" : ja("Leverage utilization"), format: "percent" as const },
  ].filter((item) => typeof pluck(b, item.key) === "number" && Number.isFinite(pluck(b, item.key)));

  return (
    <section
      data-testid="behavioral-panel"
      className="lo-panel flex flex-col gap-4 p-5"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        {zh ? "行为分析" : ja("Behavioral analysis")}
      </h2>
      {hasCounts && <div data-testid="decision-activity-summary" className="rounded-xl bg-[var(--color-paper-2)] p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3"><p className="text-sm">{zh ? "决策活动" : ja("Decision activity")}</p><p className="font-mono text-xl font-semibold tabular-nums">{decisions} <span className="text-xs font-normal text-[var(--color-ink-soft)]">{zh ? "次决策" : ja("decisions")}</span></p></div>
        <div role="img" aria-label={zh ? `有交易意图 ${proposed} 次，未提出交易 ${decisions - proposed} 次` : ja("{0} trade-proposing decisions; {1} without a proposal", proposed, decisions - proposed)} className="mt-4 flex h-3 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"><div className="h-full bg-blue-600" style={{ width: `${decisions > 0 ? proposed / decisions * 100 : 0}%` }} /></div>
        <div className="mt-3 flex flex-wrap justify-between gap-2 text-xs"><span><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-blue-600" />{zh ? "有交易意图" : ja("Trade proposed")} <strong className="font-mono">{proposed}</strong>{decisions > 0 && ` · ${(proposed / decisions * 100).toFixed(1)}%`}</span><span><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-slate-300" />{zh ? "未提出交易" : ja("No proposal")} <strong className="font-mono">{decisions - proposed}</strong></span></div>
        <p className="mt-3 text-[11px] text-[var(--color-ink-soft)]">{zh ? "按决策记录统计，不代表成交笔数。" : ja("Counts decision records, not executed fills.")}</p>
      </div>}
      {measured.length ? <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {measured.map((item) => <MetricCard key={item.key} label={item.label}>
          <MetricValue value={pluck(b, item.key)} format={item.format} />
        </MetricCard>)}
      </div> : <p className="text-sm text-neutral-500">{hasCounts ? (zh ? "其余行为指标未测量。" : ja("Additional behavioral metrics were not measured.")) : (zh ? "当前运行没有可用的行为指标；未测量项不展示。" : ja("No measured behavioral metrics are available for this run."))}</p>}

    </section>
  );
}
