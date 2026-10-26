import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocale } from "../lib/locale";
import PromptSettings, { promptProblem, type PromptBase } from "./PromptSettings";
import ResearchSettings, { StrategySettings } from "./ResearchSettings";
import { DecisionSchedule } from "./ConfigurationExamples";
import ModelPicker, { type PickerModel as Model, type PickerGroup as Group } from "./ModelPicker";

type Cadence = "intraday" | "daily";
type OverlayMode = "hedge" | "covered_call" | "hybrid";
type ExecutionSettings = { option_fee_per_contract: number; stock_fee_per_share: number; slippage_bps: number; option_spread_pct: number; order_type_policy: "strategy" | "market" | "limit" };
type Setup = PromptBase & {
  name: string; description: string; family: string; cadence?: Cadence;
  overlay_mode?: OverlayMode;
  experiment: { start: string; end: string; initial_cash: number; universe: string[] };
  strategy_params: Record<string, unknown>; risk: Record<string, unknown>; data_policy: Record<string, unknown>;
  avsr: { moneyness_anchors: number[]; expiry_anchors: number[]; distance_lambda: number };
  execution?: ExecutionSettings; context: string;
};
type Draft = {
  title: string; start: string; end: string; cash: string; universe: string; context: string; dailyTime: string; overlayMode: OverlayMode;
  templates: Record<string, string>; edits: Record<string, boolean>; addenda: Record<string, string>;
  strategy: Record<string, unknown>; risk: Record<string, unknown>; data: Record<string, unknown>;
  news: boolean; newsDays: string; avsr: Setup["avsr"]; execution: ExecutionSettings;
};
const executionDefaults: ExecutionSettings = { option_fee_per_contract: 0.65, stock_fee_per_share: 0.0005, slippage_bps: 1, option_spread_pct: 0.05, order_type_policy: "strategy" };
const names: Record<string, [string, string]> = { intraday: ["日内", "Intraday"], overlay: ["覆盖", "Overlay"], earnings: ["财报", "Earnings"], daily: ["日频", "Daily"] };
const descriptions: Record<string, [string, string]> = {
  intraday: ["交易日内按分钟间隔做期权决策。", "Options decisions at minute intervals during the session."],
  overlay: ["持有股票，使用备兑看涨和保护性看跌期权。", "Covered calls and protective puts over stock holdings."],
  earnings: ["围绕财报发布前后进行期权交易。", "Options decisions around earnings releases."],
  daily: ["每个交易日在指定的 ET 时刻做一次期权决策。", "One options decision per trading day at your chosen ET time."],
};
function setupName(base: Setup, zh: boolean, ja: (text: string) => string = (text) => text) { return ja(names[base.family]?.[zh ? 0 : 1] ?? base.name); }
function draftFrom(base: Setup, custom: boolean): Draft {
  const { news_enabled, news_lookback_days, ...data } = base.data_policy;
  return { title: "", start: base.experiment.start, end: base.experiment.end, cash: String(base.experiment.initial_cash),
    universe: base.experiment.universe.join(", "), context: base.context ?? "", templates: { ...base.prompt_templates },
    edits: Object.fromEntries(Object.keys(base.prompt_templates).map((stage) => [stage, custom])), addenda: {},
    strategy: { ...base.strategy_params }, risk: { ...base.risk }, data, news: Boolean(news_enabled), newsDays: String(news_lookback_days ?? 7),
    dailyTime: "15:45", overlayMode: base.overlay_mode ?? "covered_call", avsr: base.avsr, execution: { ...executionDefaults, ...base.execution } };
}
export type TaskSpec = {
  protocol_version: "liveoption.run.v1"; preset_id?: string; cadence?: Cadence; mode: "preset" | "custom";
  decision_time_et?: string;
  overlay_mode?: OverlayMode;
  title?: string; model_id: string; context: string; prompt_addenda: Record<string, string>; prompt_templates: Record<string, string>;
  experiment: { start: string; end: string; initial_cash: number; universe: string[] };
  strategy_params: Record<string, unknown>; risk: Record<string, unknown>; data_policy: Record<string, unknown>;
  avsr: Setup["avsr"]; execution: ExecutionSettings;
};

