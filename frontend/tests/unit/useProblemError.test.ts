/**
 * Tests for `app/lib/useProblemError.ts`.
 *
 * The hook is a pure extractor — no React state is read — so we can invoke
 * it as a function in the test environment without mounting a component.
 * That keeps the test fast and side-effect-free.
 */
import { describe, expect, it } from "vitest";

import { HttpError } from "../../app/lib/http-client";
import { useProblemError } from "../../app/lib/useProblemError";

describe("useProblemError", () => {
  it("returns null for a plain Error", () => {
    expect(useProblemError(new Error("boom"))).toBeNull();
  });

  it("returns null for HttpError without a problem body", () => {
    const err = new HttpError(502, "upstream exploded");
    expect(useProblemError(err)).toBeNull();
  });

  it("returns the problem object for HttpError with problem+json", () => {
    const problem = {
      type: "about:blank",
      title: "Run not found",
      status: 404,
      detail: "no such run_id",
    };
    const err = new HttpError(404, JSON.stringify(problem), problem);
    const out = useProblemError(err);
    expect(out).not.toBeNull();
    expect(out).toMatchObject({
      title: "Run not found",
      status: 404,
      detail: "no such run_id",
    });
  });

  it("returns null for null / undefined / non-Error values", () => {
    expect(useProblemError(null)).toBeNull();
    expect(useProblemError(undefined)).toBeNull();
    expect(useProblemError("nope")).toBeNull();
    expect(useProblemError({ title: "not an HttpError" })).toBeNull();
  });
});
