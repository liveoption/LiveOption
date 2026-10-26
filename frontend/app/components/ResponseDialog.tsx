import { useEffect, useMemo, useRef, useState } from "react";
import { useLocale } from "../lib/locale";
import { responseCalls, responseRecord, responseSummary } from "../lib/responses";
import { ReadablePrompt } from "./PromptText";

const labels: Record<string, [string, string]> = {
  intent: ["市场判断", "Intent"], confidence: ["置信度", "Confidence"], reasoning: ["判断理由", "Reasoning"],
  rationale: ["理由", "Rationale"], reason: ["原因", "Reason"], target_instruments: ["目标合约", "Target instruments"],
  actions: ["建议操作", "Proposed actions"], orders: ["建议订单", "Proposed orders"], action: ["操作", "Action"],
  symbol: ["合约代码", "Symbol"], ticker: ["标的", "Ticker"], quantity: ["数量", "Quantity"], qty: ["数量", "Quantity"],
  side: ["方向", "Side"], order_type: ["订单类型", "Order type"], limit_price: ["限价", "Limit price"],
  analysis: ["分析", "Analysis"], trade_plan: ["交易计划", "Trade plan"], reflected_plan: ["复核计划", "Reviewed plan"],
  intents_count: ["生成的交易意图数", "Generated trade intents"], spread_group: ["组合标识", "Spread group"],
};
const values: Record<string, [string, string]> = {
  bullish: ["看涨", "Bullish"], bearish: ["看跌", "Bearish"], neutral: ["中性", "Neutral"],
  BUY: ["买入", "Buy"], SELL: ["卖出", "Sell"], HOLD: ["持有 / 不操作", "Hold"],
};