export default function RunSpecEditor({ onSubmit, disabled, submitError }: { onSubmit: (spec: TaskSpec) => void; disabled?: boolean; submitError?: string }) {
  const {zh, ja } = useLocale();
  const [presets, setPresets] = useState<Setup[]>([]);
  const [customTypes, setCustomTypes] = useState<Setup[]>([]);
  const [supported, setSupported] = useState<string[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [modelId, setModelId] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [mode, setMode] = useState<"preset" | "custom">("preset");
  const [base, setBase] = useState<Setup>();
  const [draft, setDraft] = useState<Draft>();
  const [step, setStep] = useState(0);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [errorStage, setErrorStage] = useState("");
  const drafts = useRef<Record<string, Draft>>({});
  const form = useRef<HTMLFormElement>(null);
  const presetChoice = useRef("");
  const customChoice = useRef("");
  const stepLabels = zh ? ["测试范围", "交易规则", "Agent 指令", "确认启动"] : [ja("Test scope"), ja("Trading rules"), ja("Agent instructions"), ja("Review and start")];
  useEffect(() => {
    Promise.all([
      fetch("/api/tasks/presets").then((r) => { if (!r.ok) throw new Error(`presets: ${r.status}`); return r.json(); }),
      fetch("/api/model-settings").then((r) => { if (!r.ok) throw new Error(`models: ${r.status}`); return r.json(); }),
    ]).then(([catalog, registry]) => {
      const items = catalog.items as Setup[];
      setPresets(items); setCustomTypes(catalog.custom_types ?? []); setSupported(catalog.supported_underlyings ?? []);
      setModels(registry.models); setGroups(registry.groups);
      if (items[0]) { setBase(items[0]); setDraft(draftFrom(items[0], false)); presetChoice.current = items[0].id; }
      const usable = (registry.models as Model[]).find((m) => m.available !== false && (registry.groups as Group[]).find((g) => g.id === m.group_id)?.has_api_key);
      if (usable) setModelId(usable.id);
    }).catch((cause) => setLoadError(String(cause)));
  }, []);
  function choose(next: Setup, nextMode = mode) {
    if (base && draft) drafts.current[base.id] = draft;
    setBase(next); setDraft(drafts.current[next.id] ?? draftFrom(next, nextMode === "custom"));
    setMode(nextMode); setError(""); setErrorStage("");
    if (nextMode === "preset") presetChoice.current = next.id; else customChoice.current = next.id;
  }
  function changeMode(nextMode: "preset" | "custom") {
    if (mode === nextMode) return;
    const choices = nextMode === "preset" ? presets : customTypes;
    const remembered = nextMode === "preset" ? presetChoice.current : customChoice.current;
    const next = choices.find((item) => item.id === remembered) ?? choices[0];
    if (next) { choose(next, nextMode); setStep(0); }
  }
  function update<K extends keyof Draft>(key: K, value: Draft[K]) { setDraft((old) => old ? { ...old, [key]: value } : old); }
  function updateStrategy(value: Record<string, unknown>) {
    setDraft((old) => old ? { ...old, strategy: value, data: { ...old.data,
      chain_filter_dte_min: value.dte_min, chain_filter_dte_max: value.dte_max } } : old);
  }
  function updateExecution<K extends keyof ExecutionSettings>(key: K, value: ExecutionSettings[K]) { if (draft) update("execution", { ...draft.execution, [key]: value }); }
  function symbols() { return [...new Set((draft?.universe ?? "").split(/[,\s]+/).map((v) => v.trim().toUpperCase()).filter(Boolean))]; }
  function fail(message: string, at: number) { setError(message); setStep(at); return false; }
  function validate(at: number): boolean {
    if (!draft || !base) return false;
    if (at === 0) {
      if (!draft.start || !draft.end || draft.start > draft.end) return fail(zh ? "请设置有效的开始和结束日期。" : ja("Enter a valid date range."), 0);
      if (!Number.isFinite(Number(draft.cash)) || Number(draft.cash) < 1000 || Number(draft.cash) >= 10_000_000) return fail(zh ? "初始资金须为 1,000–9,999,999 美元。" : ja("Starting cash must be USD 1,000–9,999,999."), 0);
      const tickers = symbols();
      if (!tickers.length || tickers.length > 24) return fail(zh ? "请选择 1–24 个标的。" : ja("Choose 1–24 underlyings."), 0);
      const bad = mode === "custom" ? tickers.filter((ticker) => !/^[A-Z][A-Z0-9.\-]{0,15}$/.test(ticker)) : supported.length ? tickers.filter((ticker) => !supported.includes(ticker)) : [];
      if (bad.length) return fail(`${zh ? "暂无数据支持的标的" : ja("Unsupported underlyings")}: ${bad.join(", ")}`, 0);
      if (mode === "preset" && base.family === "intraday" && (tickers.length !== 1 || tickers[0] !== base.experiment.universe[0])) return fail(zh ? "该日内预设使用固定标的。更换标的请使用自定义任务。" : ja("This intraday preset has a fixed underlying. Use a custom task to change it."), 0);
      if (discovering || !models.find((m) => m.id === modelId && m.available !== false && groups.find((g) => g.id === m.group_id)?.has_api_key)) return fail(zh ? "等待模型列表获取完成，并选择已配置密钥的可用模型。" : ja("Wait for discovery and select an available model with a configured API key."), 0);
    }
    if (at === 1) {
      const inputs = form.current?.querySelectorAll<HTMLInputElement | HTMLSelectElement>('[data-setup-step="1"] input, [data-setup-step="1"] select') ?? [];
      for (const input of inputs) if (!input.checkValidity()) {
        fail(zh ? "请检查交易规则中的数值和单位。" : ja("Check the values in trading rules."), 1);
        requestAnimationFrame(() => input.reportValidity()); return false;
      }
      if (Number(draft.strategy.dte_min) > Number(draft.strategy.dte_max) || Number(draft.data.chain_filter_dte_min) > Number(draft.data.chain_filter_dte_max) || Number(draft.data.chain_filter_delta_min) > Number(draft.data.chain_filter_delta_max)) return fail(zh ? "到期天数和 Delta 的下限不能大于上限。" : ja("DTE and Delta lower bounds must not exceed upper bounds."), 1);
      const { moneyness_anchors: m, expiry_anchors: e } = draft.avsr;
      if (!(m[0] < m[1] && m[1] < m[2] && e[0] < e[1] && e[1] < e[2])) return fail(zh ? "AVSR 锚点必须依次递增。" : ja("AVSR anchors must increase."), 1);
    }
    if (at === 2) for (const [stage, text] of Object.entries(draft.templates)) {
      if (!draft.edits[stage] && mode !== "custom") continue;
      const problem = promptProblem(stage, text, base.prompt_variables?.[stage], zh, ja);
      if (problem) { setErrorStage(stage); return fail(`${stage}: ${problem}`, 2); }
    }
    setError(""); return true;
  }
  function next() { if (validate(step)) { setStep(step + 1); form.current?.scrollIntoView?.({ block: "start", behavior: "smooth" }); } }
  function submit(event: { preventDefault(): void }) {
    event.preventDefault();
    if (step < 3) { next(); return; }
    if (!base || !draft || ![0, 1, 2].every(validate)) return;
    onSubmit({ protocol_version: "liveoption.run.v1", mode,
      ...(mode === "preset" ? { preset_id: base.id } : { cadence: base.cadence }),
      ...(base.cadence === "daily" ? { decision_time_et: draft.dailyTime } : {}),
      ...(base.family === "overlay" ? { overlay_mode: draft.overlayMode } : {}),
      title: draft.title.trim() || undefined, model_id: modelId, context: draft.context.trim(),
      prompt_templates: Object.fromEntries(Object.entries(draft.templates).filter(([stage]) => mode === "custom" || draft.edits[stage])),
      prompt_addenda: Object.fromEntries(Object.entries(draft.addenda).filter(([, value]) => value.trim())),
      experiment: { start: draft.start, end: draft.end, initial_cash: Number(draft.cash), universe: symbols() },
      strategy_params: draft.strategy, risk: draft.risk, data_policy: { ...draft.data, news_enabled: draft.news, news_lookback_days: Number(draft.newsDays) }, avsr: draft.avsr, execution: draft.execution });
  }
  if (loadError) return <p role="alert" className="text-red-700">{loadError}</p>;
  if (!draft || !base) return <p>{zh ? "正在读取配置…" : ja("Loading setup…")}</p>;
  const model = models.find((m) => m.id === modelId);
  const group = groups.find((g) => g.id === model?.group_id);
  const daily = base.cadence === "daily";
  const overlayLabels: Record<OverlayMode, string> = { hedge: zh ? "对冲" : ja("Hedge"), covered_call: "Covered Call", hybrid: "Hybrid" };
  const overlayDescriptions: Record<OverlayMode, string> = {
    hedge: zh ? "以看跌期权及看跌价差保护持股，不开看涨期权仓位。" : ja("Protect stock holdings with puts and put spreads; no call positions."),
    covered_call: zh ? "以持股支持备兑看涨，可使用看涨价差；不交易看跌期权。" : ja("Write calls covered by stock holdings, including call spreads; no puts."),
    hybrid: zh ? "组合运行对冲与 Covered Call，分别管理看跌和看涨仓位，共用账户与风控。" : ja("Combine hedge and Covered Call sleeves, managing puts and calls separately in one account with shared risk controls."),
  };
  const cadenceLabel = daily ? `${zh ? "每日" : ja("Daily")} ${draft.dailyTime} ET` : base.family === "intraday" ? (zh ? `每 ${draft.strategy.decision_interval_minutes} 分钟` : ja("Every {0} min", draft.strategy.decision_interval_minutes)) : (zh ? "由预设安排" : ja("Preset schedule"));
  const orderLabel = { strategy: zh ? "模型决定" : ja("Model decides"), market: zh ? "市价单" : ja("Market"), limit: zh ? "限价单" : ja("Limit") }[draft.execution.order_type_policy];
  return <form ref={form} onSubmit={submit} noValidate className="space-y-5 scroll-mt-6" data-testid="run-spec-editor">
    <nav role="tablist" aria-label={zh ? "任务创建方式" : ja("Run setup mode")} className="flex gap-2 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-2">
      {(["preset", "custom"] as const).map((value) => <button type="button" role="tab" key={value} aria-selected={mode === value} disabled={disabled || (value === "custom" && !customTypes.length)} onClick={() => changeMode(value)} className={`flex-1 rounded-lg px-4 py-3 text-sm font-medium ${mode === value ? "bg-[var(--color-primary-600)] text-white" : "text-[var(--color-ink-soft)]"}`}>{value === "preset" ? (zh ? "从预设开始" : ja("Preset")) : (zh ? "自定义任务" : ja("Custom task"))}</button>)}
    </nav>
    <div className="grid items-start gap-5 lg:grid-cols-[205px_minmax(0,1fr)]">
      <aside className="lg:sticky lg:top-5"><nav aria-label={zh ? "配置步骤" : ja("Setup steps")} className="grid grid-cols-2 gap-2 lg:grid-cols-1">{stepLabels.map((label, index) => <button type="button" key={label} aria-current={step === index ? "step" : undefined} onClick={() => { setStep(index); setError(""); }} className={`flex items-center gap-3 rounded-lg border px-3 py-3 text-left text-sm ${step === index ? "border-[var(--color-primary-600)] bg-[var(--color-primary-600)] text-white" : "border-[var(--color-rule)] bg-[var(--color-paper)]"}`}><span className="font-mono text-xs">0{index + 1}</span>{label}</button>)}</nav><p className="mt-4 hidden px-2 text-xs leading-6 text-[var(--color-ink-soft)] lg:block">{setupName(base, zh, ja)}{base.family === "overlay" ? ` · ${overlayLabels[draft.overlayMode]}` : ""} · {cadenceLabel}<br />{draft.start} → {draft.end}<br />{zh ? "切换步骤会保留填写内容。" : ja("Your edits are kept between steps.")}</p></aside>
      <main className="min-w-0 space-y-4">
        <div hidden={step !== 0} data-setup-step="0"><section className="lo-panel space-y-6 p-5 sm:p-6"><div><h2 className="text-xl font-semibold">{stepLabels[0]}</h2><p className="mt-2 text-sm text-[var(--color-ink-soft)]">{mode === "preset" ? (zh ? "选择一组现成规则，再调整测试范围。" : ja("Choose a preset, then set your test scope.")) : (zh ? "选择决策频率。标的、交易逻辑和风险规则均由你设置。" : ja("Choose a cadence, then define underlyings, trading logic and risk rules."))}</p></div>
          <div><h3 className="mb-3 text-sm font-medium">{mode === "preset" ? (zh ? "预设" : ja("Preset")) : (zh ? "任务类别" : ja("Task category"))}</h3><div className={`grid gap-3 ${mode === "preset" ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}>{(mode === "preset" ? presets : customTypes).map((item) => <button type="button" key={item.id} aria-label={setupName(item, zh, ja)} aria-pressed={base.id === item.id} onClick={() => choose(item)} className={`rounded-xl border p-4 text-left ${base.id === item.id ? "border-[var(--color-primary-600)] bg-blue-50" : "border-[var(--color-rule)]"}`}><span className="font-semibold">{setupName(item, zh, ja)}</span><span className="mt-2 block text-xs leading-5 text-[var(--color-ink-soft)]">{ja(descriptions[item.family]?.[zh ? 0 : 1] ?? item.description)}</span></button>)}</div></div>
          {base.family === "overlay" && <fieldset className="space-y-3 rounded-xl border border-[var(--color-rule)] p-4" data-testid="overlay-modes">
            <legend className="px-2 text-sm font-semibold">{zh ? "覆盖类型" : ja("Overlay type")}</legend>
            <div className="grid gap-3 sm:grid-cols-3">{(["hedge", "covered_call", "hybrid"] as const).map((value) => <label key={value} className={`cursor-pointer rounded-lg border p-3 ${draft.overlayMode === value ? "border-[var(--color-primary-600)] bg-blue-50 dark:bg-blue-950/30" : "border-[var(--color-rule)]"}`}>
              <span className="flex items-center gap-2 font-medium"><input type="radio" name="overlay-mode" value={value} checked={draft.overlayMode === value} onChange={() => update("overlayMode", value)} />{overlayLabels[value]}</span>
              <span className="mt-2 block text-xs leading-5 text-[var(--color-ink-soft)]">{overlayDescriptions[value]}</span>
            </label>)}</div>
            <p className="text-xs leading-5 text-[var(--color-ink-soft)]">{zh ? "三种模式保留股票底仓；选择会影响实际执行引擎，不只是 Prompt 文案。" : ja("All modes retain stock holdings. Your selection changes the execution strategy, not just prompt text.")}</p>
            {draft.overlayMode === "hybrid" && <p className="text-xs leading-5 text-amber-700 dark:text-amber-300">{zh ? "Hybrid 在调仓日依次运行两个分析→执行流程，通常比单一模式多一倍模型调用。单次调仓预算由两部分各分配一半，账户风控仍统一执行；决策会分别记录。" : ja("Hybrid runs two sequential analysis-to-execution pipelines on rebalance days, usually doubling model calls. Each sleeve receives half the per-rebalance budget; account risk controls are shared and decisions are recorded separately.")}</p>}
          </fieldset>}
          <div className="grid gap-4 sm:grid-cols-2"><Field label={zh ? "名称（可选）" : ja("Run name (optional)")} className="sm:col-span-2"><input maxLength={200} value={draft.title} onChange={(event) => update("title", event.target.value)} placeholder={zh ? "我的期权测试" : ja("My options test")} /></Field><Field label={zh ? "开始日期" : ja("From")}><input type="date" value={draft.start} onChange={(event) => update("start", event.target.value)} /></Field><Field label={zh ? "结束日期" : ja("To")}><input type="date" value={draft.end} onChange={(event) => update("end", event.target.value)} /></Field><Field label={zh ? "初始资金（美元）" : ja("Starting cash · USD")}><input type="number" min="1000" max="9999999" value={draft.cash} onChange={(event) => update("cash", event.target.value)} /></Field><Field label={zh ? "标的（逗号分隔）" : ja("Underlyings (comma-separated)")}><input value={draft.universe} onChange={(event) => update("universe", event.target.value)} placeholder="SPY, QQQ" /></Field></div>
          {mode === "custom" ? <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "填写你的 QFinZero 数据中已有的标的；合成数据可用 QZSP、ACME、BOLT。请核对日期范围和期权数据。" : ja("Use underlyings available in your QFinZero data; the synthetic sample includes QZSP, ACME and BOLT. Check date and option coverage.")}</p> : supported.length > 0 && <details className="text-xs text-[var(--color-ink-soft)]"><summary className="cursor-pointer">{zh ? "查看预设标的" : ja("Preset underlyings")}</summary><p className="mt-2 leading-6">{supported.join(" · ")}</p></details>}
          <div className="border-t border-[var(--color-rule)] pt-5"><ModelPicker zh={zh} models={models} groups={groups} value={modelId} onChange={setModelId} onRegistry={(registry) => { setModels(registry.models); setGroups(registry.groups); }} onBusy={setDiscovering} /></div>
        </section></div>
        <div hidden={step !== 1} data-setup-step="1" className="space-y-4"><section className="lo-panel space-y-5 p-5 sm:p-6" data-testid="trading-settings"><div><h2 className="text-xl font-semibold">{stepLabels[1]}</h2><p className="mt-2 text-sm text-[var(--color-ink-soft)]">{daily ? (zh ? "指定每日决策时刻，使用分钟行情撮合；可持仓过夜。" : ja("Choose a daily decision time; execution uses minute bars. Overnight holdings are allowed.")) : (zh ? "设置决策频率、可交易期权和成交费用。" : ja("Set decision frequency, option selection and execution costs."))}</p></div>
          <StrategySettings zh={zh} values={draft.strategy} onChange={updateStrategy} />
          {(daily || base.family === "intraday") && <DecisionSchedule daily={daily} time={draft.dailyTime} interval={Number(draft.strategy.decision_interval_minutes ?? 15)} onTime={(value) => update("dailyTime", value)} zh={zh} />}
          <h3 className="border-t border-[var(--color-rule)] pt-5 font-medium">{zh ? "订单与费用" : ja("Orders and costs")}</h3><div className="grid gap-4 sm:grid-cols-2"><Field label={zh ? "订单类型" : ja("Order type policy")} className="sm:col-span-2"><select value={draft.execution.order_type_policy} onChange={(event) => updateExecution("order_type_policy", event.target.value as ExecutionSettings["order_type_policy"])}><option value="strategy">{zh ? "由模型决定" : ja("Model decides")}</option><option value="market">{zh ? "统一市价单" : ja("Force market orders")}</option><option value="limit">{zh ? "统一限价单" : ja("Force limit orders")}</option></select></Field><Field label={zh ? "每张期权手续费（美元）" : ja("Option fee / contract · USD")}><input type="number" min="0" max="100" step="0.01" required value={draft.execution.option_fee_per_contract} onChange={(event) => updateExecution("option_fee_per_contract", Number(event.target.value))} /></Field><Field label={zh ? "滑点（基点）" : ja("Slippage · bps")}><input type="number" min="0" max="1000" step="0.1" required value={draft.execution.slippage_bps} onChange={(event) => updateExecution("slippage_bps", Number(event.target.value))} /></Field><Field label={zh ? "期权买卖价差（%）" : ja("Option spread (%)")}><input type="number" min="0" max="100" step="0.1" required value={draft.execution.option_spread_pct * 100} onChange={(event) => updateExecution("option_spread_pct", Number(event.target.value) / 100)} /></Field>{base.family === "overlay" && <Field label={zh ? "每股手续费（美元）" : ja("Stock fee / share · USD")}><input type="number" min="0" max="10" step="0.0001" value={draft.execution.stock_fee_per_share} onChange={(event) => updateExecution("stock_fee_per_share", Number(event.target.value))} /></Field>}</div>
          {draft.execution.order_type_policy === "limit" && <p className="text-xs text-amber-700">{zh ? "请在 Prompt 中要求模型提供 limit_price。缺少有效限价时，该订单或整组多腿交易会跳过。" : ja("Orders without a valid limit_price are skipped, including the entire multi-leg group. Ask the model to supply a limit price.")}</p>}
          <div className="space-y-3 border-t border-[var(--color-rule)] pt-5"><h3 className="font-medium">{zh ? "模型可见的数据" : ja("Data supplied to the model")}</h3><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.news} onChange={(event) => update("news", event.target.checked)} />{zh ? "提供决策时已发布的新闻与事件" : ja("Include news and events published by decision time")}</label>{draft.news && <Field label={zh ? "新闻回看天数" : ja("News lookback days")}><input type="number" min="1" max="30" required value={draft.newsDays} onChange={(event) => update("newsDays", event.target.value)} /></Field>}</div>
        </section><ResearchSettings zh={zh} dteMin={Number(draft.strategy.dte_min ?? 0)} dteMax={Number(draft.strategy.dte_max ?? 365)} riskValues={Object.fromEntries(Object.entries(draft.risk).filter(([key]) => !(daily && key.startsWith("flatten_")) && !(mode === "custom" && key === "allow_overlay_stock_buy")))} dataPolicy={Object.fromEntries(Object.entries(draft.data).filter(([key]) => !["chain_filter_dte_min", "chain_filter_dte_max"].includes(key)))} avsr={draft.avsr} onRisk={(value) => update("risk", { ...draft.risk, ...value })} onData={(value) => update("data", { ...draft.data, ...value })} onAvsr={(value) => update("avsr", value)} onReset={() => { const defaults = draftFrom(base, mode === "custom"); setDraft({ ...draft, risk: defaults.risk, data: { ...defaults.data, chain_filter_dte_min: draft.strategy.dte_min, chain_filter_dte_max: draft.strategy.dte_max }, avsr: defaults.avsr }); }} /></div>
        <div hidden={step !== 2} data-setup-step="2">{base.family === "overlay" && <p className="lo-panel p-4 text-sm" data-testid="overlay-prompt-rules">{zh ? "覆盖类型" : ja("Overlay type")}: {overlayLabels[draft.overlayMode]} · {overlayDescriptions[draft.overlayMode]}</p>}<PromptSettings zh={zh} base={base} custom={mode === "custom"} context={draft.context} onContext={(value) => update("context", value)} templates={draft.templates} edits={draft.edits} onTemplate={(stage, value) => update("templates", { ...draft.templates, [stage]: value })} onEdit={(stage, value) => update("edits", { ...draft.edits, [stage]: value })} addenda={draft.addenda} onAddendum={(stage, value) => update("addenda", { ...draft.addenda, [stage]: value })} requestedStage={errorStage} /></div>
        <div hidden={step !== 3} data-setup-step="3"><section className="lo-panel space-y-5 p-5 sm:p-6"><div><h2 className="text-xl font-semibold">{stepLabels[3]}</h2><p className="mt-2 text-sm text-[var(--color-ink-soft)]">{zh ? "确认本次测试使用的配置，启动后可在运行记录中查看进度和结果。" : ja("Review this configuration. Progress and results appear in Runs after launch.")}</p></div>
          <Review title={stepLabels[0]} edit={() => setStep(0)} zh={zh}><Row label={zh ? "类型" : ja("Type")} value={`${mode === "custom" ? (zh ? "自定义 · " : ja("Custom · ")) : ""}${setupName(base, zh, ja)}${base.family === "overlay" ? ` · ${overlayLabels[draft.overlayMode]}` : ""}`} /><Row label={zh ? "期间" : ja("Period")} value={`${draft.start} → ${draft.end}`} /><Row label={zh ? "标的" : ja("Underlyings")} value={symbols().join(", ")} /><Row label={zh ? "初始资金" : ja("Starting cash")} value={`$${Number(draft.cash).toLocaleString()}`} /><Row label={zh ? "模型" : ja("Model")} value={`${model?.label ?? "—"} · ${group?.label ?? "—"}`} /></Review>
          <Review title={stepLabels[1]} edit={() => setStep(1)} zh={zh}><Row label={zh ? "决策频率" : ja("Decisions")} value={cadenceLabel} /><Row label={zh ? "到期天数" : ja("DTE range")} value={`${draft.strategy.dte_min}–${draft.strategy.dte_max}`} /><Row label={zh ? "订单" : ja("Orders")} value={orderLabel} /><Row label={zh ? "期权手续费" : ja("Option fee")} value={`$${draft.execution.option_fee_per_contract} / ${zh ? "张" : ja("contract")}`} /><Row label={zh ? "滑点 / 价差" : ja("Slippage / spread")} value={`${draft.execution.slippage_bps} bps / ${draft.execution.option_spread_pct * 100}%`} /><Row label={zh ? "每日亏损上限" : ja("Daily loss limit")} value={Number(draft.risk.max_daily_loss_pct) ? `${Number(draft.risk.max_daily_loss_pct) * 100}%` : (zh ? "关闭" : ja("Off"))} /><Row label={zh ? "新闻" : ja("News")} value={draft.news ? `${zh ? "开启，回看" : ja("On, lookback")} ${draft.newsDays} ${zh ? "天" : ja("days")}` : (zh ? "关闭" : ja("Off"))} /></Review>
          <Review title={stepLabels[2]} edit={() => setStep(2)} zh={zh}><Row label="Prompt" value={mode === "custom" ? (zh ? "自定义指令" : ja("Custom instructions")) : (zh ? `${Object.values(draft.edits).filter(Boolean).length} 项已修改，其余沿用默认` : ja("{0} edited; others use defaults", Object.values(draft.edits).filter(Boolean).length))} /><p className="mt-3 whitespace-pre-wrap text-sm text-[var(--color-ink-soft)]">{draft.context || (zh ? "无额外交易偏好" : ja("No extra trading preferences"))}</p></Review>
          <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "使用历史数据回放，无需连接券商。" : ja("Historical replay; no broker connection required.")}</p>
        </section></div>
        {(error || submitError) && <p role="alert" className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error || submitError}</p>}
        <footer className="flex items-center justify-between gap-3 rounded-xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-4"><button type="button" disabled={step === 0 || disabled} className="rounded-lg border border-[var(--color-rule)] px-4 py-2 text-sm disabled:opacity-40" onClick={() => { setStep(step - 1); setError(""); }}>{zh ? "上一步" : ja("Back")}</button><span className="text-xs text-[var(--color-ink-soft)]">{step + 1} / 4</span>{step < 3 ? <button key="continue" type="button" disabled={disabled} className="rounded-lg bg-[var(--color-primary-600)] px-5 py-2 text-sm text-white" onClick={next}>{zh ? "下一步" : ja("Continue")}</button> : <button key="launch" type="button" onClick={submit} disabled={disabled || !modelId} className="rounded-lg bg-[var(--color-primary-600)] px-5 py-2 text-sm text-white disabled:opacity-50">{disabled ? (zh ? "启动中…" : ja("Starting…")) : (zh ? "启动测试" : ja("Start run"))}</button>}</footer>
      </main>
    </div>
  </form>;
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) { return <label className={`lo-field ${className}`}><span>{label}</span>{children}</label>; }
function Row({ label, value }: { label: string; value: string }) { return <div className="flex flex-wrap justify-between gap-2 text-sm"><dt className="text-[var(--color-ink-soft)]">{label}</dt><dd className="break-words text-right">{value}</dd></div>; }
function Review({ title, edit, zh, children }: { title: string; edit: () => void; zh: boolean; children: ReactNode }) {
  const { ja } = useLocale(); return <section className="rounded-xl border border-[var(--color-rule)] p-4"><div className="mb-3 flex items-center justify-between"><h3 className="font-medium">{title}</h3><button type="button" className="text-xs text-[var(--color-primary-600)]" onClick={edit}>{zh ? "修改" : ja("Edit")}</button></div><dl className="space-y-2">{children}</dl></section>; }
