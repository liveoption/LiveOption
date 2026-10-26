import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { promptCalls, recordedParts, templateParts } from "../../app/lib/prompts";
import { PromptTokens, ReadablePrompt } from "../../app/components/PromptText";

describe("prompt provenance", () => {
  it("recognizes only supported placeholder syntax, including formatting", () => {
    const text = '{{json}} {cash:,.2f} {universe}\n{"action": "buy"}';
    const parts = templateParts(text);
    expect(parts.filter(p => p.variable).map(p => p.variable)).toEqual(["cash", "universe"]);
    expect(parts.map(p => p.text).join("")).toBe(text);
    expect(templateParts("{cash}", new Map([["cash", "1,000"]]))[1]).toMatchObject({ variable: "cash", text: "1,000" });
  });
  it("maps Python Unicode offsets correctly without marking guessed legacy values", () => {
    const text = "💹 SPY: 1,000";
    const parts = recordedParts(text, [{ start: 2, end: 5, variable: "universe" }])!;
    expect(parts.find(p => p.variable)?.text).toBe("SPY");
    expect(parts.map(p => p.text).join("")).toBe(text);
    expect(promptCalls({ system: "Trade SPY", user: "SPY" })[0].user?.provenance).toBe("unavailable");
    expect(recordedParts(text, [{ start: -1, end: 3, variable: "bad" }])).toBeNull();
    expect(recordedParts(text, [{ start: 0, end: 9999, variable: "bad" }])).toBeNull();
    expect(recordedParts(text, [{ start: 0, end: 5, variable: "a" }, { start: 3, end: 6, variable: "b" }])).toBeNull();
  });
  it("normalizes multi-phase calls and template System/User pairs", () => {
    const calls = promptCalls({ calls: [{ phase: "analyzer", system: "S", user: "U" }, { phase: "executor", user: "E" }] });
    expect(calls.map(c => c.phase)).toEqual(["analyzer", "executor"]);
    const template = promptCalls({ prompt_templates: { analyzer_system: "S {n}", analyzer_user: "U", executor_user: "E" } }, true);
    expect(template[0].system?.parts.find(p => p.variable)?.variable).toBe("n");
    expect(template[1].user?.text).toBe("E");
  });
  it("renders headings and tables safely, with separately marked filled data", () => {
    const { container } = render(<ReadablePrompt parts={[{ text: "## Account\nCash " }, { text: "1,000", variable: "cash" }, { text: "\n\n| A | B |\n|---|---|\n| X | 2 |\n<script>alert(1)</script>" }]} />);
    expect(container.querySelector("h4")?.textContent).toBe("Account");
    expect(container.querySelector("mark[data-variable=cash]")?.textContent).toBe("1,000");
    expect(container.querySelectorAll("table tr")).toHaveLength(2);
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>alert(1)</script>");
    const highlighted = render(<PromptTokens parts={templateParts("Use {universe}")} />);
    expect(highlighted.container.querySelector("mark")?.textContent).toBe("{universe}");
  });
});
