import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import AnalysisReport from "../../app/components/run-dashboard/AnalysisReport";
import AgentHistory from "../../app/components/run-dashboard/AgentHistory";
import KlineChart from "../../app/components/run-dashboard/KlineChart";
import type { DecisionRecordRow } from "../../app/lib/contracts";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function decision(seq: number, timestamp = "2025-01-08T14:45:00Z"): DecisionRecordRow {
  return { id: `d${seq}`, run_id: "r1", seq, trading_day: "2025-01-08",
    decision_ts: timestamp, has_trade: seq % 2 === 0, model_id: "model", provider: "test" } as DecisionRecordRow;
}

describe("run results", () => {
  it("shows user-facing percentages without raw nulls or evaluator IDs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      protocol_version: "liveoption.analysis.v1", run: { id: "r1" }, pending: false,
      metrics: { evaluator_version: "v1-phaseC+behavioral:v2-null-unmeasured",
        generic: { total_pnl: -3925.03, max_drawdown: 3708.53 },
        family_specific: { greeks_attribution: { delta_pnl: -1911 } },
        behavioral: { n_decisions: 520, n_with_trade: 68, confidence_calibration: null } },
      paper_portfolio: { annualized_return: -0.396, max_drawdown_pct: 0.039,
        sharpe: -10.981, var_95: 0.01, cvar_95: 0.01, n_days: 20 },
    }) }));
    const { container } = render(<AnalysisReport runId="r1" status="completed" />);
    await screen.findByText("-39.6%");
    expect(container.textContent).toContain("3.9%");
    expect(container.textContent).not.toContain("3708.53");
    expect(container.textContent).not.toContain("null");
    expect(container.textContent).not.toContain("v1-phaseC");
  });

  it("paginates long decision histories", () => {
    const decisions = Array.from({ length: 45 }, (_, seq) => decision(seq));
    const { container } = render(<AgentHistory decisions={decisions} events={[]} total={0} truncated={false} onLoadOlder={null} onSelectDecision={vi.fn()} />);
    expect(container.querySelectorAll("[id^='history-decision-']")).toHaveLength(20);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(container.querySelectorAll("[id^='history-decision-']")).toHaveLength(20);
    expect(container.querySelector("#history-decision-20")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(container.querySelectorAll("[id^='history-decision-']")).toHaveLength(5);
    expect(container.querySelector("#history-decision-44")).toBeTruthy();
  });

  it("filters decisions by model, date and trade intent", () => {
    const decisions = [decision(1), decision(2), { ...decision(3), model_id: "other-model" }];
    const { container } = render(<AgentHistory decisions={decisions} events={[]} total={0} truncated={false} onLoadOlder={null} onSelectDecision={vi.fn()} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "other-model" } });
    expect(container.querySelectorAll("[id^='history-decision-']")).toHaveLength(1);
    expect(container.querySelector("#history-decision-3")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Trade intent filter"), { target: { value: "trade" } });
    expect(container.querySelectorAll("[id^='history-decision-']")).toHaveLength(1);
    expect(container.querySelector("#history-decision-2")).toBeTruthy();
  });

  it("surfaces a saved rationale before the raw artifact", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      artifact_available: true, artifact: { response: { analysis: { reasoning: "The spread is too wide." } }, prompt: { system: "x" } },
    }) }));
    render(<AgentHistory decisions={[decision(1)]} events={[]} total={0} truncated={false} onLoadOlder={null} onSelectDecision={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /#1/ }));
    await screen.findByText("The spread is too wide.");
    expect(screen.getByText("Saved decision rationale")).toBeTruthy();
  });

  it("places a clickable decision checkpoint on the regular-session time axis", async () => {
    const onSelectDecision = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      symbol: "SPY", resolution: "1m", source: "qfinzero.upq", bars: [
        { time: "2025-01-08T13:00:00Z", open: 100, high: 101, low: 99, close: 100, volume: 10 },
        { time: "2025-01-08T14:30:00Z", open: 101, high: 102, low: 100, close: 101, volume: 20 },
        { time: "2025-01-08T14:45:00Z", open: 102, high: 103, low: 101, close: 102, volume: 30 },
      ],
    }) }));
    const { container } = render(<KlineChart runId="r1" universe={["SPY"]} startDay="2025-01-08" family="intraday" decisions={[decision(7)]} onSelectDecision={onSelectDecision} />);
    const marker = await screen.findByRole("button", { name: /Checkpoint #7/ });
    expect(container.querySelectorAll("svg rect")).toHaveLength(4); // two candles and two volume bars
    const candles = container.querySelectorAll("svg rect");
    expect(Number(candles[2].getAttribute("x")) - Number(candles[0].getAttribute("x"))).toBeGreaterThan(500);
    expect(marker.querySelector("circle")?.getAttribute("cy")).toBe("401");
    fireEvent.click(screen.getByLabelText("Show extended hours"));
    expect(container.querySelectorAll("svg rect")).toHaveLength(6); // premarket bar is now visible
    fireEvent.change(screen.getByLabelText("Jump to decision"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /Checkpoint #7/ }));
    await waitFor(() => expect(onSelectDecision).toHaveBeenCalledWith(7));
  });

  it("moves from the fallback date to the first decision day and resets for a new run", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      symbol: "SPY", resolution: "1m", source: "qfinzero.upq", bars: [],
    }) }));
    const onSelectDecision = vi.fn();
    const { rerender } = render(<KlineChart runId="r1" universe={["SPY"]} startDay="2025-01-01" family="intraday" decisions={[]} onSelectDecision={onSelectDecision} />);
    await waitFor(() => expect((screen.getByLabelText("Day") as HTMLInputElement).value).toBe("2025-01-01"));
    rerender(<KlineChart runId="r1" universe={["SPY"]} startDay="2025-01-01" family="intraday" decisions={[decision(7)]} onSelectDecision={onSelectDecision} />);
    await waitFor(() => expect((screen.getByLabelText("Day") as HTMLInputElement).value).toBe("2025-01-08"));
    fireEvent.change(screen.getByLabelText("Day"), { target: { value: "2025-01-09" } });
    expect((screen.getByLabelText("Day") as HTMLInputElement).value).toBe("2025-01-09");
    rerender(<KlineChart runId="r2" universe={["SPY"]} startDay="2025-02-03" family="intraday" decisions={[]} onSelectDecision={onSelectDecision} />);
    await waitFor(() => expect((screen.getByLabelText("Day") as HTMLInputElement).value).toBe("2025-02-03"));
  });
});
