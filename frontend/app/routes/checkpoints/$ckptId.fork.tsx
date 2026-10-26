import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { ForkDrawer } from "../../components/ForkDrawer";
import { requireAuth } from "~/lib/auth-guard";

/**
 * /checkpoints/:ckptId/fork — drawer-style route for creating a divergent
 * fork of the frozen checkpoint (Plan C Task 9).
 *
 * Layout mirrors the sibling `/ask` drawer (Task 8): the underlying
 * `/checkpoints/:ckptId` page stays mounted so the frozen context remains
 * visible while the drawer is open.
 *
 * Phase 1 supports "diverge" mode only. On success we navigate to the new
 * run's detail page `/runs/:run_id` (which auto-tails SSE while the run is
 * pending/running). We perform the jump via `window.location.assign` so the
 * run-detail route mounts cleanly on a fresh history entry.
 */
export const Route = createFileRoute("/checkpoints/$ckptId/fork")({
  loader: async (ctx) => { await requireAuth(ctx); },
  component: ForkDrawerRoute,
});

/** Selector matching the focusable elements we want to cycle between. */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/** Returns the currently focusable elements inside `root`. */
export function collectFocusable(root: HTMLElement): HTMLElement[] {
  const all = Array.from(
    root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  );
  return all.filter(
    (el) =>
      !el.hasAttribute("disabled") &&
      !el.hasAttribute("hidden") &&
      el.getAttribute("aria-hidden") !== "true" &&
      el.tabIndex !== -1,
  );
}

/**
 * Apply the wrap-around Tab focus-trap rule to `dialog` for a single Tab
 * key event. Exported so tests can drive the trap without mounting the
 * full route component (which requires a router context).
 *
 * Returns `true` if the event was trapped (and preventDefault was called).
 */
export function applyTabFocusTrap(
  dialog: HTMLElement,
  e: KeyboardEvent,
): boolean {
  if (e.key !== "Tab") return false;
  const focusables = collectFocusable(dialog);
  if (focusables.length === 0) {
    e.preventDefault();
    return true;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (e.shiftKey) {
    if (active === first || !dialog.contains(active)) {
      e.preventDefault();
      last.focus();
      return true;
    }
  } else {
    if (active === last || !dialog.contains(active)) {
      e.preventDefault();
      first.focus();
      return true;
    }
  }
  return false;
}

function ForkDrawerRoute() {
  const { ckptId } = Route.useParams();
  const navigate = useNavigate();
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // Focus management: remember the trigger, focus the close button on mount,
  // restore focus on unmount. Also handle Esc to close + Tab/Shift+Tab to
  // trap focus within the drawer dialog per WAI-ARIA dialog pattern.
  useEffect(() => {
    if (typeof document !== "undefined") {
      previousFocusRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }
    closeBtnRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        navigate({ to: "/checkpoints/$ckptId", params: { ckptId } });
        return;
      }
      if (!dialogRef.current) return;
      applyTabFocusTrap(dialogRef.current, e);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previousFocusRef.current?.focus?.();
    };
  }, [ckptId, navigate]);

  const close = () =>
    navigate({ to: "/checkpoints/$ckptId", params: { ckptId } });

  const goLive = (runId: string) => {
    if (typeof window !== "undefined") {
      window.location.assign(`/runs/${runId}`);
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="fork-drawer-title"
      data-testid="fork-drawer"
      onClick={close}
    >
      <div
        ref={dialogRef}
        className="flex h-full w-full max-w-xl flex-col gap-4 overflow-y-auto border-l border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-800 dark:bg-neutral-950"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between">
          <h2 id="fork-drawer-title" className="text-lg font-semibold">
            Fork this checkpoint
          </h2>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={close}
            aria-label="Close fork drawer"
            className="rounded px-2 py-1 text-sm text-neutral-500 hover:bg-neutral-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 dark:hover:bg-neutral-800"
            data-testid="fork-drawer-close"
          >
            Close
          </button>
        </header>
        <p className="text-sm text-neutral-500">
          Creates a new run that resumes from checkpoint{" "}
          <span className="font-mono">{ckptId}</span> with your overrides. Mode is pinned to{" "}
          <span className="font-mono">diverge</span> in Phase 1.
        </p>
        <ForkDrawer checkpointId={ckptId} onCreated={goLive} onCancel={close} />
      </div>
    </div>
  );
}
