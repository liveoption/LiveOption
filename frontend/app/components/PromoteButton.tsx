import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";

import { usePromoteV1DecisionsDecisionIdPromotePost } from "../lib/orval/decisions/decisions";
import { useProblemError } from "../lib/useProblemError";
import { HttpError } from "../lib/http-client";

/**
 * `PromoteButton` — Phase D "Promote" affordance for the decision-list
 * area of `/runs/:runId`.
 *
 * Behaviour:
 *
 *   * Already-promoted rows (`promotedCheckpointId` truthy) render a small
 *     link to `/checkpoints/{id}` instead of a button — the back-link is
 *     the canonical UX cue that promotion already completed.
 *   * Unpromoted rows render a `Promote` button. On click we POST to
 *     `/v1/decisions/{decision_id}/promote` via the orval-generated
 *     mutation hook. On 2xx we navigate to `/checkpoints/{id}`.
 *   * RFC 7807 problem responses (409 already-promoted, 5xx server)
 *     surface an inline tooltip-style message under the button — we keep
 *     this terse so the row layout stays compact.
 *
 * The component is presentational + self-contained so it can be embedded
 * inside any decision-row renderer (timeline, table, inspector) without
 * wiring promote state up the tree. This keeps the parent route file
 * (`runs/$runId.tsx`) untouched, which is important while a sibling
 * sub-agent is concurrently rewriting it for the `/live` merge.
 */

export interface PromoteButtonProps {
  decisionId: string;
  promotedCheckpointId?: string | null;
  /** Optional size override for compact contexts (e.g. timeline rows). */
  size?: "xs" | "sm";
}

function readCheckpointId(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as { id?: unknown; checkpoint_id?: unknown };
  if (typeof obj.id === "string") return obj.id;
  if (typeof obj.checkpoint_id === "string") return obj.checkpoint_id;
  return null;
}

export default function PromoteButton({
  decisionId,
  promotedCheckpointId,
  size = "xs",
}: PromoteButtonProps) {
  const navigate = useNavigate();
  const [conflictId, setConflictId] = useState<string | null>(null);

  const promote = usePromoteV1DecisionsDecisionIdPromotePost({
    mutation: {
      onSuccess: (resp) => {
        const ckptId = readCheckpointId((resp as { data?: unknown }).data);
        if (ckptId) {
          navigate({ to: "/checkpoints/$ckptId", params: { ckptId } });
        }
      },
      onError: (err: unknown) => {
        // 409 already-promoted carries the existing checkpoint_id in
        // the problem `detail` string — surface it so the next click can
        // hop straight to the existing checkpoint.
        if (err instanceof HttpError && err.status === 409) {
          const detail = err.problem?.detail ?? "";
          const m = detail.match(/checkpoint_id=([0-9A-Za-z]+)/);
          if (m) setConflictId(m[1]);
        }
      },
    },
  });

  const problem = useProblemError(promote.error);

  const effectiveCkptId = promotedCheckpointId ?? conflictId;

  if (effectiveCkptId) {
    return (
      <button
        type="button"
        data-testid="promoted-link"
        onClick={(e) => {
          e.stopPropagation();
          navigate({
            to: "/checkpoints/$ckptId",
            params: { ckptId: effectiveCkptId },
          });
        }}
        className={[
          "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
          "bg-emerald-100 text-emerald-800 hover:bg-emerald-200",
          "dark:bg-emerald-900/40 dark:text-emerald-300 dark:hover:bg-emerald-900/60",
        ].join(" ")}
        title={`Promoted → ${effectiveCkptId}`}
      >
        Promoted →
      </button>
    );
  }

  const sizeCls =
    size === "xs"
      ? "px-2 py-0.5 text-xs"
      : "px-3 py-1 text-sm";

  return (
    <span className="inline-flex flex-col items-stretch gap-0.5">
      <button
        type="button"
        data-testid="promote-button"
        disabled={promote.isPending}
        onClick={(e) => {
          e.stopPropagation();
          promote.mutate({ decisionId });
        }}
        className={[
          "rounded border border-indigo-500 font-medium text-indigo-600 transition",
          "hover:bg-indigo-50 dark:border-indigo-400 dark:text-indigo-300",
          "dark:hover:bg-indigo-950 disabled:opacity-50",
          sizeCls,
        ].join(" ")}
      >
        {promote.isPending ? "Promoting…" : "Promote"}
      </button>
      {problem && !conflictId && (
        <span
          role="alert"
          data-testid="promote-error"
          className="font-mono text-[10px] text-red-700 dark:text-red-400"
        >
          {problem.title ?? `HTTP ${problem.status ?? "?"}`}
        </span>
      )}
    </span>
  );
}
