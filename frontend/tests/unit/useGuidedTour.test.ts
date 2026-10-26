/**
 * Tests for useGuidedTour hook.
 *
 * Covers:
 *   - Auto-starts on first visit (no localStorage flag)
 *   - Does NOT auto-start when flag present
 *   - next() advances step, completes + persists on final step
 *   - prev() decrements, clamped at 0
 *   - dismiss() closes + persists flag
 *   - reset() clears flag and re-opens at step 0
 */
import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useGuidedTour, STORAGE_KEY } from "../../app/lib/useGuidedTour";

function makeStorage(initial: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initial));
  return {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, v);
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    _store: store,
  };
}

describe("useGuidedTour", () => {
  beforeEach(() => {
    // Each test passes its own storage override, so no global state to reset.
  });

  it("auto-starts on first visit when flag is absent", () => {
    const storage = makeStorage();
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 3, storage }),
    );
    expect(result.current.active).toBe(true);
    expect(result.current.step).toBe(0);
  });

  it("does not auto-start when completion flag is set", () => {
    const storage = makeStorage({ [STORAGE_KEY]: "1" });
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 3, storage }),
    );
    expect(result.current.active).toBe(false);
  });

  it("does not auto-start when autoStart=false", () => {
    const storage = makeStorage();
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 3, storage, autoStart: false }),
    );
    expect(result.current.active).toBe(false);
  });

  it("next() advances step, prev() decrements (clamped at 0)", () => {
    const storage = makeStorage();
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 3, storage }),
    );
    act(() => result.current.next());
    expect(result.current.step).toBe(1);
    act(() => result.current.next());
    expect(result.current.step).toBe(2);
    act(() => result.current.prev());
    expect(result.current.step).toBe(1);
    act(() => result.current.prev());
    act(() => result.current.prev());
    expect(result.current.step).toBe(0);
  });

  it("next() on the final step closes tour and persists flag", () => {
    const storage = makeStorage();
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 2, storage }),
    );
    act(() => result.current.next()); // 0 -> 1
    expect(result.current.active).toBe(true);
    act(() => result.current.next()); // 1 -> completes
    expect(result.current.active).toBe(false);
    expect(storage.getItem(STORAGE_KEY)).toBe("1");
  });

  it("dismiss() closes and persists completion flag", () => {
    const storage = makeStorage();
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 4, storage }),
    );
    act(() => result.current.dismiss());
    expect(result.current.active).toBe(false);
    expect(storage.getItem(STORAGE_KEY)).toBe("1");
  });

  it("reset() clears flag and reopens at step 0", () => {
    const storage = makeStorage({ [STORAGE_KEY]: "1" });
    const { result } = renderHook(() =>
      useGuidedTour({ totalSteps: 3, storage }),
    );
    expect(result.current.active).toBe(false);
    act(() => result.current.reset());
    expect(result.current.active).toBe(true);
    expect(result.current.step).toBe(0);
    expect(storage.getItem(STORAGE_KEY)).toBeNull();
  });
});
