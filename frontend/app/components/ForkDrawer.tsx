import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useCreateForkV1CheckpointsCheckpointIdForksPost } from "../lib/orval/forks/forks";
import { getListRunsV1RunsGetQueryKey } from "../lib/orval/runs/runs";
import { HttpError } from "../lib/http-client";

/**
 * ForkDrawer — diverge-only fork form (Plan C Task 9).
 *
 * Phase 1 per Plan B supervisor: `mode` is pinned to `"diverge"` in the
 * request body (matches the backend `ForkCreate` `Literal["diverge"]`).
 * Replay mode is a one-line widening deferred to Phase 2.
 *
 * Validation rule: at least one of `prompt_override` / `model_override` must
 * be non-empty — a fork with no overrides reruns the exact same prompt, which
 * the backend rejects (and is not a useful experiment).
 *
 * On success the new run appears in the `/runs` list; we invalidate that
 * query and hand control to the caller (typically the route, which navigates
 * to the live page for the new run).
 *
 * Error handling:
 *   - 409 (concurrency cap exceeded) and 400 (validation / dead prompt) are
 *     surfaced via the RFC 7807 `problem+json` body attached to `HttpError`.
 *   - Any other error falls back to the generic Error `message`.
 */
export interface ForkDrawerProps {
  checkpointId: string;
  onCreated?: (runId: string) => void;
  onCancel?: () => void;
}

type ForkRunRef = { run_id?: string; id?: string };

/**
 * The Orval-generated types describe the response as `{ data, status }`, but
 * our `httpClient` mutator returns the parsed JSON body directly (see
 * `app/lib/http-client.ts`). We handle both shapes defensively so future
 * mutator changes don't silently break the navigation.
 */
function extractRunId(res: unknown): string | undefined {
  if (!res || typeof res !== "object") return undefined;
  const obj = res as ForkRunRef & { data?: ForkRunRef };
  return obj.run_id ?? obj.id ?? obj.data?.run_id ?? obj.data?.id;
}

export function ForkDrawer({ checkpointId, onCreated, onCancel }: ForkDrawerProps) {
  const [promptOverride, setPromptOverride] = useState("");
  const [modelOverride, setModelOverride] = useState("");
  const [title, setTitle] = useState("");
  const qc = useQueryClient();

  const mutation = useCreateForkV1CheckpointsCheckpointIdForksPost({
    mutation: {
      onSuccess: (res) => {
        qc.invalidateQueries({ queryKey: getListRunsV1RunsGetQueryKey() });
        const newRunId = extractRunId(res);
        if (newRunId && onCreated) onCreated(newRunId);
      },
    },
  });

  const trimmedPrompt = promptOverride.trim();
  const trimmedModel = modelOverride.trim();
  const hasOverride = trimmedPrompt.length > 0 || trimmedModel.length > 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!hasOverride) return;
    mutation.mutate({
      checkpointId,
      data: {
        mode: "diverge",
        prompt_override: trimmedPrompt || null,
        model_override: trimmedModel || null,
        title: title.trim() || null,
      },
    });
  };

  const busy = mutation.isPending;
  const errMsg = formatError(mutation.error);

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-900"
      data-testid="fork-drawer-form"
      aria-label="Fork checkpoint (diverge)"
    >
      <div
        className="rounded border border-indigo-200 bg-indigo-50 p-2 text-xs text-indigo-900 dark:border-indigo-900/50 dark:bg-indigo-950/40 dark:text-indigo-200"
        data-testid="fork-mode-note"
      >
        <strong>Fork (diverge)</strong> re-runs this day from the checkpoint, calling the LLM
        fresh with your overrides. For bit-exact replay see the <code>verify-replays</code> CLI.
      </div>

      <input type="hidden" name="mode" value="diverge" data-testid="fork-mode" />

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-neutral-700 dark:text-neutral-300">
          Prompt override
        </span>
        <textarea
          value={promptOverride}
          onChange={(e) => setPromptOverride(e.target.value)}
          rows={6}
          maxLength={16000}
          placeholder="Leave blank to keep the original prompt. Enter alternate instructions to steer the LLM."
          className="rounded border border-neutral-300 bg-white p-2 text-sm font-mono dark:border-neutral-700 dark:bg-neutral-950"
          disabled={busy}
          data-testid="fork-prompt-override"
        />
        <span className="text-xs text-neutral-500">{promptOverride.length}/16000</span>
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-neutral-700 dark:text-neutral-300">
          Model override (optional)
        </span>
        <input
          type="text"
          value={modelOverride}
          onChange={(e) => setModelOverride(e.target.value)}
          placeholder="e.g. deepseek-chat, gemini-2.0-flash"
          className="rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          disabled={busy}
          data-testid="fork-model-override"
        />
      </label>

      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-neutral-700 dark:text-neutral-300">
          Title (optional)
        </span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Short label to find this fork later"
          className="rounded border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          disabled={busy}
          data-testid="fork-title"
        />
      </label>

      {!hasOverride && (
        <p
          className="text-xs text-amber-700 dark:text-amber-400"
          data-testid="fork-validation-msg"
        >
          Enter at least one override (prompt or model) — a fork with no overrides would just
          rerun the original prompt.
        </p>
      )}

      {errMsg && (
        <div
          className="rounded border border-red-300 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
          role="alert"
          data-testid="fork-error"
        >
          {errMsg}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !hasOverride}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:bg-neutral-300 dark:disabled:bg-neutral-700"
          data-testid="fork-submit"
        >
          {busy ? "Creating fork…" : "Create fork"}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
            data-testid="fork-cancel"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

/**
 * Surface RFC 7807 problem+json detail when available, with explicit hints
 * for the two known error classes (409 concurrency cap, 400 validation /
 * dead prompt_override). Falls back to the generic message otherwise.
 */
function formatError(err: unknown): string | null {
  if (!err) return null;
  if (err instanceof HttpError) {
    const detail = err.problem?.detail ?? err.problem?.title;
    if (err.status === 409) {
      return detail
        ? `Concurrency cap exceeded — ${detail}`
        : "Concurrency cap exceeded — too many runs in flight. Try again in a moment.";
    }
    if (err.status === 400) {
      return detail ?? "Invalid fork request. Check the prompt/model override.";
    }
    return detail ?? err.message;
  }
  if (err instanceof Error) return err.message;
  return "Failed to create fork.";
}
