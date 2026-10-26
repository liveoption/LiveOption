import { useEffect, useState } from "react";
import { useLocale } from "../../lib/locale";
import ResultVisualizations, { type ResultPoint } from "./ResultVisualizations";

type DumpReport = {
  background: { run_id: string; title?: string; status: string; purpose: string; context?: string; timezone: string };
  settings: { configuration_source: string; experiment: { start?: string; end?: string; universe?: string[]; initial_cash?: number; frequency?: string }; decision_time_et?: string; overlay_mode?: "hedge" | "covered_call" | "hybrid" | null; model: { model_id?: string; provider?: string }; context?: string; prompt_templates?: Record<string, string> };
  results: { provisional: boolean; summary: { decisions: number; trade_proposals: number; fill_events: number; completed_days: number; total_pnl: number | null }; series: ResultPoint[]; activity: { trading_day: string; decisions: number; trade_proposals: number }[] };
};

type Analysis = {
  protocol_version: string;
  run: { id: string; status?: string };
  metrics: null | {
    generic?: Record<string, unknown>;
    family_specific?: Record<string, unknown>;
    behavioral?: Record<string, unknown>;
  };
  paper_portfolio?: Record<string, unknown> | null;
  pending: boolean;
};

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function display(value: number | null, kind: "percent" | "money" | "ratio" | "count", zh: boolean, ja: (text: string) => string = (text) => text): string {
  if (value === null) return zh ? "样本不足" : ja("Insufficient data");
  if (kind === "percent") return new Intl.NumberFormat(zh ? "zh-CN" : ja("en-US"), { style: "percent", maximumFractionDigits: 2 }).format(value);
  if (kind === "money") return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
  return new Intl.NumberFormat(zh ? "zh-CN" : ja("en-US"), { maximumFractionDigits: kind === "count" ? 0 : 2 }).format(value);
}

