import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AvsrExample, DataExample, DecisionSchedule } from "../../app/components/ConfigurationExamples";
import ResultVisualizations from "../../app/components/run-dashboard/ResultVisualizations";
import RunLogs from "../../app/components/run-dashboard/RunLogs";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });
it("shows a nine-cell AVSR example and explains the selected illustrative contract", () => {
  render(<AvsrExample value={{ moneyness_anchors: [0.95, 1, 1.05], expiry_anchors: [7, 30, 90], distance_lambda: 0.01 }} zh={false} />);
  expect(screen.getAllByRole("button")).toHaveLength(9);
  fireEvent.click(screen.getAllByRole("button")[0]);
  expect(screen.getByText(/Target: K\/S 95.0%, 7d/)).toBeTruthy();
  expect(screen.getByText(/Illustrative data/)).toBeTruthy();
});
it("uses the selected DTE and Delta bounds in the data-rule illustration", () => {
  const { container } = render(<DataExample values={{ chain_filter_delta_min: 0.2, chain_filter_delta_max: 0.6, chain_max_contracts_per_ticker: 1 }} dteMin={7} dteMax={30} zh={false} />);
  expect([...container.querySelectorAll("td")].filter((td) => td.textContent === "Keep")).toHaveLength(2);
  expect(screen.getAllByText("Exclude · Candidate cap")).toHaveLength(2);
});
it("shows the actual interval-based full-session illustration", () => {
  const { container } = render(<DecisionSchedule daily={false} time="15:45" interval={30} onTime={vi.fn()} zh={false} />);
  expect(container.querySelectorAll("circle")).toHaveLength(13);
  expect(screen.getByText(/09:59, 10:29/)).toBeTruthy();
});
it("paginates results and does not draw across missing observations", () => {
  const points = Array.from({ length: 45 }, (_, i) => ({ trading_day: `2025-01-${String(i + 1).padStart(2, "0")}`, portfolio_value: i === 1 ? null : 1000 + i, daily_pnl: 1, cum_pnl: i, drawdown_pct: -0.01, decisions: 1, trade_proposals: 0 }));
  const { container } = render(<ResultVisualizations points={points} zh={false} />);
  expect(container.querySelectorAll("polyline")).toHaveLength(2);
  fireEvent.click(screen.getByText(/Daily results table/));
  expect(container.querySelectorAll("tbody tr")).toHaveLength(20);
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(container.querySelectorAll("tbody tr")).toHaveLength(5);
  fireEvent.click(screen.getByRole("tab", { name: "Daily-equity drawdown" }));
  expect(container.textContent).toContain("-1.00%");
});
it("shows the worker failure, older pages and a log download entry", async () => {
  localStorage.setItem("lo-locale", "en");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ available: true, text: "NameError: templates is not defined", next_before: 100, start: 100, end: 1000, total_bytes: 1000, exit_code: 1, error_message: "NameError: templates is not defined" }) }));
  render(<RunLogs runId="failed-run" status="failed" />);
  await screen.findByText("Latest error · exit 1");
  expect(screen.getByRole("link", { name: "Download full log" }).getAttribute("href")).toContain("download=true");
  fireEvent.click(screen.getByRole("button", { name: "Older logs" }));
  expect(fetch).toHaveBeenLastCalledWith("/api/tasks/failed-run/logs?before=100");
});
