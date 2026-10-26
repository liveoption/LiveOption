import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RunSpecEditor from "../../app/components/RunSpecEditor";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });
const base = {
  id: "intraday_spy_2025_q1", name: "SPY preset", description: "Old description", family: "intraday",
  experiment: { start: "2025-01-06", end: "2025-01-10", initial_cash: 100000, universe: ["SPY"] },
  strategy_params: { decision_interval_minutes: 15, dte_min: 0, dte_max: 7, max_positions: 5 },
  risk: { max_daily_loss_pct: 0.02 }, data_policy: { news_enabled: false, news_lookback_days: 7, lookback_bars: 60 },
  avsr: { moneyness_anchors: [0.95, 1, 1.05], expiry_anchors: [1, 7, 30], distance_lambda: 0.01 },
  prompt_templates: { system: "Trade {universe}", user: "Chain: {chain_sample_table}" }, context: "",
  prompt_variables: {
    system: [{ name: "universe", description: "标的", description_en: "Underlyings", source: "设置", example: "SPY" }],
    user: [{ name: "chain_sample_table", description: "候选期权合约", source: "期权链", example: "SPY call" }],
  },
};
const customTypes = [
  { ...base, id: "custom_intraday", cadence: "intraday", context: "" },
  { ...base, id: "custom_daily", cadence: "daily", family: "daily", strategy_params: { dte_min: 7, dte_max: 60, max_positions: 5 }, prompt_templates: { ...base.prompt_templates, system: "Daily options on {universe}" } },
];
function setup() {
  localStorage.setItem("lo-locale", "en");
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve({ ok: true, json: async () => url.includes("presets")
    ? { items: [base], custom_types: customTypes, supported_underlyings: ["SPY", "QQQ"] }
    : { models: [{ id: "m1", label: "Model 1", group_id: "g1" }], groups: [{ id: "g1", label: "Group 1", has_api_key: true }] },
  })));
  const submit = vi.fn(); render(<RunSpecEditor onSubmit={submit} />); return submit;
}
function next() { fireEvent.click(screen.getByRole("button", { name: "Continue" })); }

describe("task creation flow", () => {
  it("takes a beginner through four steps and submits preset settings", async () => {
    const submit = setup();
    await screen.findByRole("button", { name: /^Intraday/ });
    expect(screen.queryByText("SPY preset")).toBeNull();
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
    next();
    fireEvent.change(screen.getByLabelText("Option fee / contract · USD"), { target: { value: "1.25" } });
    next();
    expect(screen.getByLabelText("system prompt").tagName).toBe("PRE");
    fireEvent.change(screen.getByLabelText("Trading context"), { target: { value: "Keep cash when uncertain" } });
    next();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect(submit).toHaveBeenCalledWith(expect.objectContaining({ mode: "preset", preset_id: base.id,
      context: "Keep cash when uncertain", execution: expect.objectContaining({ option_fee_per_contract: 1.25 }) }));
  });

  it("creates a daily custom task on operator-provided data without a preset", async () => {
    const submit = setup(); await screen.findByRole("button", { name: /^Intraday/ });
    fireEvent.click(screen.getByRole("tab", { name: "Custom task" }));
    fireEvent.click(screen.getByRole("button", { name: "Daily" }));
    expect((screen.getByLabelText("Starting cash · USD") as HTMLInputElement).value).toBe("100000");
    expect(screen.queryByText("Trading engine")).toBeNull();
    fireEvent.change(screen.getByLabelText("Underlyings (comma-separated)"), { target: { value: "QZSP" } });
    next();
    expect(screen.queryByLabelText("Decision interval (min)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Daily decision time (ET)"), { target: { value: "10:30" } });
    next();
    fireEvent.change(screen.getByLabelText("system prompt"), { target: { value: "My own daily options rules" } });
    fireEvent.click(screen.getByRole("button", { name: "User" }));
    expect(screen.getByText("{chain_sample_table}", { selector: "code" })).toBeTruthy();
    next(); fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    const spec = submit.mock.calls[0][0];
    expect(spec).toMatchObject({ mode: "custom", cadence: "daily", decision_time_et: "10:30", experiment: { universe: ["QZSP"] }, prompt_templates: { system: "My own daily options rules" } });
    expect(spec).not.toHaveProperty("preset_id");
    expect(spec.strategy_params).not.toHaveProperty("stock_qty");
  });

  it("keeps separate drafts for presets and custom categories", async () => {
    setup(); await screen.findByRole("button", { name: /^Intraday/ });
    fireEvent.change(screen.getByLabelText("Run name (optional)"), { target: { value: "Preset draft" } });
    fireEvent.click(screen.getByRole("tab", { name: "Custom task" }));
    fireEvent.change(screen.getByLabelText("Run name (optional)"), { target: { value: "Intraday draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Daily" }));
    expect((screen.getByLabelText("Run name (optional)") as HTMLInputElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Intraday" }));
    expect((screen.getByLabelText("Run name (optional)") as HTMLInputElement).value).toBe("Intraday draft");
    fireEvent.click(screen.getByRole("tab", { name: /^Preset$/ }));
    expect((screen.getByLabelText("Run name (optional)") as HTMLInputElement).value).toBe("Preset draft");
  });

  it("stops invalid prompt variables before review or submission", async () => {
    const submit = setup(); await screen.findByRole("button", { name: /^Intraday/ });
    fireEvent.click(screen.getByRole("tab", { name: "Custom task" })); next(); next();
    fireEvent.click(screen.getByRole("button", { name: "User" }));
    fireEvent.change(screen.getByLabelText("user prompt"), { target: { value: "Use {fake}" } });
    expect(screen.getByText("Unsupported variables: {fake}")).toBeTruthy();
    next(); expect(submit).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
  });
});
