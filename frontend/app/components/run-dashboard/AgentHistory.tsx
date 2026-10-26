import { useEffect, useMemo, useState } from "react";
import type { DecisionRecordRow, EventHistoryItem } from "../../lib/contracts";
import { useLocale } from "../../lib/locale";
import PromptDialog from "../PromptDialog";
import ResponseDialog from "../ResponseDialog";

type Artifact = {
  prompt?: unknown; response?: unknown; trades_executed?: unknown;
  context?: unknown; market?: unknown; decision_ts?: string;
};
type Detail = { artifact_available: boolean; artifact: Artifact | null };

function hasRawModelResponse(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const record = value as { raw?: unknown; calls?: unknown };
  return typeof record.raw === "string" ||
    (Array.isArray(record.calls) && record.calls.some((call) => call && typeof call.raw === "string"));
}

function savedRationale(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  for (const key of ["reasoning", "rationale", "reason"]) {
    const text = record[key];
    if (typeof text === "string" && text.trim()) return text.trim();
  }
  for (const key of ["analysis", "parsed", "trade_plan", "reflected_plan"]) {
    const nested = savedRationale(record[key]);
    if (nested) return nested;
  }
  if (typeof record.raw === "string" && record.raw.length < 100_000) {
    try { return savedRationale(JSON.parse(record.raw)); } catch { /* The raw text remains available below. */ }
  }
  return null;
}

function DataBlock({ title, value }: { title: string; value: unknown }) {
  if (value === undefined || value === null) return null;
  const content = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return <details className="min-w-0 rounded border border-[var(--color-rule)] p-3"><summary className="cursor-pointer text-xs font-semibold uppercase text-[var(--color-ink-soft)]">{title}</summary>
    <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-surface)] p-3 font-mono text-xs">{content}</pre>
  </details>;
}

function Pagination({ page, pageCount, onPage, zh }: { page: number; pageCount: number; onPage: (page: number) => void; zh: boolean }) {
  const { ja } = useLocale();
  if (pageCount < 2) return null;
  return <div className="mt-4 flex items-center justify-end gap-3 text-sm">
    <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)} className="rounded border border-[var(--color-rule)] px-3 py-1 disabled:opacity-40">{zh ? "上一页" : ja("Previous")}</button>
    <span className="tabular-nums">{page} / {pageCount}</span>
    <button type="button" disabled={page >= pageCount} onClick={() => onPage(page + 1)} className="rounded border border-[var(--color-rule)] px-3 py-1 disabled:opacity-40">{zh ? "下一页" : ja("Next")}</button>
  </div>;
}

