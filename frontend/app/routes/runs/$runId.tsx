import { createFileRoute, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import DecisionTimeline from "../../components/DecisionTimeline";
import DecisionInspector from "../../components/DecisionInspector";
import PnLChart from "../../components/PnLChart";
import EventStreamPanel from "../../components/EventStreamPanel";
import CheckpointBadge from "../../components/CheckpointBadge";
import FamilyDashboard, {
  type FamilyKind,
} from "../../components/run-dashboard/FamilyDashboard";
import AnalysisReport from "../../components/run-dashboard/AnalysisReport";
import RunLogs from "../../components/run-dashboard/RunLogs";
import MarketCharts from "../../components/run-dashboard/MarketCharts";
import AgentHistory from "../../components/run-dashboard/AgentHistory";
import ProblemAlert from "../../components/ProblemAlert";
import RunErrorBanner from "../../components/RunErrorBanner";
import RunTitle from "../../components/RunTitle";
import SSEIndicator from "../../components/SSEIndicator";
import { useProblemError } from "../../lib/useProblemError";

import { useRunData } from "../../lib/run-data/useRunData";
import type { EventHistoryItem } from "../../lib/contracts";
import { requireAuth } from "../../lib/auth-guard";
import { useLocale } from "../../lib/locale";

/**
 * /runs/:runId — unified run detail page.
 *
 * Merged page: auto-detects live vs terminal status from `run.status`:
 *
 *   - status ∈ {pending, running}: SSE subscription active. The event log and
 *     a live-folded PnL series populate from `/api/runs/:id/events`. The run
 *     metadata query polls every 10s as a safety net so the status pill
 *     reflects ground truth even if the SSE pipe stalls.
 *   - otherwise: SSE skipped. Static timeline + PnL series + event history
 *     load from the existing `/v1/runs/:id/{timeline,pnl,events/history}` endpoints.
 *
 * Layout (>=1024px):
 *   ┌──────────────┬───────────────┬────────────────┐
 *   │ Timeline     │ Inspector     │ PnL + Events   │
 *   │ (decisions)  │ (selected)    │ (chart + log)  │
 *   └──────────────┴───────────────┴────────────────┘
 *
 * Below `lg` the panes stack vertically (Timeline → Inspector → PnL → Events).
 *
 * URL-as-state:
 *   - `search.seq` selects a decision → drives the inspector and the nested
 *     drawer route `$runId.decisions.$seq`.
 */

type RunDetailSearch = {
  seq?: number;
  section?: ResultSection;
};

type ResultSection = "chart" | "history" | "timeline" | "strategy" | "performance" | "logs" | "manage";

function parseSearch(raw: Record<string, unknown>): RunDetailSearch {
  const out: RunDetailSearch = {};
  if (raw.seq !== undefined && raw.seq !== null && raw.seq !== "") {
    const n = Number(raw.seq);
    if (Number.isFinite(n)) out.seq = n;
  }
  if (["chart", "history", "timeline", "strategy", "performance", "logs", "manage"].includes(String(raw.section))) {
    out.section = raw.section as ResultSection;
  }
  return out;
}

export const Route = createFileRoute("/runs/$runId")({
  validateSearch: parseSearch,
  loader: async (ctx) => { await requireAuth(ctx); },
  component: RunDetailPage,
});

interface RunMeta {
  id: string;
  scenario?: string | null;
  status?: string | null;
  kind?: string | null;
  title?: string | null;
  created_by?: string | null;
  date_start?: string | null;
  date_end?: string | null;
  model_id?: string | null;
  provider?: string | null;
  universe?: string[] | null;
  family?: FamilyKind | null;
  replay_frequency?: string | null;
  /** Populated by reconciler for failed / completed_with_errors runs. */
  error_message?: string | null;
}

function RunDetailPage() {
  const { runId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = useNavigate();
  const location = useLocation();
  const compareRoute = location.pathname.includes("/compare/");
  const decisionRoute = /\/decisions\/\d+(?:\/|$)/.test(location.pathname);
  const pathSeq = decisionRoute ? Number(location.pathname.match(/\/decisions\/(\d+)/)?.[1]) : null;
  const selectedSeq = pathSeq ?? search.seq ?? null;
  const [activeSection, setActiveSection] = useState<ResultSection>(decisionRoute ? "timeline" : search.section ?? (search.seq == null ? "chart" : "history"));
  const [highlightedSeq, setHighlightedSeq] = useState<number | null>(search.seq ?? null);
  const [hydrated, setHydrated] = useState(false);
  const choseSection = useRef(false);
  const {zh, ja } = useLocale();
  useEffect(() => setHydrated(true), []);
  useEffect(() => { setActiveSection(decisionRoute ? "timeline" : search.section ?? (search.seq == null ? "chart" : "history")); }, [decisionRoute, search.section, search.seq]);
  useEffect(() => { setHighlightedSeq(selectedSeq); }, [selectedSeq, runId]);

  const data = useRunData(runId);
  const { run, live } = data;
  useEffect(() => {
    if (live && !compareRoute && !choseSection.current && search.seq == null && !search.section) setActiveSection("timeline");
  }, [live, compareRoute, search.seq, search.section]);
  const universe = useMemo(() => {
    const value = (run as (typeof run & { universe?: unknown }) | null)?.universe;
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  }, [run]);

  const selectedDecision = useMemo(() => {
    if (selectedSeq == null) return null;
    return data.timeline.find((d) => d.seq === selectedSeq) ?? null;
  }, [data.timeline, selectedSeq]);

  const selectDecision = useCallback(
    (seq: number) => {
      choseSection.current = true;
      setHighlightedSeq(seq);
      setActiveSection("timeline");
      navigate({
        to: "/runs/$runId",
        params: { runId },
        search: { seq, section: "timeline" },
      });
    },
    [navigate, runId],
  );
  const openHistoryDecision = useCallback((seq: number) => {
    choseSection.current = true;
    setHighlightedSeq(seq);
    setActiveSection("history");
    navigate({ to: "/runs/$runId", params: { runId }, search: { section: "history", seq } });
  }, [navigate, runId]);
  const sections: { id: ResultSection; zh: string; en: string }[] = [
    { id: "chart", zh: "行情与决策", en: "Market & decisions" },
    { id: "history", zh: "决策详情", en: "Decision records" },
    { id: "timeline", zh: "进度与盈亏", en: "Progress & P&L" },
    { id: "strategy", zh: "策略分析", en: "Strategy analysis" },
    { id: "performance", zh: "结果总览与报告", en: "Results & report" },
    { id: "logs", zh: "运行日志", en: "Run logs" },
    { id: "manage", zh: "任务管理", en: "Run management" },
  ];

  // Inject a `summary` into each event's payload for the panel's toStreamEvents
  // adapter, which reads `payload.summary` when present. summarizeEvent builds
  // a compact human-readable one-liner per event kind (e.g. "day=2026-01-02 cum_pnl=+12.50").
  const eventsForPanel = useMemo(
    () =>
      data.events.map((e) => ({
        ...e,
        payload: {
          ...e.payload,
          summary:
            typeof (e.payload as { summary?: unknown }).summary === "string"
              ? (e.payload as { summary: string }).summary
              : summarizeEvent(e.kind, e.payload),
        },
      })),
    [data.events],
  );

  // Narrow run to RunMeta shape for Header (run is RunDetail which is a superset).
  const runMeta = run as RunMeta | null;

  // Count distinct severity=error worker.error event rows for this run.
  // We use distinct event-row count (not sum of payload.count) so the header
  // mirrors what's stored in the events table — the banner shows the running
  // total separately. This is intentionally a different, simpler signal.
  const errorEventCount = useMemo(
    () =>
      data.events.filter(
        (e) =>
          e.kind === "worker.error" &&
          (e.payload as { severity?: string } | undefined)?.severity === "error",
      ).length,
    [data.events],
  );

  return (
    <div className="flex flex-col gap-4">
      <Header
        run={runMeta}
        runId={runId}
        loading={data.isLoading}
        live={live}
        sseStatus={data.sseStatus ?? "closed"}
        sseLastEventId={data.sseLastEventId ?? undefined}
        onTitleSaved={data.refetchRun}
        errorEventCount={errorEventCount}
      />

      <RunErrorBanner runId={runId} events={data.events} />
      {["failed", "completed_with_errors"].includes(run?.status ?? "") && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-800"><span>{zh ? "任务执行失败或包含错误。运行日志保留了错误与调用堆栈。" : ja("This run failed or completed with errors. Worker logs contain errors and tracebacks.")}</span><button type="button" className="rounded border border-red-400 px-3 py-2" onClick={() => { choseSection.current = true; setActiveSection("logs"); navigate({ to: "/runs/$runId", params: { runId }, search: { section: "logs" } }); }}>{zh ? "查看错误日志" : ja("View error logs")}</button></div>}

      {compareRoute ? <Outlet /> : <div className="grid min-w-0 gap-4 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label={zh ? "结果分区" : ja("Result sections")} className="lo-panel flex gap-1 overflow-x-auto p-2 lg:sticky lg:top-4 lg:flex-col lg:self-start">
          {sections.map((section) => <button key={section.id} type="button" disabled={!hydrated} data-testid={`result-section-${section.id}`} aria-current={activeSection === section.id ? "page" : undefined}
            onClick={() => { choseSection.current = true; setActiveSection(section.id); navigate({ to: "/runs/$runId", params: { runId }, search: { section: section.id } }); }}
            className={`shrink-0 rounded px-3 py-2 text-left text-sm ${activeSection === section.id ? "bg-[var(--color-primary-600)] text-white" : "text-[var(--color-ink-soft)] hover:bg-[var(--color-surface)]"}`}>
            {zh ? section.zh : ja(section.en)}
          </button>)}
        </nav>
        <div className="min-w-0">
          {activeSection === "chart" && <MarketCharts key={runId} runId={runId} universe={universe} status={run?.status} startDay={run?.date_start} family={(run as RunMeta | null)?.family} dailyBars={run?.scenario === "custom_daily" && (run as RunMeta)?.replay_frequency !== "1m"} decisions={data.timeline} selectedSeq={highlightedSeq} onSelectDecision={openHistoryDecision} />}
          {activeSection === "history" && <AgentHistory decisions={data.timeline} events={data.events} total={data.eventsTotal} truncated={data.eventsTruncated} onLoadOlder={data.eventsLoadOlder} selectedSeq={highlightedSeq} onSelectDecision={openHistoryDecision} />}
          {activeSection === "timeline" && <RunPanels data={data} eventsForPanel={eventsForPanel} selectedDecision={selectedDecision} selectedSeq={selectedSeq} decisionRoute={decisionRoute} onSelectDecision={selectDecision} onOpenRecord={openHistoryDecision} onClearSelection={() => navigate({ to: "/runs/$runId", params: { runId }, search: { section: "timeline" } })} />}
          {activeSection === "strategy" && (run?.family ? <FamilyDashboard family={run.family as FamilyKind} runId={runId} /> : <p className="lo-panel p-5 text-sm">{zh ? "暂无策略分析" : ja("No strategy analysis available")}</p>)}
          {activeSection === "performance" && <AnalysisReport runId={runId} status={run?.status} />}
          {activeSection === "logs" && <RunLogs runId={runId} status={run?.status} />}
          {activeSection === "manage" && <div className="lo-panel p-5"><DeleteRunButton runId={runId} status={run?.status ?? null} /></div>}
        </div>
      </div>}

      {!!(data.errors.run || data.errors.timeline || data.errors.pnl || data.errors.events) && (
        <RunErrorAlerts
          runError={data.errors.run}
          timelineError={data.errors.timeline}
          pnlError={data.errors.pnl}
          // TODO: surface data.errors.events here once RunErrorAlerts accepts it.
          // Events errors mostly degrade to "no events shown" which is non-blocking;
          // timeline/run errors are shown first.
        />
      )}
    </div>
  );
}

/**
 * Single 4-panel layout replacing the old LiveLayout / TerminalLayout split.
 *
 * 3-col grid at ≥ lg breakpoint:
 *   | Timeline | Inspector | (PnL chart on top, EventStreamPanel below) |
 *
 * Mobile (< lg): single column — Timeline → Inspector → PnL → Events stacked.
 *
 * Works identically in both live and terminal modes; the data source differences
 * are handled inside `useRunData` and the `eventsForPanel` adaptation above.
 */
function RunPanels({
  data,
  eventsForPanel,
  selectedDecision,
  selectedSeq,
  decisionRoute,
  onSelectDecision,
  onOpenRecord,
  onClearSelection,
}: {
  data: ReturnType<typeof useRunData>;
  eventsForPanel: EventHistoryItem[];
  selectedDecision: ReturnType<typeof useRunData>["timeline"][number] | null;
  selectedSeq: number | null;
  decisionRoute: boolean;
  onSelectDecision: (seq: number) => void;
  onOpenRecord: (seq: number) => void;
  onClearSelection: () => void;
}) {
  const {zh, ja } = useLocale();
  const [showTradesOnly, setShowTradesOnly] = useState(false);
  const inspector = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selectedSeq !== null && window.matchMedia("(max-width: 639px)").matches) {
      requestAnimationFrame(() => inspector.current?.scrollIntoView({ block: "start", behavior: "smooth" }));
    }
  }, [selectedSeq]);

  const filteredTimeline = useMemo(() => {
    if (!showTradesOnly) return data.timeline;
    return data.timeline.filter((d) => d.has_trade === true);
  }, [data.timeline, showTradesOnly]);

  const tradedCount = useMemo(
    () => data.timeline.filter((d) => d.has_trade === true).length,
    [data.timeline],
  );

  return (
    <div
      data-testid="run-panels"
      className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]"
    >
      {/* Left block: Timeline + Inspector */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
        {/* Timeline */}
        <section
          data-testid="timeline"
          className="flex h-[60vh] max-h-[60vh] flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white dark:border-[var(--color-neutral-800)] dark:bg-[var(--color-neutral-950)]"
        >
          <div className="flex items-center justify-between border-b border-neutral-200 px-3 py-2 dark:border-[var(--color-neutral-800)]">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              {zh ? "决策时间线" : ja("Timeline")}
            </h2>
            <button
              type="button"
              className={[
                "cursor-pointer select-none rounded border px-2 py-1 text-xs",
                showTradesOnly
                  ? "border-emerald-500 bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                  : "border-neutral-300 text-neutral-600 dark:border-neutral-700 dark:text-neutral-300",
              ].join(" ")}
              onClick={() => setShowTradesOnly((v) => !v)}
              aria-pressed={showTradesOnly}
            >
              {showTradesOnly ? `${zh ? "交易" : ja("Trades")}: ${tradedCount}` : (zh ? "全部" : ja("All"))}
            </button>
          </div>
          <div className="flex-1 overflow-auto">
            <DecisionTimeline
              decisions={filteredTimeline}
              selectedSeq={selectedSeq}
              onSelect={onSelectDecision}
              isLoading={data.timelineLoading}
            />
          </div>
        </section>

        {/* Inspector */}
        <section
          data-testid="inspector"
          ref={inspector}
          className="flex min-h-[60vh] min-w-0 scroll-mt-20 flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white dark:border-[var(--color-neutral-800)] dark:bg-[var(--color-neutral-950)]"
        >
          <h2 className="border-b border-neutral-200 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:border-[var(--color-neutral-800)]">
            {zh ? "决策详情" : ja("Inspector")}
          </h2>
          <div className="flex-1">
            {decisionRoute ? <Outlet /> : data.timelineLoading ? <p className="p-4 text-sm text-[var(--color-ink-soft)]">{zh ? "正在加载决策…" : ja("Loading decision…")}</p> : <DecisionInspector decision={selectedDecision} requestedSeq={selectedSeq} onClose={selectedSeq !== null ? onClearSelection : undefined} onOpenRecord={selectedDecision ? () => onOpenRecord(selectedDecision.seq) : undefined} />}
          </div>
        </section>
      </div>

      {/* Right block: PnL chart (top) + EventStreamPanel (bottom) */}
      <section className="flex min-h-[60vh] flex-col gap-4">
        <div
          data-testid="pnl-chart"
          className="overflow-hidden rounded-lg border border-neutral-200 bg-white p-3 dark:border-[var(--color-neutral-800)] dark:bg-[var(--color-neutral-950)]"
        >
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            {zh ? "累计盈亏" : ja("Cumulative PnL")}
          </h2>
          {data.pnlLoading ? (
            <div className="p-6 text-sm text-neutral-500">{zh ? "加载盈亏数据…" : ja("Loading PnL…")}</div>
          ) : (
            <PnLChart points={data.pnlPoints} />
          )}
        </div>

        <EventStreamPanel
          events={eventsForPanel}
          total={data.eventsTotal}
          live={data.live}
          truncated={data.eventsTruncated}
          onLoadOlder={data.eventsLoadOlder}
        />
      </section>
    </div>
  );
}

