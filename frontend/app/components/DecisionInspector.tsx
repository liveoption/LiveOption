import CheckpointBadge from "./CheckpointBadge";
import { formatDateTime, formatMoney } from "../lib/format";
import type { DecisionRow } from "./DecisionTimeline";
import { useLocale } from "../lib/locale";

export interface DecisionInspectorProps {
  decision: DecisionRow | null | undefined;
  /** Called when the user dismisses the drawer. */
  onClose?: () => void;
  onOpenRecord?: () => void;
  requestedSeq?: number | null;
}

/**
 * Detail panel for a single decision record.
 *
 * Metadata summary for query selections and legacy nested decision links.
 * Heavy prompt/response artifacts load only when the user opens the complete
 * decision record through the parent's navigation callback.
 */
export default function DecisionInspector({ decision, onClose, onOpenRecord, requestedSeq }: DecisionInspectorProps) {
  const {zh, ja } = useLocale();
  if (!decision) {
    return (
      <aside
        data-testid="decision-inspector-empty"
        className="flex h-full items-center justify-center p-6 text-sm text-neutral-500"
      >
        {requestedSeq != null ? (zh ? `未找到决策 #${requestedSeq}，请从时间线选择一条已有记录。` : ja("Decision #{0} was not found. Choose an existing timeline record.", requestedSeq)) : (zh ? "选择一次决策查看详情。" : ja("Select a decision on the left to inspect it."))}
      </aside>
    );
  }

  return (
    <aside
      data-testid="decision-inspector"
      className="flex h-full flex-col gap-3 overflow-auto p-4"
    >
      <header className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">
            {zh ? "决策" : ja("Decision")} #{decision.seq}
          </h2>
          <p className="truncate font-mono text-xs text-neutral-500">{decision.id}</p>
        </div>
        <div className="flex items-center gap-2">
          <CheckpointBadge
            checkpointId={decision.promoted_checkpoint_id}
            href={
              decision.promoted_checkpoint_id
                ? `/checkpoints/${decision.promoted_checkpoint_id}`
                : undefined
            }
          />
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close inspector"
              className="rounded-md px-2 py-1 text-sm text-neutral-500 hover:bg-neutral-100 dark:hover:bg-[var(--color-neutral-900)]"
            >
              ✕
            </button>
          )}
        </div>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <Field label={zh ? "交易日" : ja("Trading day")} value={decision.trading_day ?? "—"} />
        <Field label={zh ? "决策时间（ET）" : ja("Decided at (ET)")} value={formatDateTime(decision.decision_ts) || "—"} />
        <Field
          label={zh ? "模型" : ja("Model")}
          value={
            decision.provider
              ? `${decision.provider}/${decision.model_id ?? "?"}`
              : decision.model_id ?? "—"
          }
        />
        <Field label={zh ? "交易意图" : ja("Trade proposed")} value={decision.has_trade ? (zh ? "有" : ja("yes")) : (zh ? "无" : ja("no"))} />
        <Field
          label={zh ? "累计盈亏" : ja("Cum PnL")}
          value={formatMoney(decision.cum_pnl_after ?? null)}
        />
      </dl>

      {onOpenRecord && <button type="button" data-testid="open-decision-record" className="mt-3 rounded-lg border border-[var(--color-primary-600)] px-3 py-3 text-left text-sm text-[var(--color-primary-600)]" onClick={onOpenRecord}><span className="block font-medium">{zh ? "打开完整决策记录" : ja("Open full decision record")}</span><span className="mt-1 block text-xs">{zh ? "查看 Prompt、模型响应与操作历史" : ja("View prompt, model response and action history")}</span></button>}
    </aside>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className="truncate text-neutral-900 dark:text-[var(--color-neutral-100)]">{value}</dd>
    </>
  );
}
