import { useLocale } from "../lib/locale";
import { AvsrExample, DataExample } from "./ConfigurationExamples";
type Values = Record<string, unknown>;
type Avsr = { moneyness_anchors: number[]; expiry_anchors: number[]; distance_lambda: number };
type FieldInfo = { key: string; zh: string; en: string; helpZh: string; helpEn: string; min?: number; max?: number; step?: number; percent?: boolean; options?: [string, string, string][] };

const strategy: FieldInfo[] = [
  { key: "decision_interval_minutes", zh: "决策间隔（分钟）", en: "Decision interval (min)", helpZh: "每隔多少分钟调用一次模型。", helpEn: "Minutes between model decisions.", min: 1, max: 390, step: 1 },
  { key: "post_decision_interval_minutes", zh: "财报后决策间隔（分钟）", en: "Post-earnings interval (min)", helpZh: "财报后盘中调用模型的间隔。", helpEn: "Model decision interval after earnings.", min: 1, max: 390, step: 1 },
  { key: "max_positions", zh: "最多持仓数", en: "Maximum positions", helpZh: "同时持有的期权仓位上限。", helpEn: "Maximum simultaneous option positions.", min: 1, max: 50, step: 1 },
  { key: "stock_qty", zh: "初始每只股票股数", en: "Initial shares per ticker", helpZh: "组合覆盖场景中各标的的初始持股数量。", helpEn: "Initial shares per underlying in overlay runs.", min: 0, step: 1 },
  { key: "cash_buffer_pct", zh: "现金缓冲（%）", en: "Cash buffer (%)", helpZh: "预留现金占比。", helpEn: "Share of cash reserved.", min: 0, max: 100, step: 1, percent: true },
  { key: "otm_pct", zh: "价外距离（%）", en: "OTM distance (%)", helpZh: "选择价外期权时的目标价格距离。", helpEn: "Target distance for out-of-the-money contracts.", min: 0, max: 100, step: 1, percent: true },
  { key: "rebalance_weekday", zh: "再平衡星期", en: "Rebalance weekday", helpZh: "0=周一，6=周日。", helpEn: "0=Monday, 6=Sunday.", min: 0, max: 6, step: 1 },
  { key: "max_position_cost", zh: "单仓最大成本（美元）", en: "Maximum position cost (USD)", helpZh: "财报事件单个期权仓位允许投入的金额。", helpEn: "Maximum spend for one earnings position.", min: 1, step: 1 },
  { key: "max_contracts", zh: "单次最大合约数", en: "Maximum contracts", helpZh: "单次财报交易的合约张数上限。", helpEn: "Maximum contracts per earnings trade.", min: 1, max: 1000, step: 1 },
  { key: "auto_close_at_t2", zh: "财报后第二天自动平仓", en: "Auto-close on T+2", helpZh: "在 T+2 自动结束相关仓位。", helpEn: "Automatically close on the second post-earnings day." },
  { key: "dte_min", zh: "最短到期天数", en: "Minimum DTE", helpZh: "候选期权的最短剩余天数。", helpEn: "Minimum days to expiry.", min: 0, max: 365, step: 1 },
  { key: "dte_max", zh: "最长到期天数", en: "Maximum DTE", helpZh: "候选期权的最长剩余天数。", helpEn: "Maximum days to expiry.", min: 0, max: 365, step: 1 },
];
const risk: FieldInfo[] = [
  { key: "max_exposure_pct", zh: "最大敞口倍数", en: "Maximum exposure multiple", helpZh: "总敞口相对账户权益的倍数；预设中的大数值表示近似不限制。", helpEn: "Exposure relative to equity; a very large preset value effectively disables this limit.", min: 0, step: 0.1 },
  { key: "max_daily_loss_pct", zh: "每日最大亏损（%）", en: "Maximum daily loss (%)", helpZh: "达到后拒绝新交易；0 表示关闭。", helpEn: "Reject new trades beyond this loss; 0 disables it.", min: 0, step: 0.1, percent: true },
  { key: "circuit_breaker_loss_pct", zh: "熔断亏损（%）", en: "Circuit-breaker loss (%)", helpZh: "当日亏损达到阈值后触发熔断；0 表示关闭。", helpEn: "Daily loss threshold for circuit breaker; 0 disables it.", min: 0, step: 0.1, percent: true },
  { key: "cooldown_seconds", zh: "下单冷却（秒）", en: "Order cooldown (sec)", helpZh: "连续下单的最短间隔。", helpEn: "Minimum interval between orders.", min: 0, step: 1 },
  { key: "flatten_before_close", zh: "收盘前清仓", en: "Flatten before close", helpZh: "日内策略在收盘前关闭持仓。", helpEn: "Close intraday positions before market close." },
  { key: "flatten_minutes_before_close", zh: "提前清仓分钟数", en: "Minutes before close", helpZh: "距离收盘多少分钟开始清仓。", helpEn: "Minutes before close at which flattening starts.", min: 0, max: 390, step: 1 },
  { key: "max_orders_per_day", zh: "每日最多订单", en: "Maximum orders per day", helpZh: "单日订单总数上限。", helpEn: "Maximum number of daily orders.", min: 1, step: 1 },
  { key: "max_position_per_symbol", zh: "每个标的最大持仓", en: "Maximum position per symbol", helpZh: "每个标的允许的最大仓位数量，期权按张计。", helpEn: "Maximum position per symbol; contracts for options.", min: 1, step: 1 },
  { key: "max_net_delta", zh: "净 Delta 上限（可留空）", en: "Net Delta limit (optional)", helpZh: "留空表示不启用该限制。", helpEn: "Leave empty to disable this limit.", min: 0, step: 0.1 },
  { key: "max_net_vega", zh: "净 Vega 上限（可留空）", en: "Net Vega limit (optional)", helpZh: "留空表示不启用该限制。", helpEn: "Leave empty to disable this limit.", min: 0, step: 0.1 },
  { key: "allow_naked_short_options", zh: "允许无保护卖空期权", en: "Allow naked short options", helpZh: "风险较高；关闭时拒绝无保护的卖空期权。", helpEn: "Higher risk; when off, uncovered short options are rejected." },
  { key: "prevent_position_inversion", zh: "防止平仓反向建仓", en: "Prevent position inversion", helpZh: "将平仓数量限制在实际持仓以内。", helpEn: "Clamp closes to live inventory." },
  { key: "allow_overlay_stock_buy", zh: "允许组合策略买入股票", en: "Allow overlay stock buys", helpZh: "组合策略买股时不使用期权张数限制。", helpEn: "Exempt stock buys from the option contract count limit." },
];
const data: FieldInfo[] = [
  { key: "max_stale_minutes", zh: "行情过期上限（分钟）", en: "Maximum stale data (min)", helpZh: "数据超过此时长时拒绝依赖该数据的订单。", helpEn: "Reject orders when required market data is older.", min: 0, step: 1 },
  { key: "lookback_bars", zh: "历史 K 线根数", en: "Historical bars", helpZh: "行情缓存保留的 K 线数；策略会进一步生成价格摘要。", helpEn: "Bars retained in the market history buffer; the strategy builds its price summary from this data.", min: 1, step: 1 },
  { key: "chain_refresh_interval", zh: "期权链刷新频率", en: "Option-chain refresh", helpZh: "每日更新候选合约，或仅在整个回放开始时选取一次。", helpEn: "Refresh candidates each day, or select once at replay start.", options: [["daily", "每日", "Daily"], ["session_start", "仅回放开始时", "Replay start only"]] },
  { key: "chain_filter_dte_min", zh: "期权链最短到期天数", en: "Chain minimum DTE", helpZh: "只提供到期天数不少于此值的合约。", helpEn: "Exclude contracts expiring sooner.", min: 0, max: 365, step: 1 },
  { key: "chain_filter_dte_max", zh: "期权链最长到期天数", en: "Chain maximum DTE", helpZh: "只提供到期天数不超过此值的合约。", helpEn: "Exclude contracts expiring later.", min: 0, max: 365, step: 1 },
  { key: "chain_filter_delta_min", zh: "最小 |Delta|", en: "Minimum |Delta|", helpZh: "筛选候选期权的绝对 Delta 下限。", helpEn: "Minimum absolute delta for candidate contracts.", min: 0, max: 1, step: 0.01 },
  { key: "chain_filter_delta_max", zh: "最大 |Delta|", en: "Maximum |Delta|", helpZh: "筛选候选期权的绝对 Delta 上限。", helpEn: "Maximum absolute delta for candidate contracts.", min: 0, max: 1, step: 0.01 },
  { key: "chain_max_contracts_per_ticker", zh: "每标的每类候选合约数", en: "Candidates per ticker per option side", helpZh: "Call 和 Put 分别按成交量排序，各最多保留此数量。", helpEn: "Calls and puts are separately ranked by volume and capped at this number.", min: 1, step: 1 },
];

