import { useCallback, useEffect, useState } from "react";

/**
 * useGuidedTour — client-side hook managing onboarding tour state.
 *
 * Persistence model:
 *   - Completion flag stored in localStorage under `STORAGE_KEY`.
 *   - Once set to "1", the tour is considered finished and `active` stays
 *     false on mount (even after reload).
 *   - `reset()` clears the flag and reopens the tour from step 0 (useful for
 *     a "replay tour" menu item).
 *
 * The hook is SSR-safe: it does not touch `window`/`localStorage` outside
 * `useEffect`, so it is safe to import from components rendered during SSR
 * (though the consumer is expected to mount behind `<ClientOnly/>`).
 */

export const STORAGE_KEY = "liveoption.guidedTour.completed";

export interface GuidedTourState {
  active: boolean;
  step: number;
  totalSteps: number;
  next: () => void;
  prev: () => void;
  dismiss: () => void;
  reset: () => void;
}

export interface UseGuidedTourOptions {
  totalSteps: number;
  /**
   * If true (default), auto-start on first visit (when storage flag absent).
   * Set false to only open via `reset()` (e.g. menu-triggered replays).
   */
  autoStart?: boolean;
  /** Override storage for testing. */
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">;
}

function getStorage(
  override?: Pick<Storage, "getItem" | "setItem" | "removeItem">,
): Pick<Storage, "getItem" | "setItem" | "removeItem"> | null {
  if (override) return override;
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function useGuidedTour(options: UseGuidedTourOptions): GuidedTourState {
  const { totalSteps, autoStart = true, storage } = options;
  const [active, setActive] = useState(false);
  const [step, setStep] = useState(0);

  // On mount: consult storage, possibly auto-start.
  useEffect(() => {
    const s = getStorage(storage);
    if (!s) return;
    const done = s.getItem(STORAGE_KEY);
    if (!done && autoStart) {
      setActive(true);
      setStep(0);
    }
  }, [autoStart, storage]);

  const markComplete = useCallback(() => {
    const s = getStorage(storage);
    if (s) {
      try {
        s.setItem(STORAGE_KEY, "1");
      } catch {
        // Ignore quota/security errors — worst case tour re-prompts.
      }
    }
  }, [storage]);

  const next = useCallback(() => {
    setStep((curr) => {
      if (curr + 1 >= totalSteps) {
        setActive(false);
        markComplete();
        return curr;
      }
      return curr + 1;
    });
  }, [totalSteps, markComplete]);

  const prev = useCallback(() => {
    setStep((curr) => (curr > 0 ? curr - 1 : curr));
  }, []);

  const dismiss = useCallback(() => {
    setActive(false);
    markComplete();
  }, [markComplete]);

  const reset = useCallback(() => {
    const s = getStorage(storage);
    if (s) {
      try {
        s.removeItem(STORAGE_KEY);
      } catch {
        // ignore
      }
    }
    setStep(0);
    setActive(true);
  }, [storage]);

  return { active, step, totalSteps, next, prev, dismiss, reset };
}
