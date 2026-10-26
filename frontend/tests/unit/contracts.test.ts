/**
 * Tests for `app/lib/contracts.ts` — the zod envelope parser used by pages
 * to validate Orval `additionalProperties: true` responses at the boundary.
 *
 * Coverage:
 *   - happy paths on each envelope (runs list, timeline, pnl, questions,
 *     children) + single-row schemas (run, checkpoint)
 *   - mismatches throw `ContractError` with a useful message + path
 *   - permissive fields: unknown keys pass through; nullable optional
 *     fields accept null/undefined
 */
import { describe, expect, it } from "vitest";
import {
  ContractError,
  parseContract,
  zChildrenPage,
  zCheckpointRow,
  zDecisionRecordRow,
  zPnLSeries,
  zQuestionRow,
  zQuestionsPage,
  zRunRow,
  zRunsPage,
  zTimelinePage,
} from "~/lib/contracts";

describe("zRunsPage", () => {
  it("parses a typical runs list envelope", () => {
    const raw = {
      items: [
        {
          id: "01HXYZ",
          scenario: "baseline",
          status: "finished",
          kind: "batch",
          model_id: "gpt-5",
          provider: "openai",
          date_start: "2025-01-02",
          date_end: "2025-01-10",
          created_at: "2025-01-02T14:30:00Z",
          is_featured: true,
          title: "Baseline run",
        },
      ],
      next_cursor: null,
    };
    const parsed = parseContract(zRunsPage, raw);
    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0].id).toBe("01HXYZ");
    expect(parsed.next_cursor).toBeNull();
  });

  it("allows next_cursor to be a string", () => {
    const parsed = parseContract(zRunsPage, {
      items: [{ id: "a" }],
      next_cursor: "cursor-xyz",
    });
    expect(parsed.next_cursor).toBe("cursor-xyz");
  });

  it("passes through unknown keys on rows", () => {
    const parsed = parseContract(zRunsPage, {
      items: [{ id: "a", future_field: 42 }],
      next_cursor: null,
    });
    expect(
      (parsed.items[0] as unknown as { future_field: number }).future_field,
    ).toBe(42);
  });

  it("throws ContractError when items is missing", () => {
    expect(() => parseContract(zRunsPage, { next_cursor: null })).toThrow(
      ContractError,
    );
  });

  it("throws when a row is missing id", () => {
    try {
      parseContract(zRunsPage, { items: [{ scenario: "x" }], next_cursor: null });
      throw new Error("expected throw");
    } catch (e) {
      expect(e).toBeInstanceOf(ContractError);
      const err = e as ContractError;
      expect(err.message).toMatch(/items\.0\.id/);
      expect(err.issues.length).toBeGreaterThan(0);
    }
  });

  it("includes a snippet of received payload in the error message", () => {
    try {
      parseContract(zRunsPage, { wrong: "shape" });
      throw new Error("expected throw");
    } catch (e) {
      expect(e).toBeInstanceOf(ContractError);
      const err = e as ContractError;
      expect(err.message).toMatch(/received .*wrong.*shape/);
    }
  });
});

describe("zTimelinePage", () => {
  it("parses a timeline envelope", () => {
    const parsed = parseContract(zTimelinePage, {
      items: [
        { id: "d1", seq: 1, trading_day: "2025-01-02", has_trade: true },
        { id: "d2", seq: 2 },
      ],
    });
    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[0].seq).toBe(1);
  });

  it("rejects non-array items", () => {
    expect(() => parseContract(zTimelinePage, { items: "nope" })).toThrow(
      ContractError,
    );
  });
});

describe("zPnLSeries", () => {
  it("parses a pnl series envelope", () => {
    const parsed = parseContract(zPnLSeries, {
      items: [
        { trading_day: "2025-01-02", cum_pnl: 100.5, portfolio_value: 10100.5 },
        { trading_day: "2025-01-03", cum_pnl: null, portfolio_value: null },
      ],
    });
    expect(parsed.items).toHaveLength(2);
  });
});

describe("zQuestionsPage + zQuestionRow", () => {
  it("parses a questions envelope", () => {
    const parsed = parseContract(zQuestionsPage, {
      items: [
        {
          id: "q1",
          checkpoint_id: "ck1",
          asked_by: "user@example.com",
          asked_at: "2025-01-02T15:00:00Z",
          user_prompt: "why?",
          status: "completed",
        },
      ],
    });
    expect(parsed.items[0].id).toBe("q1");
  });

  it("accepts null for optional fields", () => {
    const parsed = parseContract(zQuestionRow, {
      id: "q1",
      checkpoint_id: "ck1",
      parent_question_id: null,
      response_text: null,
      latency_ms: null,
      cost_usd: null,
    });
    expect(parsed.id).toBe("q1");
  });
});

describe("zRunRow + zCheckpointRow + zDecisionRecordRow", () => {
  it("parses a minimal run row (only id required)", () => {
    expect(parseContract(zRunRow, { id: "r1" }).id).toBe("r1");
  });

  it("parses a checkpoint row", () => {
    const parsed = parseContract(zCheckpointRow, {
      id: "ck1",
      run_id: "r1",
      portfolio_summary: { foo: "bar" },
      fingerprint: "abc",
    });
    expect(parsed.run_id).toBe("r1");
  });

  it("parses a decision record row", () => {
    const parsed = parseContract(zDecisionRecordRow, {
      id: "d1",
      seq: 5,
      has_trade: false,
    });
    expect(parsed.seq).toBe(5);
  });

  it("rejects decision row with non-numeric seq", () => {
    expect(() =>
      parseContract(zDecisionRecordRow, { id: "d1", seq: "five" }),
    ).toThrow(ContractError);
  });
});

describe("zChildrenPage", () => {
  it("parses children list as an items envelope", () => {
    const parsed = parseContract(zChildrenPage, {
      items: [{ id: "child1" }, { id: "child2" }],
    });
    expect(parsed.items.map((r) => r.id)).toEqual(["child1", "child2"]);
  });
});