function Fields({ fields, values, onChange, zh }: { fields: FieldInfo[]; values: Values; onChange: (next: Values) => void; zh: boolean }) {
  const { ja } = useLocale();
  return <div className="grid gap-4 sm:grid-cols-2">{fields.filter(({ key }) => key in values).map((field) => {
    const value = values[field.key];
    const label = zh ? field.zh : ja(field.en);
    return <div key={field.key} className="space-y-1 rounded-lg border border-[var(--color-rule)] p-3">
      <label className="block text-sm font-medium" htmlFor={`research-${field.key}`}>{label}</label>
      <p className="text-xs text-[var(--color-ink-soft)]">{zh ? field.helpZh : ja(field.helpEn)}</p>
      {typeof value === "boolean" ? <input id={`research-${field.key}`} aria-label={label} type="checkbox" checked={value} onChange={(event) => onChange({ ...values, [field.key]: event.target.checked })} />
        : field.options ? <select id={`research-${field.key}`} aria-label={label} value={String(value)} onChange={(event) => onChange({ ...values, [field.key]: event.target.value })}>{field.options.map(([id, cn, en]) => <option key={id} value={id}>{zh ? cn : ja(en)}</option>)}</select>
          : <input id={`research-${field.key}`} aria-label={label} type="number" required={field.key !== "max_net_delta" && field.key !== "max_net_vega"} min={field.min} max={field.max} step={field.step ?? "any"} value={value == null ? "" : field.percent ? Number(value) * 100 : Number(value)} onChange={(event) => onChange({ ...values, [field.key]: event.target.value === "" ? null : (field.percent ? Number(event.target.value) / 100 : Number(event.target.value)) })} />}
    </div>;
  })}</div>;
}

