import { useEffect, useRef, useState } from "react";

import { usePatchRunV1RunsRunIdPatch } from "../lib/orval/runs/runs";

/**
 * `RunTitle` — heading + inline-rename affordance for `/runs/:id` headers.
 *
 * Click the title → swaps to an `<input>` editor. Press Enter (or blur)
 * commits via `PATCH /v1/runs/:id` with `{title}`. Press Escape cancels.
 * After a successful PATCH the parent's React Query cache for the run
 * row is refetched via the supplied `onSaved` callback (typically
 * `runQuery.refetch`), so the new title flows back into the page header
 * without an explicit local state mirror.
 *
 * The component is intentionally additive: it owns the heading element
 * directly so the parent can drop it in place of the original `<h1>`
 * without rewiring header layout. The rendered fallback chain matches
 * the previous inline expression (`title || scenario || id`).
 */
export interface RunTitleProps {
  runId: string;
  title?: string | null;
  scenario?: string | null;
  onSaved?: () => void;
}

export function RunTitle({ runId, title, scenario, onSaved }: RunTitleProps) {
  const display = title?.trim() || scenario || runId;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title ?? "");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const patch = usePatchRunV1RunsRunIdPatch();

  useEffect(() => {
    if (editing) {
      setDraft(title ?? "");
      // focus next tick so the input is mounted
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [editing, title]);

  const commit = () => {
    const next = draft.trim();
    // Empty string explicitly clears the title (server treats null/empty
    // as "no title" — display falls back to scenario/id).
    if (next === (title ?? "").trim()) {
      setEditing(false);
      return;
    }
    patch.mutate(
      { runId, data: { title: next || null } },
      {
        onSuccess: () => {
          setEditing(false);
          if (onSaved) onSaved();
        },
        onError: () => {
          // Keep the editor open so the user can retry; the error toast
          // is the parent's concern (we don't want every header sprouting
          // its own alert UI).
          setEditing(true);
        },
      },
    );
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        data-testid="run-title-input"
        type="text"
        value={draft}
        maxLength={200}
        disabled={patch.isPending}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            setEditing(false);
          }
        }}
        onBlur={commit}
        className="w-full max-w-[24rem] rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-2 py-1 text-2xl font-semibold text-[color:var(--color-ink)]"
      />
    );
  }

  return (
    <h1
      data-testid="run-title"
      title="Click to rename"
      onClick={() => setEditing(true)}
      className="cursor-pointer text-2xl font-semibold hover:underline decoration-dotted underline-offset-4"
    >
      {display}
    </h1>
  );
}

export default RunTitle;
