import CheckpointBadge from "./CheckpointBadge";
import PromoteButton from "./PromoteButton";
import { formatDateTime, formatMoney } from "../lib/format";
import { useLocale } from "../lib/locale";

/**
 * Decision-record shape consumed by the timeline.
 *
 * Mirrors the subset of columns emitted by
 * `GET /v1/runs/{run_id}/timeline` (see
 * `liveoption_web/runs/service.py::get_timeline`). The OpenAPI schema marks
 * this endpoint as an open object, so we define the narrow shape we render
 * here and keep all fields nullable/optional to survive backend churn.
 */
export interface DecisionRow {
  id: string;
  seq: number;
  trading_day?: string | null;
  decision_ts?: string | null;
  has_trade?: boolean | null;
  model_id?: string | null;
  provider?: string | null;
  cum_pnl_after?: number | null;
  promoted_checkpoint_id?: string | null;
}

export interface DecisionTimelineProps {
  /** Decision records ordered by `seq` (ascending). */
  decisions: DecisionRow[];
  /** `seq` of the currently-selected row (drawer target). */
  selectedSeq?: number | null;
  /** Invoked when a row is clicked. */
  onSelect: (seq: number) => void;
  /** Render a loading placeholder when true. */
  isLoading?: boolean;
}

/**
 * Pure presentational list of decision records.
 *
 * The list is an unordered stack of clickable cards. Virtualization is
 * intentionally deferred — typical runs have O(1e2..1e3) decisions, so plain
 * scroll is fine; heavy virtualization already lives in the `/runs` list
 * page.
 */
export default function DecisionTimeline({
  decisions,
  selectedSeq,
  onSelect,
  isLoading,
}: DecisionTimelineProps) {
  const { zh, ja } = useLocale();
  if (isLoading) {
    return (
      <div className="p-4 text-sm text-neutral-500" data-testid="decision-timeline">
        {zh ? "正在加载时间线…" : ja("Loading timeline…")}
      </div>
    );
  }
  if (decisions.length === 0) {
    return (
      <div className="p-4 text-sm text-neutral-500" data-testid="decision-timeline">
        {zh ? "这次运行尚无决策记录。" : ja("No decisions recorded for this run yet.")}
      </div>
    );
  }

  return (
    <ol
      data-testid="decision-timeline"
      aria-label={zh ? "决策时间线" : ja("Decision timeline")}
      className="flex flex-col divide-y divide-neutral-200 dark:divide-[var(--color-neutral-800)]"
    >
      {decisions.map((d) => {
        const active = selectedSeq != null && d.seq === selectedSeq;
        return (
          <li
            key={d.id}
            className={[
              "flex min-h-[44px] w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-sm transition",
              active
                ? "border-l-2 border-l-[var(--color-primary-600)] bg-[var(--color-paper-2)]"
                : "border-l-2 border-l-transparent hover:bg-[var(--color-paper-2)]",
            ].join(" ")}
          >
            <button
              type="button"
              data-testid="decision-row"
              data-seq={d.seq}
              data-active={active ? "true" : "false"}
              aria-current={active ? "true" : undefined}
              aria-label={`${zh ? "决策" : ja("Decision")} ${d.seq}${d.trading_day ? ` · ${d.trading_day}` : ""} · ${d.has_trade ? (zh ? "有交易意图" : ja("Trade proposed")) : (zh ? "未提出交易" : ja("No proposal"))}`}
              onClick={() => onSelect(d.seq)}
              className="grid min-w-0 flex-1 grid-cols-[32px_minmax(0,1fr)] items-start gap-2 rounded py-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-500"
            >
              <span className="font-mono text-xs text-neutral-500">
                #{d.seq}
              </span>
              <span className="min-w-0 space-y-1"><time className="block text-xs text-neutral-700 dark:text-[var(--color-neutral-300)]" dateTime={d.decision_ts ?? undefined}>{d.decision_ts ? `${formatDateTime(d.decision_ts)} ET` : d.trading_day ?? "—"}</time><span className="block truncate text-[11px] text-neutral-500" title={d.provider ? `${d.provider}/${d.model_id ?? "?"}` : d.model_id ?? ""}>{d.model_id ?? "—"}</span><span className="flex flex-wrap items-center gap-2 text-[10px] text-neutral-500"><TradeDot hasTrade={d.has_trade ?? false} /><span>{d.has_trade ? (zh ? "有交易意图" : ja("Trade proposed")) : (zh ? "未提出交易" : ja("No proposal"))}</span>{d.cum_pnl_after != null && <span className="font-mono">{formatMoney(d.cum_pnl_after)}</span>}<CheckpointBadge checkpointId={d.promoted_checkpoint_id} size="xs" /></span></span>
            </button>
            <PromoteButton
              decisionId={d.id}
              promotedCheckpointId={d.promoted_checkpoint_id}
            />
          </li>
        );
      })}
    </ol>
  );
}

function TradeDot({ hasTrade }: { hasTrade: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={[
        "inline-block h-2 w-2 shrink-0 rounded-full",
        hasTrade ? "bg-emerald-500" : "bg-neutral-300 dark:bg-[var(--color-neutral-700)]",
      ].join(" ")}
    />
  );
}
