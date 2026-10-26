import { describe, expect, it } from "vitest";
import { parseResponse, responseCalls, responseSummary } from "../../app/lib/responses";

describe("model response parsing", () => {
  it.each([
    ['{"intent":"bearish","confidence":0.85}', { intent: "bearish", confidence: 0.85 }],
    ['```json\n{"reasoning":"保留原文 💹"}\n```', { reasoning: "保留原文 💹" }],
    ['```\r\n[ {"action":"BUY","qty":2} ]\r\n```', [{ action: "BUY", qty: 2 }]],
    ["[]", []], ["null", null], ["false", false], ["0", 0], ['"plain string"', "plain string"],
  ])("parses complete JSON without changing values: %s", (raw, content) => {
    expect(parseResponse(raw)).toEqual({ format: "json", content });
  });
  it.each([
    '```json\n{"bad":}\n```', 'Commentary\n```json\n{"a":1}\n```',
    '```json\n{}\n```\n```json\n[]\n```', '<script>alert(1)</script>', '', '{"a":1} trailing',
  ])("keeps invalid or mixed output verbatim: %s", raw => {
    expect(parseResponse(raw)).toEqual({ format: "text", content: raw });
  });
  it("keeps multi-stage raw text, saved fields and the complete artifact intact", () => {
    const response = { calls: [{ phase: "analyzer", raw: '```json\n{"intent":"bearish"}\n```', tokens: 12 }, { phase: "executor", raw: "[]" }], intents_count: 0 };
    const original = JSON.stringify(response);
    const calls = responseCalls(response);
    expect(calls[0]).toMatchObject({ phase: "analyzer", format: "json", content: { intent: "bearish" }, saved: { tokens: 12 } });
    expect(calls[0].raw).toBe(response.calls[0].raw);
    expect(calls[1].content).toEqual([]);
    expect(responseSummary(response)).toEqual({ intents_count: 0 });
    expect(JSON.stringify(response)).toBe(original);
  });
  it("supports structured-only, missing, plain and empty legacy shapes", () => {
    expect(responseCalls({ analysis: { reasoning: "No liquidity." } })[0]).toMatchObject({ raw: null, format: "structured", content: { analysis: { reasoning: "No liquidity." } } });
    expect(responseCalls({ raw: "[]", actions: [] })[0]).toMatchObject({ content: [], saved: { actions: [] } });
    expect(responseCalls({ calls: [{ phase: "executor", raw: null }] })[0].format).toBe("missing");
    expect(responseCalls("HOLD")[0]).toMatchObject({ content: "HOLD", format: "text", raw: "HOLD" });
    expect(responseCalls(null)[0].format).toBe("missing");
    expect(responseCalls([])[0]).toMatchObject({ content: [], format: "structured" });
    expect(responseCalls({ calls: [], intents_count: 0 })).toHaveLength(1);
    expect(responseSummary({ raw: "[]" })).toBeNull();
  });
});