export default function AnalysisReport({ runId, status }: { runId: string; status?: string | null }) {
  const {zh, ja } = useLocale();
  const [report, setReport] = useState<Analysis | null>(null);
  const [error, setError] = useState("");
  const [dump, setDump] = useState<DumpReport | null>(null);
  const [dumpError, setDumpError] = useState("");
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    let active = true;
    const read = () => {
      fetch(`/api/tasks/${encodeURIComponent(runId)}/analysis`).then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (active) { setReport(await response.json() as Analysis); setError(""); }
      }).catch((cause) => { if (active) setError(String(cause)); });
    };
    read();
    const timer = status === "pending" || status === "running" ? window.setInterval(read, 10000) : undefined;
    return () => { active = false; if (timer) window.clearInterval(timer); };
  }, [runId, status]);
  useEffect(() => {
    let active = true;
    setDump(null); setDumpError("");
    const read = () => fetch(`/api/tasks/${encodeURIComponent(runId)}/report`).then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const value = await response.json();
      if (active && value.protocol_version === "liveoption.report.v1") { setDump(value); setDumpError(""); }
    }).catch((cause) => { if (active) setDumpError(String(cause)); });
    read();
    const timer = status === "pending" || status === "running" ? window.setInterval(read, 10000) : undefined;
    return () => { active = false; if (timer) window.clearInterval(timer); };
  }, [runId, status]);

  const paper = report?.paper_portfolio;
  const generic = report?.metrics?.generic;
  const behavioral = report?.metrics?.behavioral;
  const cards = [
    { key: "total_pnl", name: zh ? "累计盈亏" : ja("Total P&L"), value: dump ? finite(dump.results.summary.total_pnl) : finite(generic?.total_pnl), kind: "money" as const },
    { key: "annualized_return", name: zh ? "年化收益" : ja("Annualized return"), value: finite(paper?.annualized_return), kind: "percent" as const },
    { key: "max_drawdown_pct", name: zh ? "最大回撤" : ja("Maximum drawdown"), value: finite(paper?.max_drawdown_pct), kind: "percent" as const },
    { key: "sharpe", name: zh ? "夏普比率" : ja("Sharpe ratio"), value: finite(paper?.sharpe), kind: "ratio" as const },
    { key: "var_95", name: zh ? "95% 单日 VaR" : ja("95% one-day VaR"), value: finite(paper?.var_95), kind: "percent" as const },
    { key: "cvar_95", name: zh ? "95% 单日 CVaR" : ja("95% one-day CVaR"), value: finite(paper?.cvar_95), kind: "percent" as const },
  ];
  const days = finite(paper?.n_days);
  const decisions = finite(behavioral?.n_decisions);
  const trades = finite(behavioral?.n_with_trade);

  const download = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${runId}-analysis.json`; anchor.click();
    URL.revokeObjectURL(url);
  };
  async function downloadReport(format: "html" | "markdown" | "json") {
    setDownloading(true); setDumpError("");
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(runId)}/report?format=${format}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url;
      link.download = `${runId}-report.${format === "markdown" ? "md" : format}`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { setDumpError(String(cause)); } finally { setDownloading(false); }
  }

  return <section className="lo-panel p-6" data-testid="analysis-report">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="lo-eyebrow">PERFORMANCE / RISK</p>
        <h2 className="mt-2 text-xl font-semibold">{zh ? "结果总览与报告" : ja("Results and report")}</h2>
        <p className="mt-1 text-sm text-[var(--color-ink-soft)]">{zh ? "基于已完成交易日的权益序列。" : ja("Based on the equity series from completed trading days.")}</p>
      </div>
      <div className="flex flex-wrap gap-2">{(["html", "markdown", "json"] as const).map((format) => <button key={format} type="button" disabled={downloading || !dump} onClick={() => downloadReport(format)} className="rounded-lg border border-[var(--color-rule)] px-3 py-2 text-sm disabled:opacity-40">{zh ? "导出" : ja("Export")} {format === "markdown" ? "Markdown" : format.toUpperCase()} {zh ? "报告" : ja("report")}</button>)}</div>
    </div>
    <p className="mt-3 text-xs text-[var(--color-ink-soft)]">{zh ? "报告包含 Background、Settings、Results；HTML 可离线查看图表和打印，Markdown 便于分享，JSON 包含完整结构化结果。" : ja("Reports include Background, Settings and Results. HTML includes offline charts and supports printing; Markdown is shareable; JSON contains complete structured results.")}</p>
    {dumpError && <p role="alert" className="mt-3 text-sm text-red-600">{zh ? "报告暂时无法读取或导出" : ja("Report loading or export failed")}: {dumpError}</p>}
    {dump?.results.provisional && <p className="mt-3 rounded border border-amber-300 p-3 text-sm">{zh ? "当前为进行中或未完整结束的结果，报告会标记为临时/部分结果。" : ja("This run is ongoing or did not finish completely. Reports are marked provisional / partial.")}</p>}
    {error && <p role="alert" className="mt-4 text-sm text-red-600">{zh ? "分析数据暂时无法读取" : ja("Analysis data is unavailable")}: {error}</p>}
    {!report && !error && <p className="mt-4 text-sm">{zh ? "读取分析中…" : ja("Loading analysis…")}</p>}
    {report?.pending && <p className="mt-4 text-sm text-[var(--color-ink-soft)]">{zh ? "运行完成后生成收益与风险指标。" : ja("Performance and risk metrics appear after completion.")}</p>}
    {report?.metrics && <>
      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {cards.map((card) => <div key={card.key} className="rounded border border-[var(--color-rule)] p-4">
          <p className="text-xs text-[var(--color-ink-soft)]">{card.name}</p>
          <p className="mt-2 text-xl font-semibold tabular-nums">{display(card.value, card.kind, zh, ja)}</p>
        </div>)}
      </div>
      <p className="mt-4 text-sm text-[var(--color-ink-soft)]">
        {zh ? `已覆盖 ${display(days, "count", zh, ja)} 个交易日` : ja("{0} trading days covered", display(days, "count", zh, ja))}
        {decisions !== null && <> · {zh ? "决策" : ja("Decisions")} {display(decisions, "count", zh, ja)}</>}
        {trades !== null && <> · {zh ? "有交易意图" : ja("Trade proposals")} {display(trades, "count", zh, ja)}</>}
      </p>
      <p className="mt-3 text-xs text-[var(--color-ink-soft)]">{zh ? "年化收益由当前样本外推；样本较短时波动较大。VaR/CVaR 需要至少 20 个有效日收益。场景细项请看「策略分析」。" : ja("Annualized return extrapolates this sample and is volatile for short runs. VaR/CVaR require at least 20 valid daily returns. Scenario details are under Strategy analysis.")}</p>
    </>}
    {dump && <>
      <div className="mt-4 flex flex-wrap gap-4 text-sm"><span>{zh ? "决策次数" : ja("Decisions")}: {dump.results.summary.decisions}</span><span>{zh ? "有交易意图的决策" : ja("Trade proposals")}: {dump.results.summary.trade_proposals}</span><span>{zh ? "实际成交事件" : ja("Executed fill events")}: {dump.results.summary.fill_events}</span></div>
      <ResultVisualizations key={runId} points={dump.results.series} activity={dump.results.activity} zh={zh} />
      <details className="mt-5 border-t border-[var(--color-rule)] pt-4 text-sm"><summary className="cursor-pointer font-medium">{zh ? "报告背景与实际设置" : ja("Report background and actual settings")}</summary><dl className="mt-3 space-y-2"><div>{zh ? "运行" : ja("Run")}: {dump.background.title || dump.background.run_id}</div><div>{zh ? "期间" : ja("Period")}: {dump.settings.experiment.start} → {dump.settings.experiment.end}</div><div>{zh ? "标的" : ja("Underlyings")}: {dump.settings.experiment.universe?.join(", ")}</div><div>{zh ? "模型" : ja("Model")}: {dump.settings.model.model_id} · {dump.settings.model.provider}</div><div>{zh ? "决策时刻" : ja("Decision time")}: {dump.settings.decision_time_et ? `${dump.settings.decision_time_et} ET` : (zh ? "详见报告中的策略配置" : ja("See strategy settings in the report"))}</div><div>{zh ? "回放行情周期" : ja("Replay bar frequency")}: {dump.settings.experiment.frequency || (zh ? "未记录" : ja("Not recorded"))}</div>{dump.settings.overlay_mode && <div>{zh ? "覆盖类型" : ja("Overlay type")}: {{ hedge: zh ? "对冲" : ja("Hedge"), covered_call: "Covered Call", hybrid: "Hybrid" }[dump.settings.overlay_mode]}</div>}<div>{zh ? "行情 / 模拟成交" : ja("Market data / simulated execution")}: QFinZero UPQ / PMB</div><div className="whitespace-pre-wrap">Context: {dump.settings.context || (zh ? "无额外交易偏好" : ja("No extra preferences"))}</div></dl>{dump.settings.configuration_source !== "saved_effective_config" && <p className="mt-3 text-amber-700">{zh ? "旧任务未保存完整有效配置。报告会标记缺失项，不会套用当前预设。" : ja("The legacy run did not retain a complete effective configuration. Missing values are marked; current presets are not substituted.")}</p>}{Object.entries(dump.settings.prompt_templates ?? {}).map(([stage, text]) => <details className="mt-3" key={stage}><summary className="cursor-pointer">{stage} Prompt</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-canvas)] p-3 text-xs">{text}</pre></details>)}</details>
    </>}
    <details className="mt-5 border-t border-[var(--color-rule)] pt-4 text-sm">
      <summary className="cursor-pointer font-medium">{zh ? "指标说明与研究数据" : ja("Metric definitions and research data")}</summary>
      <dl className="mt-3 grid gap-2 text-[var(--color-ink-soft)]">
        <div><dt className="inline font-medium text-[var(--color-ink)]">{zh ? "年化收益" : ja("Annualized return")}</dt><dd className="inline">{zh ? "：按 252 个交易日，将当前权益序列复合外推。" : ja(": Compound extrapolation of the current equity series to 252 trading days.")}</dd></div>
        <div><dt className="inline font-medium text-[var(--color-ink)]">{zh ? "最大回撤" : ja("Maximum drawdown")}</dt><dd className="inline">{zh ? "：权益从历史峰值的最大跌幅，显示为比例。" : ja(": Largest percentage decline from a previous equity peak.")}</dd></div>
        <div><dt className="inline font-medium text-[var(--color-ink)]">VaR / CVaR</dt><dd className="inline">{zh ? "：有效日收益最差 5% 的边界及该尾部的平均损失。" : ja(": Boundary and average loss of the worst 5% of valid daily returns.")}</dd></div>
        <div><dt className="inline font-medium text-[var(--color-ink)]">{zh ? "数据缺失" : ja("Missing data")}</dt><dd className="inline">{zh ? "：样本不足会显示「样本不足」，不会填零。" : ja(": Insufficient samples are shown explicitly, never replaced with zero.")}</dd></div>
      </dl>
      <button type="button" onClick={download} disabled={!report} className="mt-4 rounded border border-[var(--color-rule)] px-3 py-2 text-sm">{zh ? "导出原始 JSON" : ja("Export raw JSON")}</button>
    </details>
  </section>;
}