function Header({
  run,
  runId,
  loading,
  live,
  sseStatus,
  sseLastEventId,
  onTitleSaved,
  errorEventCount,
}: {
  run: RunMeta | null;
  runId: string;
  loading: boolean;
  live: boolean;
  /** `"closed"` is passed when SSE is inactive (terminal mode). */
  sseStatus: "connecting" | "open" | "closed" | "error";
  sseLastEventId: string | undefined;
  onTitleSaved?: () => void;
  /** Distinct count of severity=error worker.error event rows for this run. */
  errorEventCount: number;
}) {
  const {zh, ja } = useLocale();
  const handleScrollToBanner = () => {
    if (typeof document === "undefined") return;
    const el = document.getElementById(`run-error-banner-${runId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      // Best-effort focus for keyboard users.
      (el as HTMLElement).focus?.();
    }
  };
  return (
    <header className="flex flex-col gap-1">
    <div className="flex flex-wrap items-center gap-3">
      <RunTitle
        runId={runId}
        title={run?.title}
        scenario={run?.scenario ?? null}
        onSaved={onTitleSaved}
      />
      {run?.scenario && (
        <span className="inline-flex rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800 dark:bg-[var(--color-indigo-900)]/40 dark:text-indigo-300">
          {run.scenario}
        </span>
      )}
      {run?.status && (
        <StatusPill status={run.status} errorCount={errorEventCount} />
      )}
      {/*
       * StatusPill already conveys "running" (= live). The SSEIndicator
       * shows the SSE connection state; we don't need a separate "Live"
       * pill on top of those two — it was visual noise.
       */}
      {live && (
        <SSEIndicator
          status={sseStatus}
          detail={sseLastEventId ? `#${sseLastEventId}` : null}
        />
      )}
      {run?.kind && (
        <span className="text-xs uppercase tracking-wide text-neutral-600">
          {run.kind}
        </span>
      )}
      <span className="ml-auto font-mono text-xs text-neutral-600">{runId.slice(0, 8)}…</span>
      {/* CheckpointBadge here is intentional noise-free (no id → null) until
          the run-level promoted-checkpoint lookup lands in a later task. */}
      <CheckpointBadge checkpointId={null} />
      {loading && <span className="text-xs text-neutral-500">{zh ? "加载中…" : ja("Loading…")}</span>}
    </div>
    {run?.created_by && (
      <span
        data-testid="run-created-by"
        className="text-xs text-neutral-500"
      >
        by {run.created_by}
      </span>
    )}
    {run?.error_message && (
      <button
        type="button"
        onClick={handleScrollToBanner}
        data-testid="run-last-error"
        className="self-start truncate text-left text-xs text-red-700 hover:underline dark:text-red-300"
        title={run.error_message}
      >
        Last error: {run.error_message}
      </button>
    )}
    </header>
  );
}

