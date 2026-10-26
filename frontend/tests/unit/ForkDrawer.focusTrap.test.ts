/**
 * Tests for the Tab focus-trap helpers exported by
 * `app/routes/checkpoints/$ckptId.fork.tsx`.
 *
 * The helpers are extracted from the route's keydown handler so we can test
 * them in isolation — mounting the route component itself would require a
 * full TanStack router context, which this task does not need.
 *
 * Coverage:
 *   - `collectFocusable` skips disabled / aria-hidden / tabindex=-1 elements.
 *   - `applyTabFocusTrap` wraps from last -> first on Tab at the end.
 *   - `applyTabFocusTrap` wraps from first -> last on Shift+Tab at the start.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  applyTabFocusTrap,
  collectFocusable,
} from "../../app/routes/checkpoints/$ckptId.fork";

function makeDialog(): HTMLDivElement {
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  dialog.innerHTML = `
    <button data-testid="first">First</button>
    <input data-testid="middle" type="text" />
    <button data-testid="disabled" disabled>Disabled</button>
    <button data-testid="hidden" aria-hidden="true" tabindex="-1">Hidden</button>
    <button data-testid="last">Last</button>
  `;
  document.body.appendChild(dialog);
  return dialog;
}

describe("ForkDrawer focus trap helpers", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("collectFocusable filters out disabled / aria-hidden / tabindex=-1", () => {
    const dialog = makeDialog();
    const focusables = collectFocusable(dialog);
    const ids = focusables.map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual(["first", "middle", "last"]);
  });

  it("Tab on the last element wraps focus to the first", () => {
    const dialog = makeDialog();
    const first = dialog.querySelector<HTMLElement>('[data-testid="first"]')!;
    const last = dialog.querySelector<HTMLElement>('[data-testid="last"]')!;
    last.focus();
    expect(document.activeElement).toBe(last);

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      shiftKey: false,
      bubbles: true,
      cancelable: true,
    });
    const trapped = applyTabFocusTrap(dialog, event);
    expect(trapped).toBe(true);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);
  });

  it("Shift+Tab on the first element wraps focus to the last", () => {
    const dialog = makeDialog();
    const first = dialog.querySelector<HTMLElement>('[data-testid="first"]')!;
    const last = dialog.querySelector<HTMLElement>('[data-testid="last"]')!;
    first.focus();
    expect(document.activeElement).toBe(first);

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    });
    const trapped = applyTabFocusTrap(dialog, event);
    expect(trapped).toBe(true);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(last);
  });

  it("Tab in the middle does not trap (native focus move proceeds)", () => {
    const dialog = makeDialog();
    const middle = dialog.querySelector<HTMLElement>('[data-testid="middle"]')!;
    middle.focus();

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      shiftKey: false,
      bubbles: true,
      cancelable: true,
    });
    const trapped = applyTabFocusTrap(dialog, event);
    expect(trapped).toBe(false);
    expect(event.defaultPrevented).toBe(false);
  });
});