function decisionTime(timestamp: string | null | undefined, day: string | null | undefined, zh: boolean, ja: (text: string) => string = (text) => text): string {
  if (!timestamp) return day ?? "—";
  const parsed = Date.parse(timestamp);
  if (!Number.isFinite(parsed)) return timestamp;
  return new Intl.DateTimeFormat(zh ? "zh-CN" : ja("en-US"), {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(parsed) + " ET";
}

export default function AgentHistory({ decisions, events, total, truncated, onLoadOlder, selectedSeq, onSelectDecision }: {
  decisions: DecisionRecordRow[];
  events: EventHistoryItem[];
  total: number;
  truncated: boolean;
  onLoadOlder: (() => Promise<void>) | null;
  selectedSeq?: number | null;
  onSelectDecision: (seq: number) => void;
}) {
  const {zh, ja } = useLocale();
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  const [details, setDetails] = useState<Record<string, Detail>>({});
  const [loadingId, setLoadingId] = useState("");
  const [errorId, setErrorId] = useState("");
  const [showEvents, setShowEvents] = useState(false);
  const [query, setQuery] = useState("");
  const [tradeFilter, setTradeFilter] = useState<"all" | "trade" | "hold">("all");
  const [decisionPage, setDecisionPage] = useState(1);
  const [eventPage, setEventPage] = useState(1);
  const [promptViewer, setPromptViewer] = useState<{ prompt: unknown; seq: number } | null>(null);
  const [responseViewer, setResponseViewer] = useState<{ response: unknown; seq: number } | null>(null);
  const filteredDecisions = useMemo(() => decisions.filter((decision) => {
    if (tradeFilter === "trade" && decision.has_trade !== true) return false;
    if (tradeFilter === "hold" && decision.has_trade === true) return false;
    const needle = query.trim().toLowerCase();
    return !needle || [String(decision.seq), decision.trading_day, decision.decision_ts, decision.model_id, decision.provider]
      .some((value) => String(value ?? "").toLowerCase().includes(needle));
  }), [decisions, query, tradeFilter]);
  const decisionPageCount = Math.max(1, Math.ceil(filteredDecisions.length / 20));
  const eventPageCount = Math.max(1, Math.ceil(events.length / 25));
  const visibleDecisions = filteredDecisions.slice((decisionPage - 1) * 20, decisionPage * 20);
  const visibleEvents = events.slice((eventPage - 1) * 25, eventPage * 25);
  const selectedDecisionId = decisions.find((item) => item.seq === openSeq)?.id;
  useEffect(() => {
    if (selectedSeq == null) return;
    const index = filteredDecisions.findIndex((decision) => decision.seq === selectedSeq);
    if (index >= 0) setDecisionPage(Math.floor(index / 20) + 1);
    setOpenSeq(selectedSeq);
  }, [selectedSeq, filteredDecisions]);
  useEffect(() => {
    if (selectedSeq == null || openSeq !== selectedSeq) return;
    requestAnimationFrame(() => document.getElementById(`history-decision-${selectedSeq}`)?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }, [selectedSeq, openSeq, decisionPage]);
  useEffect(() => { setDecisionPage((page) => Math.min(page, decisionPageCount)); }, [decisionPageCount]);
  useEffect(() => { setEventPage((page) => Math.min(page, eventPageCount)); }, [eventPageCount]);
  useEffect(() => {
    if (!selectedDecisionId || details[selectedDecisionId]) return;
    const controller = new AbortController();
    setLoadingId(selectedDecisionId); setErrorId("");
    fetch(`/api/decisions/${encodeURIComponent(selectedDecisionId)}`, { signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<Detail>; })
      .then((value) => setDetails((previous) => ({ ...previous, [selectedDecisionId]: value })))
      .catch(() => { if (!controller.signal.aborted) setErrorId(selectedDecisionId); })
      .finally(() => { if (!controller.signal.aborted) setLoadingId(""); });
    return () => controller.abort();
  }, [selectedDecisionId]);

  return <section className="lo-panel p-5" data-testid="agent-history">
    <div><p className="lo-eyebrow">AGENT / AUDIT TRAIL</p><h2 className="mt-1 text-lg font-semibold">{zh ? "Agent 完整操作历史" : ja("Complete agent operation history")}</h2>
      <p className="mt-2 text-xs text-[var(--color-ink-soft)]">{zh ? "逐次查看实际 Prompt、模型响应、交易意图和原始事件。旧任务若未保存某项内容，会明确显示缺失。" : ja("Inspect actual prompts, model responses, proposed trades and raw events. Missing older artifacts are shown explicitly.")}</p></div>
    <div className="mt-5 space-y-5">
      <div className="max-w-4xl"><h3 className="mb-3 text-sm font-semibold">{zh ? `决策记录 · ${filteredDecisions.length}/${decisions.length}` : ja("Decisions · {0}/{1}", filteredDecisions.length, decisions.length)}</h3>
        <div className="mb-4 flex flex-wrap gap-2">
          <input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setDecisionPage(1); }}
            aria-label={zh ? "搜索决策编号、日期或模型" : ja("Search decision number, date or model")}
            placeholder={zh ? "搜索编号、日期、模型" : ja("Search number, date, model")}
            className="min-w-0 flex-1 rounded border border-[var(--color-rule)] px-3 py-2 text-sm" />
          <select value={tradeFilter} onChange={(event) => { setTradeFilter(event.target.value as typeof tradeFilter); setDecisionPage(1); }}
            aria-label={zh ? "交易意图筛选" : ja("Trade intent filter")} className="rounded border border-[var(--color-rule)] px-3 py-2 text-sm">
            <option value="all">{zh ? "全部决策" : ja("All decisions")}</option>
            <option value="trade">{zh ? "有交易意图" : ja("Trade proposed")}</option>
            <option value="hold">{zh ? "未交易" : ja("No trade")}</option>
          </select>
        </div>
        {!decisions.length && <p className="text-sm text-[var(--color-ink-soft)]">{zh ? "暂无决策记录" : ja("No decisions recorded")}</p>}
        {!!decisions.length && !filteredDecisions.length && <p className="rounded border border-[var(--color-rule)] p-4 text-sm text-[var(--color-ink-soft)]">{zh ? "没有匹配的决策。请调整搜索或筛选条件。" : ja("No matching decisions. Change the search or filter.")}</p>}
        <ol className="space-y-2">{visibleDecisions.map((decision) => {
          const open = openSeq === decision.seq;
          const detail = details[decision.id];
          const artifact = detail?.artifact;
          const rationale = savedRationale(artifact?.response);
          return <li id={`history-decision-${decision.seq}`} key={decision.id} className={`rounded border ${open ? "border-[var(--color-primary-600)]" : "border-[var(--color-rule)]"}`}>
            <button type="button" className="flex w-full flex-wrap items-center gap-3 p-3 text-left text-sm" aria-expanded={open} onClick={() => { setOpenSeq(open ? null : decision.seq); if (!open) onSelectDecision(decision.seq); }}>
              <span className="font-mono font-semibold">#{decision.seq}</span><time title={decision.decision_ts ?? undefined}>{decisionTime(decision.decision_ts, decision.trading_day, zh, ja)}</time>
              <span className={decision.has_trade ? "text-emerald-700" : "text-amber-700"}>{decision.has_trade ? (zh ? "有交易意图" : ja("Trade proposed")) : (zh ? "未交易" : ja("No trade"))}</span>
              <span className="ml-auto text-xs text-[var(--color-ink-soft)]">{decision.model_id}</span>
            </button>
            {open && <div className="grid gap-4 border-t border-[var(--color-rule)] p-4">
              {loadingId === decision.id && <p className="text-sm">{zh ? "加载原始记录…" : ja("Loading artifact…")}</p>}
              {errorId === decision.id && <p role="alert" className="text-sm text-red-600">{zh ? "无法读取这次决策的原始记录" : ja("Could not load this decision artifact")}</p>}
              {detail && !detail.artifact_available && <p className="text-sm text-[var(--color-ink-soft)]">{zh ? "这次决策没有保存 Prompt/响应文件；可查看下方原始事件。" : ja("Prompt/response artifact was not saved for this decision; raw events remain below.")}</p>}
              {artifact && <>{rationale && <div className="rounded border border-[var(--color-rule)] bg-[var(--color-surface)] p-3 text-sm"><p className="mb-1 font-semibold">{zh ? "保存的决策理由" : ja("Saved decision rationale")}</p><p className="whitespace-pre-wrap">{rationale}</p></div>}
                {!hasRawModelResponse(artifact.response) && <p className="text-xs text-[var(--color-ink-soft)]">{rationale ? (zh ? "已保存结构化结果；原始模型文本未保存。" : ja("Structured output is saved; raw model text was not saved.")) : (zh ? "没有可读取的原始模型回复；可能由规则跳过，或旧任务未保存。" : ja("No raw model reply is available; this may be a rule skip or an older run without saved text."))}</p>}
                {artifact.prompt != null && <button type="button" onClick={() => setPromptViewer({ prompt: artifact.prompt, seq: decision.seq })} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--color-rule)] p-4 text-left text-sm text-[var(--color-primary-600)]"><span className="font-semibold">{zh ? "实际 Prompt" : ja("Actual prompt")}</span><span className="text-xs">{zh ? "打开窗口 · System / User · 字段数据 · JSON" : ja("Open viewer · System / User · fields · JSON")}</span></button>}
                {artifact.response != null && <button type="button" onClick={() => setResponseViewer({ response: artifact.response, seq: decision.seq })} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--color-rule)] p-4 text-left text-sm text-[var(--color-primary-600)]"><span className="font-semibold">{zh ? "模型响应" : ja("Model response")}</span><span className="text-xs">{zh ? "打开窗口 · 分阶段解析 · 理由与订单 · JSON" : ja("Open viewer · phases · reasoning & orders · JSON")}</span></button>}
                <DataBlock title={zh ? "交易意图" : ja("Proposed trades")} value={artifact.trades_executed} />
                <DataBlock title="Context" value={artifact.context} /><DataBlock title={zh ? "行情快照" : ja("Market snapshot")} value={artifact.market} /></>}
            </div>}
          </li>;
        })}</ol>
        <Pagination page={decisionPage} pageCount={decisionPageCount} onPage={setDecisionPage} zh={zh} />
      </div>
      <div className="max-w-4xl border-t border-[var(--color-rule)] pt-4"><button type="button" onClick={() => setShowEvents(!showEvents)} className="mb-3 flex w-full items-center justify-between text-left text-sm font-semibold" aria-expanded={showEvents}>
        <span>{zh ? `原始事件 · 已加载 ${events.length}/${total}` : ja("Raw events · loaded {0}/{1}", events.length, total)}</span><span>{showEvents ? "−" : "+"}</span></button>
        {showEvents && <><ol className="space-y-2">{visibleEvents.map((event) => <li key={event.id} className="rounded border border-[var(--color-rule)] p-3 text-xs">
          <div className="flex flex-wrap justify-between gap-2"><span className="font-mono font-semibold">{event.kind}</span><time className="text-[var(--color-ink-soft)]">{event.occurred_at}</time></div>
          <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px]">{JSON.stringify(event.payload, null, 2)}</pre>
        </li>)}</ol>
          <Pagination page={eventPage} pageCount={eventPageCount} onPage={setEventPage} zh={zh} />
          {truncated && onLoadOlder && <button type="button" className="mt-3 text-sm text-[var(--color-primary-600)]" onClick={() => void onLoadOlder()}>{zh ? "加载更早事件" : ja("Load older events")}</button>}
        </>}
      </div>
    </div>
    {promptViewer && <PromptDialog prompt={promptViewer.prompt} title={`Decision ${promptViewer.seq} · Prompt`} onClose={() => setPromptViewer(null)} />}
    {responseViewer && <ResponseDialog response={responseViewer.response} title={`Decision ${responseViewer.seq} · ${zh ? "模型响应" : ja("Model response")}`} onClose={() => setResponseViewer(null)} />}
  </section>;
}