function DeleteRunButton({
  runId,
  status,
}: {
  runId: string;
  status: string | null;
}) {
  const { ja } = useLocale();
  const navigate = useNavigate();
  const [deleting, setDeleting] = useState(false);

  // Do not show for runs that are still active.
  if (status === "running" || status === "pending") {
    return null;
  }

  const handleDelete = async () => {
    // Double-check: two-stage confirmation.
    const first = window.confirm(
      ja("Are you sure you want to delete run {0}?\n\nThis will permanently remove the run and all associated data (timeline, checkpoints, questions, events) from the database. This action cannot be undone.", runId)
    );
    if (!first) return;

    const second = window.confirm(
      ja("Final confirmation: permanently delete run {0}?", runId)
    );
    if (!second) return;

    setDeleting(true);
    try {
      const res = await fetch(`/api/runs/${runId}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        let msg = ja("Failed to delete run ({0})", res.status);
        try {
          const parsed = JSON.parse(body) as { detail?: string };
          if (parsed.detail) msg = parsed.detail;
        } catch {
          // ignore
        }
        alert(msg);
        return;
      }
      navigate({ to: "/runs" });
    } catch (err) {
      alert(ja("Network error: {0}", err instanceof Error ? err.message : String(err)));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={handleDelete}
        aria-disabled={deleting}
        tabIndex={deleting ? -1 : 0}
        className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 dark:bg-[var(--color-red-700)] dark:hover:bg-[var(--color-red-600)]"
      >
        {deleting ? ja("Deleting…") : ja("Delete Run")}
      </button>
      <span className="text-xs text-neutral-500">
        {ja("Permanently removes this run and all associated data.")}
      </span>
    </div>
  );
}

function RunErrorAlerts({
  runError,
  timelineError,
  pnlError,
}: {
  runError: unknown;
  timelineError: unknown;
  pnlError: unknown;
}) {
  const runProblem = useProblemError(runError);
  const timelineProblem = useProblemError(timelineError);
  const pnlProblem = useProblemError(pnlError);
  const anyProblem = runProblem ?? timelineProblem ?? pnlProblem;

  if (anyProblem) {
    return <ProblemAlert problem={anyProblem} fallbackTitle="Failed to load run" />;
  }
  return (
    <div
      role="alert"
      className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-[var(--color-red-950)] dark:text-red-300"
    >
      Failed to load part of this run.
    </div>
  );
}

function StatusPill({
  status,
  errorCount = 0,
}: {
  status: string;
  /** Distinct event-row count of severity=error worker.error events. Only
   *  rendered when status is `completed_with_errors`. */
  errorCount?: number;
}) {
  const tone =
    status === "finished" || status === "completed"
      ? "bg-emerald-100 text-emerald-800 dark:bg-[var(--color-emerald-950)]/40 dark:text-[var(--color-emerald-300)]"
      : status === "running"
        ? "bg-sky-100 text-sky-800 dark:bg-[var(--color-sky-900)]/40 dark:text-[var(--color-sky-300)]"
        : status === "errored" || status === "failed" || status === "completed_with_errors"
          ? "bg-red-100 text-red-800 dark:bg-[var(--color-red-950)]/40 dark:text-[var(--color-red-300)]"
          : "bg-neutral-200 text-neutral-700 dark:bg-[var(--color-neutral-800)] dark:text-[var(--color-neutral-300)]";
  const label =
    status === "completed_with_errors"
      ? `Completed (${errorCount} error${errorCount === 1 ? "" : "s"})`
      : status;
  return (
    <span
      data-testid="run-status-pill"
      data-status={status}
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}
    >
      {label}
    </span>
  );
}

/**
 * Compact one-line summary for the live event log. Keep dumb on purpose —
 * the backend payload shapes evolve and we don't want the live panel to
 * blank out when a new optional field appears.
 *
 * Used in `eventsForPanel` to inject a `summary` field into each event's
 * payload so that `EventStreamPanel`'s `toStreamEvents` adapter can render
 * a human-readable one-liner per event (option A from the task spec).
 */
function summarizeEvent(kind: string, data: unknown): string {
  if (!data || typeof data !== "object") return "";
  const d = data as Record<string, unknown>;
  switch (kind) {
    case "day.completed": {
      const day = d.trading_day ?? "";
      const pnl = typeof d.cum_pnl === "number" ? formatPnl(d.cum_pnl) : "—";
      return `day=${day} cum_pnl=${pnl}`;
    }
    case "decision.completed": {
      const seq = d.seq ?? "?";
      const trades = d.trades_count ?? d.n_trades ?? "?";
      return `seq=${seq} trades=${trades}`;
    }
    case "run.started":
    case "run.finished":
      return typeof d.status === "string" ? `status=${d.status}` : "";
    case "session.finished":
      return typeof d.trading_day === "string"
        ? `trading_day=${d.trading_day}`
        : "";
    default: {
      const parts: string[] = [];
      for (const [k, v] of Object.entries(d)) {
        if (parts.length >= 3) break;
        if (v === null || ["string", "number", "boolean"].includes(typeof v)) {
          parts.push(`${k}=${v}`);
        }
      }
      return parts.join(" ");
    }
  }
}

function formatPnl(n: number): string {
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
}
