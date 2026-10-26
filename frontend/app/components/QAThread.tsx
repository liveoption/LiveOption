/**
 * QAThread — pure, stateless list of Q/A pairs for a checkpoint.
 *
 * The backend's `GET /v1/checkpoints/:id/questions` response shape is an open
 * object per OpenAPI, so we mirror the backend row projection
 * (`liveoption_web/api/checkpoints.py::_question_row_to_dict`) locally.
 *
 * Streaming is a Task 12+ concern: this component renders only the
 * persisted `response_text` plus a status pill hint. When a row has
 * `status === "streaming"`, we show a subtle "…" hint so the caller knows
 * chunks are still landing out of band.
 */
export type QuestionRow = {
  id: string;
  checkpoint_id: string;
  parent_question_id: string | null;
  asked_by: string | null;
  asked_at: string | null;
  user_prompt: string;
  model_id: string | null;
  provider: string | null;
  response_text: string | null;
  response_tokens: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  status: string;
  status_updated_at: string | null;
  error_message: string | null;
};

export function QAThread({ questions }: { questions: QuestionRow[] }) {
  if (questions.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
        No questions yet. Ask one below to inspect this frozen context.
      </div>
    );
  }

  return (
    <ol className="flex flex-col gap-4" data-testid="qa-thread">
      {questions.map((q) => (
        <QAItem key={q.id} q={q} />
      ))}
    </ol>
  );
}

function QAItem({ q }: { q: QuestionRow }) {
  const isStreaming = q.status === "streaming" || q.status === "pending";
  const isError = q.status === "errored" || q.status === "failed";

  return (
    <li
      className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-950"
      data-testid="qa-item"
      data-question-id={q.id}
    >
      <div className="flex items-start gap-2">
        <span className="mt-0.5 inline-flex shrink-0 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300">
          Q
        </span>
        <div className="flex-1">
          <p className="whitespace-pre-wrap text-sm text-neutral-900 dark:text-neutral-100">
            {q.user_prompt}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-neutral-500">
            {q.asked_at && <span>{q.asked_at}</span>}
            {q.asked_by && <span>· {q.asked_by}</span>}
            {q.model_id && <span>· {q.model_id}</span>}
            <StatusPill status={q.status} />
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-start gap-2">
        <span className="mt-0.5 inline-flex shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">
          A
        </span>
        <div className="flex-1 text-sm text-neutral-800 dark:text-neutral-200">
          {isError ? (
            <p className="text-red-700 dark:text-red-300">
              {q.error_message ?? "Answer failed."}
            </p>
          ) : q.response_text ? (
            <p className="whitespace-pre-wrap">{q.response_text}</p>
          ) : isStreaming ? (
            <p className="italic text-neutral-500" data-testid="qa-streaming-hint">
              streaming answer…
            </p>
          ) : (
            <p className="italic text-neutral-500">(no response)</p>
          )}
          {(q.response_tokens != null || q.latency_ms != null || q.cost_usd != null) && (
            <div className="mt-1 flex flex-wrap gap-2 text-xs text-neutral-500">
              {q.response_tokens != null && <span>{q.response_tokens} tok</span>}
              {q.latency_ms != null && <span>· {q.latency_ms} ms</span>}
              {q.cost_usd != null && <span>· ${q.cost_usd.toFixed(4)}</span>}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

function StatusPill({ status }: { status: string }) {
  const cls =
    status === "complete" || status === "done"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
      : status === "streaming" || status === "pending"
        ? "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300"
        : status === "errored" || status === "failed"
          ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
          : "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300";
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${cls}`}>
      {status}
    </span>
  );
}
