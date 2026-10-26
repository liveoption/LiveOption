import { useState } from "react";
import { useLocale } from "../../lib/locale";
import KlineChart, { type KlineChartProps } from "./KlineChart";
import ReturnsChart from "./ReturnsChart";

export default function MarketCharts(props: KlineChartProps & { status?: string | null }) {
  const {zh, ja } = useLocale();
  const [view, setView] = useState<"candles" | "returns">("candles");
  const [candlesLayout, setCandlesLayout] = useState<"single" | "facets">("single");
  return <div className="min-w-0 space-y-4">
    <div className="lo-panel flex flex-wrap items-center justify-between gap-3 p-3"><nav role="tablist" aria-label={zh ? "行情图表类型" : ja("Market chart type")} className="flex gap-2">{(["candles", "returns"] as const).map((item) => <button role="tab" type="button" key={item} aria-selected={view === item} onClick={() => setView(item)} className={`rounded-lg px-4 py-2 text-sm ${view === item ? "bg-[var(--color-primary-600)] text-white" : "border border-[var(--color-rule)]"}`}>{item === "candles" ? (zh ? "标的 K 线" : ja("Underlying candles")) : (zh ? "标的与账户收益率" : ja("Asset & account returns"))}</button>)}</nav>
      {view === "candles" && props.universe.length > 1 && <label className="text-sm">{zh ? "显示方式" : ja("Candle layout")}<select aria-label={zh ? "显示方式" : ja("Candle layout")} className="ml-2 rounded border border-[var(--color-rule)] p-2" value={candlesLayout} onChange={(event) => setCandlesLayout(event.target.value as "single" | "facets")}><option value="single">{zh ? "单个标的" : ja("Single underlying")}</option><option value="facets">{zh ? "多资产分面" : ja("Multi-asset panels")}</option></select></label>}
    </div>
    {view === "returns" ? <ReturnsChart runId={props.runId} universe={props.universe} status={props.status} /> : candlesLayout === "single" ? <KlineChart {...props} /> : <div className="space-y-4"><p className="text-xs text-[var(--color-ink-soft)]">{zh ? "每个标的独立价格轴；K 线不直接叠加不同资产的美元价格。收益率对比可切换到同一百分比轴。" : ja("Each asset has its own price axis. Use the returns view to compare assets on a shared percentage axis.")}</p>{props.universe.map((symbol) => <KlineChart key={symbol} {...props} universe={[symbol]} />)}</div>}
  </div>;
}
