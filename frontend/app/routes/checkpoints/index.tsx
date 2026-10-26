import { createFileRoute, Link } from "@tanstack/react-router";
import { requireAuth } from "~/lib/auth-guard";
import { useLocale } from "~/lib/locale";

/**
 * /checkpoints — index page.
 *
 * The backend exposes only run-scoped checkpoint listing
 * (`GET /v1/runs/:run_id/checkpoints`) and no global
 * `GET /v1/checkpoints` endpoint, so this page directs users
 * to browse runs and inspect checkpoints from within a run's
 * decision timeline.
 */
export const Route = createFileRoute("/checkpoints/")({
  loader: async (ctx) => { await requireAuth(ctx); },
  component: CheckpointsIndexPage,
});

function CheckpointsIndexPage() {
  const { ja } = useLocale();
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="lo-display text-2xl text-[color:var(--color-ink)]">
          {ja("Checkpoints")}
        </h1>
        <p className="text-sm text-[color:var(--color-ink-soft)]">
          {ja("Frozen portfolio snapshots promoted from run decisions. Fork from any checkpoint to explore an alternate path.")}
        </p>
      </header>

      <div
        data-testid="checkpoints-empty"
        className="rounded-xl border border-dashed border-[var(--color-rule)] bg-[var(--color-paper)] p-8 text-center"
      >
        <p className="text-sm text-[color:var(--color-ink-soft)]">
          {ja("No checkpoints yet. Promoted checkpoints appear inside individual runs — open a run from the runs list and inspect its decision timeline.")}
        </p>
        <Link
          to="/runs"
          className="mt-4 inline-flex rounded-lg bg-[var(--color-ink)] px-4 py-2 text-sm font-medium text-[var(--color-paper)]"
        >
          {ja("Browse runs")}
        </Link>
      </div>
    </div>
  );
}
