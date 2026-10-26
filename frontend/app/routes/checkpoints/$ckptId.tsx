import { createFileRoute, Link, Outlet, useNavigate } from "@tanstack/react-router";
import {
  useGetCheckpointV1CheckpointsCheckpointIdGet,
  useListCheckpointQuestionsV1CheckpointsCheckpointIdQuestionsGet,
} from "../../lib/orval/checkpoints/checkpoints";
import { QAThread, type QuestionRow } from "../../components/QAThread";
import { AskPanel } from "../../components/AskPanel";
import FamilyCheckpointView from "../../components/checkpoint-view/FamilyCheckpointView";
import ProblemAlert from "../../components/ProblemAlert";
import { useProblemError } from "../../lib/useProblemError";
import { parseContract, zCheckpointRow, zQuestionsPage } from "../../lib/contracts";
import { requireAuth } from "../../lib/auth-guard";

/**
 * /checkpoints/:ckptId — frozen-context inspector.
 *
 * Sections:
 *   - Header w/ id, run, promotion status badge
 *   - Frozen context card (decision_record_id, fingerprint, portfolio summary)
 *   - Q&A thread + ask form
 *   - Fork button (routes to /checkpoints/:id/fork — ForkDrawer is Task 9)
 *
 * Data shape is the backend row projection from
 * `liveoption_web/api/checkpoints.py::_checkpoint_row_to_dict`. The generated
 * type is opaque (`{ [key: string]: unknown }`) so we mirror the narrow read
 * shape locally.
 *
 * CheckpointBadge is owned by Task 7. If it has not been committed yet, we
 * render a minimal local fallback; swap the import when Task 7 lands.
 * TODO(plan-c task 7): import { CheckpointBadge } from "../../components/CheckpointBadge".
 */

type CheckpointDetail = {
  id: string;
  run_id: string;
  decision_record_id: string | null;
  storage_dir: string | null;
  fingerprint: string | null;
  portfolio_summary: unknown;
  promoted_at: string | null;
  promoted_by: string | null;
  status: string | null;
  status_updated_at: string | null;
  label: string | null;
};

export const Route = createFileRoute("/checkpoints/$ckptId")({
  loader: async (ctx) => { await requireAuth(ctx); },
  component: CheckpointDetailPage,
});