export function StrategySettings({ zh, values, onChange }: { zh: boolean; values: Values; onChange: (value: Values) => void }) {
  return <Fields fields={strategy} values={values} onChange={onChange} zh={zh} />;
}

export default function ResearchSettings({ zh, riskValues, dataPolicy, avsr, dteMin = 0, dteMax = 365, onRisk, onData, onAvsr, onReset }: {
  zh: boolean; riskValues: Values; dataPolicy: Values; avsr: Avsr;
  dteMin?: number; dteMax?: number;
  onRisk: (value: Values) => void; onData: (value: Values) => void; onAvsr: (value: Avsr) => void; onReset: () => void;
}) {
  const { ja } = useLocale();
  const anchorChange = (kind: "moneyness_anchors" | "expiry_anchors", index: number, value: number) => onAvsr({ ...avsr, [kind]: avsr[kind].map((old, i) => i === index ? value : old) });
  return <details className="lo-panel p-6" data-testid="research-settings" onInvalidCapture={(event) => {
    let node: HTMLElement | null = event.target as HTMLElement;
    while (node) { if (node instanceof HTMLDetailsElement) node.open = true; node = node.parentElement; }
  }}>
    <summary className="cursor-pointer text-base font-semibold">{zh ? "高级设置 · 风控、行情与 AVSR" : ja("Advanced · risk, data and AVSR")}</summary>
    <p className="mt-3 text-sm text-[var(--color-ink-soft)]">{zh ? "所有数值直接用于本次历史回放。修改后可恢复预设值。" : ja("These values directly affect this replay. You can restore preset values.")}</p>
    <button type="button" className="mt-3 text-sm text-[var(--color-primary-600)]" onClick={onReset}>{zh ? "恢复研究设置预设值" : ja("Restore research defaults")}</button>
    <div className="mt-5 space-y-3">
      <details className="rounded-lg border border-[var(--color-rule)] p-4"><summary className="cursor-pointer font-medium">AVSR · {zh ? "期权链摘要" : ja("option-chain summary")}</summary>
        <p className="mt-3 text-xs text-[var(--color-ink-soft)]">{zh ? "用三个行权价/现价比例（如 95%、100%、105%）与三个到期天数作为代表点，压缩传给模型的期权链。锚点必须从小到大。" : ja("Compress the chain around three strike/spot ratios and three expiry-day anchors, each increasing.")}</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">{avsr.moneyness_anchors.map((value, index) => <label key={`m${index}`} className="lo-field"><span>{zh ? `价内程度锚点 ${index + 1}（%）` : ja("Moneyness anchor {0} (%)", index + 1)}</span><input type="number" aria-label={`${zh ? "价内程度锚点" : ja("Moneyness anchor")} ${index + 1}`} min="50" max="150" step="0.1" value={value * 100} onChange={(event) => anchorChange("moneyness_anchors", index, Number(event.target.value) / 100)} /></label>)}
          {avsr.expiry_anchors.map((value, index) => <label key={`e${index}`} className="lo-field"><span>{zh ? `到期日锚点 ${index + 1}（天）` : ja("Expiry anchor {0} (days)", index + 1)}</span><input type="number" aria-label={`${zh ? "到期日锚点" : ja("Expiry anchor")} ${index + 1}`} min="1" max="365" step="1" value={value} onChange={(event) => anchorChange("expiry_anchors", index, Number(event.target.value))} /></label>)}</div>
        <label className="lo-field mt-3"><span>{zh ? "距离权重 λ（0–1）" : ja("Distance weight λ (0–1)")}</span><input type="number" aria-label={zh ? "距离权重" : ja("Distance weight")} min="0.001" max="1" step="0.001" value={avsr.distance_lambda} onChange={(event) => onAvsr({ ...avsr, distance_lambda: Number(event.target.value) })} /></label>
        <p className="mt-1 text-xs text-[var(--color-ink-soft)]">{zh ? "选择代表合约时，到期天数距离相对行权价/现价距离的权重；数值越大，到期天数影响越强。" : ja("Weight of expiry-day distance relative to strike/spot distance when selecting representative contracts.")}</p>
        <AvsrExample value={avsr} zh={zh} />
      </details>
      <details className="rounded-lg border border-[var(--color-rule)] p-4"><summary className="cursor-pointer font-medium">{zh ? "风控规则" : ja("Risk limits")}</summary><div className="mt-4"><Fields fields={risk} values={riskValues} onChange={onRisk} zh={zh} /></div></details>
      <details className="rounded-lg border border-[var(--color-rule)] p-4"><summary className="cursor-pointer font-medium">{zh ? "行情与期权链数据" : ja("Market and chain data")}</summary><div className="mt-4"><Fields fields={data} values={dataPolicy} onChange={onData} zh={zh} /></div>
        <p className="mt-3 text-xs text-[var(--color-ink-soft)]">{zh ? "缺失 K 线的处理方式固定为保留前值并标记过期。新闻设置在上方单独调整。" : ja("Missing bars use forward-fill with a stale flag. News settings are above.")}</p>
        <DataExample values={dataPolicy} dteMin={dteMin} dteMax={dteMax} zh={zh} />
      </details>
    </div>
  </details>;
}
