/**
 * Zod schemas for the real Plan B backend contract.
 *
 * Plan B's OpenAPI currently declares list/dict endpoint responses as
 * `type: object, additionalProperties: true`, which causes Orval to generate
 * `{[key: string]: unknown}` types. Pages previously bypassed those types
 * with `as unknown as {...}` casts — which is strictly worse than parsing,
 * because a backend shape drift produces a silent blank UI.
 *
 * The schemas here mirror the real service output (see
 * `liveoption_web/db/models.py` + `liveoption_web/api/runs.py` +
 * `liveoption_web/api/checkpoints.py`). Only the top-level envelope + the
 * primary id/seq fields are strictly validated; permissive optional fields
 * use `z.unknown()` or coerced primitives so minor churn (added columns, a
 * null vs missing key) does not explode the UI.
 *
 * Usage:
 *   const page = parseContract(zRunsPage, res.data);
 *
 * On mismatch, `parseContract` throws `ContractError` with the zod issue
 * path and a short snippet of the received payload for debugging.
 */
import { z } from "zod";

/* --------------------------------------------------------------------- */
/* Primitive helpers                                                     */
/* --------------------------------------------------------------------- */

/** Many backend fields are `Optional[str]` — accept string|null|missing. */
const nstr = z.string().nullable().optional();
/** Numeric optional — tolerates null/missing. */
const nnum = z.number().nullable().optional();
/** Boolean optional. */
const nbool = z.boolean().nullable().optional();

/* --------------------------------------------------------------------- */
/* Row schemas — mirror the Pydantic models in liveoption_web/db/models.py */
/* --------------------------------------------------------------------- */

export const zRunRow = z
  .object({
    id: z.string(),
    scenario: z.string().optional(),
    status: z.string().optional(),
    kind: z.string().optional(),
    model_id: nstr,
    provider: nstr,
    date_start: nstr,
    date_end: nstr,
    created_at: nstr,
    is_featured: nbool,
    title: nstr,
    created_by: nstr,
    parent_run_id: nstr,
    parent_checkpoint_id: nstr,
    storage_dir: nstr,
  })
  .passthrough();
export type RunRow = z.infer<typeof zRunRow>;

export const zDecisionRecordRow = z
  .object({
    id: z.string(),
    seq: z.number(),
    run_id: z.string().optional(),
    trading_day: nstr,
    decision_ts: nstr,
    model_id: nstr,
    provider: nstr,
    has_trade: nbool,
    cum_pnl_after: nnum,
    promoted_checkpoint_id: nstr,
  })
  .passthrough();
export type DecisionRecordRow = z.infer<typeof zDecisionRecordRow>;

export const zCheckpointRow = z
  .object({
    id: z.string(),
    run_id: z.string(),
    decision_record_id: nstr,
    storage_dir: nstr,
    fingerprint: nstr,
    portfolio_summary: z.unknown().optional(),
    promoted_at: nstr,
    promoted_by: nstr,
    status: nstr,
    status_updated_at: nstr,
    label: nstr,
  })
  .passthrough();
export type CheckpointRow = z.infer<typeof zCheckpointRow>;

export const zQuestionRow = z
  .object({
    id: z.string(),
    checkpoint_id: z.string(),
    parent_question_id: nstr,
    asked_by: z.string().optional(),
    asked_at: nstr,
    user_prompt: z.string().optional(),
    model_id: nstr,
    provider: nstr,
    response_text: nstr,
    response_tokens: nnum,
    latency_ms: nnum,
    cost_usd: nnum,
    status: nstr,
    status_updated_at: nstr,
    error_message: nstr,
  })
  .passthrough();
export type QuestionRow = z.infer<typeof zQuestionRow>;

export const zPnLPoint = z
  .object({
    trading_day: nstr,
    cum_pnl: nnum,
    portfolio_value: nnum,
  })
  .passthrough();
export type PnLPoint = z.infer<typeof zPnLPoint>;

