import { useState } from "react";
import { useAskQuestionV1CheckpointsCheckpointIdQuestionsPost } from "../lib/orval/questions/questions";
import { getListCheckpointQuestionsV1CheckpointsCheckpointIdQuestionsGetQueryKey } from "../lib/orval/checkpoints/checkpoints";
import { useQueryClient } from "@tanstack/react-query";

/**
 * AskPanel — form to submit a new question against a frozen checkpoint.
 *
 * Backend accepts `{user_prompt, model_id?, parent_question_id?}` and returns
 * 202 with `{id, stream_url, status, asked_at}`. Streaming subscription is
 * left to Task 12; here we optimistically invalidate the questions list so
 * the new row appears immediately after POST succeeds.
 */
export function AskPanel({
  checkpointId,
  onSubmitted,
}: {
  checkpointId: string;
  onSubmitted?: (questionId: string) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [modelId, setModelId] = useState("");
  const qc = useQueryClient();

  const mutation = useAskQuestionV1CheckpointsCheckpointIdQuestionsPost({
    mutation: {
      onSuccess: (res: { data?: unknown } | undefined) => {
        qc.invalidateQueries({
          queryKey: getListCheckpointQuestionsV1CheckpointsCheckpointIdQuestionsGetQueryKey(
            checkpointId,
          ),
        });
        const body = (res?.data ?? {}) as { id?: string };
        if (body.id && onSubmitted) onSubmitted(body.id);
        setPrompt("");
      },
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed) return;
    mutation.mutate({
      checkpointId,
      data: {
        user_prompt: trimmed,
        model_id: modelId.trim() || undefined,
      },
    });
  };

  const busy = mutation.isPending;
  const err = mutation.error as unknown as { message?: string } | null;

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-900"
      data-testid="ask-panel"
      aria-label="Ask a question about this checkpoint"
    >
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-neutral-700 dark:text-neutral-300">
          Ask about this frozen context
        </span>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          maxLength={8000}
          placeholder="Why did you open 3 SPY puts at this checkpoint?"
          className="rounded border border-neutral-300 bg-white p-2 text-sm dark:border-neutral-700 dark:bg-neutral-950"
          disabled={busy}
          data-testid="ask-panel-prompt"
        />
      </label>

      <label className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
        <span>Model (optional)</span>
        <input
          type="text"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          placeholder="e.g. deepseek-chat"
          className="rounded border border-neutral-300 bg-white px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-950"
          disabled={busy}
        />
      </label>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !prompt.trim()}
          className="rounded bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:cursor-not-allowed disabled:bg-neutral-300 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200 dark:disabled:bg-neutral-700"
          data-testid="ask-panel-submit"
        >
          {busy ? "Asking…" : "Ask"}
        </button>
        <span className="text-xs text-neutral-500">
          {prompt.length}/8000
        </span>
        {err && (
          <span className="text-xs text-red-600 dark:text-red-400">
            {err.message ?? "Failed to post question."}
          </span>
        )}
      </div>
    </form>
  );
}