function ResponseValue({ value, field = "", depth = 0 }: { value: unknown; field?: string; depth?: number }) {
  const { zh, ja } = useLocale();
  const [page, setPage] = useState(0);
  const label = (key: string) => labels[key] ? (zh ? labels[key][0] : ja(labels[key][1])) : key;
  if (depth > 16) return <pre className="overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(value, null, 2)}</pre>;
  if (value === null || value === undefined) return <span className="text-[var(--color-ink-soft)]">{zh ? "未提供（null）" : ja("Not provided (null)")}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <p className="rounded border border-dashed border-[var(--color-rule)] p-4 text-sm">{zh ? "空列表（0 项）" : ja("Empty list (0 items)")}</p>;
    const visible = value.slice(page * 20, (page + 1) * 20);
    const records = visible.map(responseRecord);
    const columns = [...new Set(records.flatMap(item => item ? Object.keys(item) : []))];
    const table = records.every(item => item && Object.keys(item).length) && columns.length <= 10;
    return <div className="min-w-0 space-y-3">
      {table ? <div className="max-w-full overflow-x-auto rounded border border-[var(--color-rule)]"><table className="w-full text-left text-sm"><thead className="bg-[var(--color-paper-2)]"><tr>{columns.map(key => <th key={key} className="px-3 py-2 font-medium">{label(key)}</th>)}</tr></thead><tbody>{records.map((item, index) => <tr key={index} className="border-t border-[var(--color-rule)]">{columns.map(key => <td key={key} className="min-w-28 max-w-md px-3 py-2 align-top">{Object.hasOwn(item!, key) ? <ResponseValue value={item![key]} field={key} depth={depth + 1} /> : "—"}</td>)}</tr>)}</tbody></table></div> : <ol className="space-y-2">{visible.map((item, index) => <li key={index} className="flex min-w-0 gap-3 rounded border border-[var(--color-rule)] p-3"><span className="shrink-0 text-xs text-[var(--color-ink-soft)]">{page * 20 + index + 1}.</span><div className="min-w-0 flex-1"><ResponseValue value={item} depth={depth + 1} /></div></li>)}</ol>}
      {value.length > 20 && <div className="flex items-center justify-end gap-3 text-sm"><button type="button" disabled={!page} onClick={() => setPage(page - 1)} className="rounded border px-3 py-1 disabled:opacity-40">{zh ? "上一页" : ja("Previous")}</button><span>{page + 1} / {Math.ceil(value.length / 20)}</span><button type="button" disabled={(page + 1) * 20 >= value.length} onClick={() => setPage(page + 1)} className="rounded border px-3 py-1 disabled:opacity-40">{zh ? "下一页" : ja("Next")}</button></div>}
    </div>;
  }
  const record = responseRecord(value);
  if (record) return Object.keys(record).length ? <dl className="space-y-4">{Object.entries(record).map(([key, item]) => <div key={key} className="min-w-0 rounded-lg border border-[var(--color-rule)] p-4"><dt className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold">{label(key)}{labels[key] && <code className="text-xs font-normal text-[var(--color-ink-soft)]">{key}</code>}</dt><dd className="min-w-0"><ResponseValue value={item} field={key} depth={depth + 1} /></dd></div>)}</dl> : <p>{zh ? "空对象（0 个字段）" : ja("Empty object (0 fields)")}</p>;
  if (typeof value === "number") return <span className="font-mono text-sm">{field === "confidence" && value >= 0 && value <= 1 ? `${Number((value * 100).toFixed(4))}% (${value})` : String(value)}</span>;
  if (typeof value === "boolean") return <span>{value ? (zh ? "是（true）" : ja("Yes (true)")) : (zh ? "否（false）" : ja("No (false)"))}</span>;
  if (typeof value === "string" && (field === "intent" || field === "action" || field === "side") && values[value]) return <span className="inline-block rounded bg-[var(--color-paper-2)] px-2 py-1 text-sm">{zh ? values[value][0] : ja(values[value][1])} <code>({value})</code></span>;
  if (value === "") return <span className="text-[var(--color-ink-soft)]">{zh ? "空文本" : ja("Empty text")}</span>;
  return <ReadablePrompt parts={[{ text: String(value) }]} />;
}

export default function ResponseDialog({ response, title, onClose }: { response: unknown; title: string; onClose: () => void }) {
  const { zh, ja } = useLocale();
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef<Element | null>(null);
  const calls = useMemo(() => responseCalls(response), [response]);
  const summary = useMemo(() => responseSummary(response), [response]);
  const raw = useMemo(() => JSON.stringify(response, null, 2) ?? "null", [response]);
  const [phase, setPhase] = useState(0), [view, setView] = useState<"readable" | "text" | "json">("readable");
  const [copyState, setCopyState] = useState("");
  const call = calls[phase];
  useEffect(() => {
    opener.current ??= document.activeElement;
    const overflow = document.body.style.overflow; document.body.style.overflow = "hidden";
    dialog.current?.showModal();
    return () => { document.body.style.overflow = overflow; requestAnimationFrame(() => { if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus(); }); };
  }, []);
  function download() {
    const url = URL.createObjectURL(new Blob([raw], { type: "application/json;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `${title.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80) || "response"}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copy() {
    try { await navigator.clipboard.writeText(raw); setCopyState(zh ? "已复制" : ja("Copied")); }
    catch { setCopyState(zh ? "复制失败，请下载 JSON" : ja("Copy failed. Download JSON instead.")); }
  }
  const phases: Record<string, string> = { analyzer: zh ? "分析" : ja("Analysis"), executor: zh ? "执行" : ja("Execution") };
  const phaseName = (name: string) => phases[name] ? `${phases[name]} · ${name}` : name;
  return <dialog ref={dialog} aria-labelledby="response-dialog-title" onCancel={onClose} onClose={onClose} className="m-auto h-[92dvh] max-h-none w-[min(1300px,96vw)] max-w-none rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-0 text-[var(--color-ink)] shadow-2xl backdrop:bg-black/50" data-testid="response-dialog">
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-rule)] p-4"><div className="min-w-0"><p className="lo-eyebrow">RESPONSE INSPECTOR</p><h2 id="response-dialog-title" className="break-words text-lg font-semibold">{title}</h2></div><button type="button" onClick={onClose} aria-label={zh ? "关闭响应窗口" : ja("Close response window")} className="shrink-0 rounded border border-[var(--color-rule)] px-3 py-2 text-sm">{zh ? "关闭" : ja("Close")}</button></header>
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--color-rule)] p-4">
        <nav className="flex flex-wrap gap-2" role="tablist" aria-label={zh ? "响应查看方式" : ja("Response view")}>{(["readable", "text", "json"] as const).map(id => <button type="button" key={id} role="tab" aria-selected={view === id} onClick={() => setView(id)} className={`rounded px-3 py-2 text-sm ${view === id ? "bg-[var(--color-primary-600)] text-white" : "border border-[var(--color-rule)]"}`}>{id === "readable" ? (zh ? "可读响应" : ja("Readable response")) : id === "text" ? (zh ? "原始响应文本" : ja("Original response text")) : "Raw JSON"}</button>)}</nav>
        <div className="flex flex-wrap items-center gap-2 text-xs"><button type="button" onClick={() => void copy()} className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "复制 JSON" : ja("Copy JSON")}</button><button type="button" onClick={download} className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "下载原始 JSON" : ja("Download raw JSON")}</button>{copyState && <span role="status">{copyState}</span>}</div>
      </div>
      {view !== "json" && <div className="flex shrink-0 flex-wrap items-center gap-3 px-4 py-3 text-xs"><label className="flex items-center gap-2">{zh ? "调用阶段" : ja("Call phase")}<select aria-label={zh ? "响应调用阶段" : ja("Response call phase")} value={phase} onChange={event => setPhase(Number(event.target.value))} className="max-w-64 rounded border border-[var(--color-rule)] bg-[var(--color-paper)] p-2">{calls.map((item, index) => <option key={index} value={index}>{index + 1} · {phaseName(item.phase)}</option>)}</select></label><span>{zh ? "这里展示模型输出；建议订单与交易意图不代表实际成交。" : ja("These are model outputs. Proposed orders and trade intents do not confirm fills.")}</span></div>}
      {view === "json" ? <pre tabIndex={0} data-testid="response-raw-json" className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-[var(--color-paper-2)] p-5 font-mono text-xs">{raw}</pre> : view === "text" ? <pre tabIndex={0} data-testid="response-original-text" className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-[var(--color-paper-2)] p-5 font-mono text-sm">{call.raw ?? (zh ? "未保存原始响应文本，可在可读响应中查看已保存的结构化数据。" : ja("Original response text was not saved. Saved structured data is available in the readable view."))}</pre> : <div className="min-h-0 flex-1 space-y-4 overflow-auto overscroll-contain p-4" data-testid="response-readable">
        {summary && <details className="rounded border border-[var(--color-rule)] p-3"><summary className="cursor-pointer text-sm">{zh ? "整次决策的汇总字段" : ja("Decision summary fields")}</summary><div className="mt-3"><ResponseValue value={summary} /></div></details>}
        {call.format === "text" && <p className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100">{zh ? "响应无法完整解析为 JSON，保留原文显示。" : ja("The response is not a complete JSON document. Original text is shown.")}</p>}
        {call.format === "missing" ? <p>{zh ? "此阶段未保存响应内容。" : ja("No response content was saved for this phase.")}</p> : <ResponseValue key={phase} value={call.content} />}
        {call.saved && <details className="rounded border border-[var(--color-rule)] p-3"><summary className="cursor-pointer text-sm">{zh ? "其他已保存字段" : ja("Other saved fields")}</summary><div className="mt-3"><ResponseValue key={phase} value={call.saved} /></div></details>}
      </div>}
    </div>
  </dialog>;
}