function CheckpointDetailPage() {
  const { ckptId } = Route.useParams();
  const navigate = useNavigate();

  const ckptQuery = useGetCheckpointV1CheckpointsCheckpointIdGet(ckptId);
  const questionsQuery = useListCheckpointQuestionsV1CheckpointsCheckpointIdQuestionsGet(ckptId);

  if (ckptQuery.isLoading) {
    return <div className="p-6 text-sm text-neutral-500">Loading checkpoint…</div>;
  }
  if (ckptQuery.isError) {
    return (
      <CheckpointErrorFallback error={ckptQuery.error} ckptId={ckptId} />
    );
  }
  let ckpt: CheckpointDetail | null = null;
  if (ckptQuery.data?.data != null) {
    try {
      ckpt = parseContract(zCheckpointRow, ckptQuery.data.data) as CheckpointDetail;
    } catch {
      ckpt = null;
    }
  }
  if (!ckpt) {
    return <div className="p-6 text-sm text-neutral-500">Checkpoint not found.</div>;
  }

  let questions: QuestionRow[] = [];
  if (questionsQuery.data?.data != null) {
    try {
      questions = parseContract(zQuestionsPage, questionsQuery.data.data)
        .items as QuestionRow[];
    } catch {
      questions = [];
    }
  }

  return (
    <div className="flex flex-col gap-6" data-testid="checkpoint-detail">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">
          Checkpoint
          <span className="ml-2 font-mono text-base text-neutral-500">{ckpt.id}</span>
        </h1>
        <LocalCheckpointBadge status={ckpt.status} label={ckpt.label} />
        <div className="ml-auto flex items-center gap-2">
          <Link
            to="/runs/$runId"
            params={{ runId: ckpt.run_id }}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            Back to run
          </Link>
          <button
            type="button"
            onClick={() =>
              navigate({ to: "/checkpoints/$ckptId/fork", params: { ckptId: ckpt.id } })
            }
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-500"
            data-testid="fork-button"
          >
            Fork
          </button>
        </div>
      </header>

      <section
        className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-950"
        data-testid="frozen-context"
      >
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          Frozen context
        </h2>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
          <Row label="Run">
            <Link
              to="/runs/$runId"
              params={{ runId: ckpt.run_id }}
              className="font-mono text-indigo-600 hover:underline dark:text-indigo-400"
            >
              {ckpt.run_id}
            </Link>
          </Row>
          <Row label="Decision record">
            <span className="font-mono">{ckpt.decision_record_id ?? "—"}</span>
          </Row>
          <Row label="Fingerprint">
            <span className="font-mono text-xs">{ckpt.fingerprint ?? "—"}</span>
          </Row>
          <Row label="Promoted at">{ckpt.promoted_at ?? "—"}</Row>
          <Row label="Promoted by">{ckpt.promoted_by ?? "—"}</Row>
          <Row label="Storage dir">
            <span className="font-mono text-xs break-all">{ckpt.storage_dir ?? "—"}</span>
          </Row>
        </dl>

        {ckpt.portfolio_summary != null && (
          <details className="mt-4" data-testid="portfolio-summary">
            <summary className="cursor-pointer text-sm font-medium text-neutral-700 dark:text-neutral-300">
              Portfolio summary
            </summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded bg-neutral-100 p-3 text-xs dark:bg-neutral-900">
              {JSON.stringify(ckpt.portfolio_summary, null, 2)}
            </pre>
          </details>
        )}
      </section>

      <FamilyCheckpointView portfolioSummary={ckpt.portfolio_summary} />

      <section className="flex flex-col gap-4" data-testid="qa-section">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            Questions & Answers
          </h2>
          <span className="text-xs text-neutral-500">
            {questionsQuery.isLoading
              ? "loading…"
              : `${questions.length} question${questions.length === 1 ? "" : "s"}`}
          </span>
        </div>
        {questionsQuery.isError ? (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            Failed to load questions.
          </div>
        ) : (
          <QAThread questions={questions} />
        )}
        <AskPanel checkpointId={ckpt.id} />
      </section>

      {/* Drawer-style child route mounts here (e.g. /:id/ask, /:id/fork). */}
      <Outlet />
    </div>
  );
}

function CheckpointErrorFallback({
  error,
  ckptId,
}: {
  error: unknown;
  ckptId: string;
}) {
  const problem = useProblemError(error);
  if (problem) {
    return (
      <ProblemAlert
        problem={problem}
        fallbackTitle={`Failed to load checkpoint ${ckptId}`}
      />
    );
  }
  return (
    <div
      role="alert"
      className="rounded-md bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
    >
      Failed to load checkpoint {ckptId}.
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs uppercase tracking-wide text-neutral-500">{label}</dt>
      <dd className="text-neutral-900 dark:text-neutral-100">{children}</dd>
    </div>
  );
}

/**
 * Minimal local badge used until Task 7 ships `CheckpointBadge.tsx`.
 * Mirrors the promote status semantics from Plan B (fresh|promoted|stale).
 */
function LocalCheckpointBadge({
  status,
  label,
}: {
  status: string | null;
  label: string | null;
}) {
  const s = (status ?? "fresh").toLowerCase();
  const cls =
    s === "promoted"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
      : s === "stale"
        ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
        : "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}
      data-testid="checkpoint-badge"
    >
      <span className="uppercase tracking-wide">{s}</span>
      {label && <span className="font-normal text-neutral-500">· {label}</span>}
    </span>
  );
}