export const zEventRow = z
  .object({
    id: z.number(),
    run_id: z.string(),
    subprocess_seq: z.number().optional(),
    ts: z.string().optional(),
    kind: z.string(),
    payload_json: z.string().optional(),
  })
  .passthrough();
export type EventRow = z.infer<typeof zEventRow>;

/* --------------------------------------------------------------------- */
/* Envelope schemas                                                       */
/* --------------------------------------------------------------------- */

export const zRunsPage = z.object({
  items: z.array(zRunRow),
  next_cursor: z.string().nullable().optional(),
});
export type RunsPage = z.infer<typeof zRunsPage>;

export const zTimelinePage = z.object({
  items: z.array(zDecisionRecordRow),
});
export type TimelinePage = z.infer<typeof zTimelinePage>;

export const zPnLSeries = z.object({
  items: z.array(zPnLPoint),
});
export type PnLSeries = z.infer<typeof zPnLSeries>;

export const zQuestionsPage = z.object({
  items: z.array(zQuestionRow),
});
export type QuestionsPage = z.infer<typeof zQuestionsPage>;

export const zChildrenPage = z.object({
  items: z.array(zRunRow),
});
export type ChildrenPage = z.infer<typeof zChildrenPage>;

/**
 * Single-row envelope for `GET /v1/runs/:id`. The backend returns the run
 * row directly (no `{data: ...}` wrapper). We reuse `zRunRow` but expose it
 * under a dedicated alias so page-level code reads clearly.
 */
export const zRunDetail = zRunRow;
export type RunDetail = z.infer<typeof zRunDetail>;

export const zEventHistoryItem = z.object({
  id: z.number(),
  kind: z.string(),
  payload: z.record(z.string(), z.unknown()),
  occurred_at: z.string(),
});
export type EventHistoryItem = z.infer<typeof zEventHistoryItem>;

export const zEventHistoryPage = z.object({
  events: z.array(zEventHistoryItem),
  total: z.number(),
  truncated: z.boolean(),
  next_cursor: z.number().nullable(),
});
export type EventHistoryPage = z.infer<typeof zEventHistoryPage>;

/**
 * Loose envelope for `GET /v1/runs/:a/compare/:b`. Inner rows are permissive
 * because the compare projection is service-specific and not worth schema
 * pinning field-by-field.
 */
export const zCompareResponse = z
  .object({
    a: z.record(z.string(), z.unknown()).nullable().optional(),
    b: z.record(z.string(), z.unknown()).nullable().optional(),
    days: z.array(z.record(z.string(), z.unknown())).nullable().optional(),
    decisions: z
      .array(z.object({ seq: z.number() }).passthrough())
      .nullable()
      .optional(),
  })
  .passthrough();
export type CompareEnvelope = z.infer<typeof zCompareResponse>;

/* --------------------------------------------------------------------- */
/* parseContract helper                                                   */
/* --------------------------------------------------------------------- */

export class ContractError extends Error {
  readonly issues: z.ZodIssue[];
  readonly received: unknown;

  constructor(message: string, issues: z.ZodIssue[], received: unknown) {
    super(message);
    this.name = "ContractError";
    this.issues = issues;
    this.received = received;
  }
}

function snippet(raw: unknown): string {
  try {
    const s = JSON.stringify(raw);
    if (s == null) return String(raw);
    return s.length > 240 ? s.slice(0, 240) + "…" : s;
  } catch {
    return "<unserializable>";
  }
}

/**
 * Parse `raw` against `schema`. Throws `ContractError` on mismatch with
 * a human-readable message that includes the zod issue paths and a short
 * snippet of the received payload.
 */
export function parseContract<T>(schema: z.ZodType<T>, raw: unknown): T {
  const result = schema.safeParse(raw);
  if (result.success) return result.data;
  const paths = result.error.issues
    .map((i) => `${i.path.join(".") || "<root>"}: ${i.message}`)
    .join("; ");
  throw new ContractError(
    `Contract mismatch: ${paths} — received ${snippet(raw)}`,
    result.error.issues,
    raw,
  );
}
