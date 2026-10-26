import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { AskPanel } from "../../components/AskPanel";
import { requireAuth } from "~/lib/auth-guard";

/**
 * /checkpoints/:ckptId/ask — drawer-style route for posting a new question.
 *
 * Task 8 renders this as a modal sheet anchored to the right. The underlying
 * `/checkpoints/:ckptId` page stays mounted so its frozen context and QAThread
 * remain visible while the drawer is open. Dismissing the drawer navigates
 * back to the parent route.
 *
 * Streaming UI (subscribing to `/v1/questions/:id/stream`) is deferred to
 * Task 12 — for now the drawer just POSTs and closes on success.
 */
export const Route = createFileRoute("/checkpoints/$ckptId/ask")({
  loader: async (ctx) => { await requireAuth(ctx); },
  component: AskDrawerRoute,
});

function AskDrawerRoute() {
  const { ckptId } = Route.useParams();
  const navigate = useNavigate();

  const close = () =>
    navigate({ to: "/checkpoints/$ckptId", params: { ckptId } });

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/40"
      role="dialog"
      aria-modal="true"
      data-testid="ask-drawer"
      onClick={close}
    >
      <div
        className="flex h-full w-full max-w-lg flex-col gap-4 overflow-y-auto border-l border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-800 dark:bg-neutral-950"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Ask a question</h2>
          <button
            type="button"
            onClick={close}
            className="rounded px-2 py-1 text-sm text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800"
            data-testid="ask-drawer-close"
          >
            Close
          </button>
        </header>
        <p className="text-sm text-neutral-500">
          Posts against checkpoint{" "}
          <span className="font-mono">{ckptId}</span>. Answer streams will
          appear in the Q&amp;A thread on the underlying page.
        </p>
        <AskPanel checkpointId={ckptId} onSubmitted={close} />
      </div>
    </div>
  );
}
