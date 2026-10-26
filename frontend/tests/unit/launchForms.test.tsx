/**
 * Tests for family-specific launch subforms (Phase D-full-2).
 *
 * Each subform is presentational and pure (no network, no router). We
 * assert: render, onChange dispatch on field edit, and field reflection
 * back into the rendered value.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import OverlayForm from "../../app/components/launch/OverlayForm";
import EarningsForm from "../../app/components/launch/EarningsForm";
import IntradayForm from "../../app/components/launch/IntradayForm";
import type {
  EarningsValues,
  IntradayValues,
  OverlayValues,
} from "../../app/components/launch/types";

const MOCK_UNDERLYINGS = ["AAPL", "AMD", "NVDA", "QQQ", "SPY"];

describe("OverlayForm", () => {
  it("renders paper default holdings (QQQ + NVDA) when value has no holdings", () => {
    render(
      <OverlayForm value={{}} onChange={() => {}} supportedUnderlyings={MOCK_UNDERLYINGS} />,
    );
    expect(screen.getByTestId("overlay-form")).toBeTruthy();
    // Paper defaults: 2 holdings visible
    expect(screen.getByTestId("overlay-holding-0")).toBeTruthy();
    expect(screen.getByTestId("overlay-holding-1")).toBeTruthy();
  });

  it("renders empty state when holdings explicitly set to []", () => {
    render(
      <OverlayForm
        value={{ underlying_holdings: [] }}
        onChange={() => {}}
        supportedUnderlyings={MOCK_UNDERLYINGS}
      />,
    );
    expect(screen.getByTestId("overlay-form")).toBeTruthy();
    expect(screen.getByText(/No holdings yet/i)).toBeTruthy();
  });

  it("dispatches onChange when adding a holding", () => {
    const onChange = vi.fn();
    render(
      <OverlayForm
        value={{ underlying_holdings: [] }}
        onChange={onChange}
        supportedUnderlyings={MOCK_UNDERLYINGS}
      />,
    );
    fireEvent.click(screen.getByTestId("overlay-add-holding"));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as OverlayValues;
    expect(next.underlying_holdings?.length).toBe(1);
  });

  it("propagates DTE range edits", () => {
    const onChange = vi.fn();
    render(
      <OverlayForm
        value={{ overlay_dte_range: [21, 45] }}
        onChange={onChange}
        supportedUnderlyings={MOCK_UNDERLYINGS}
      />,
    );
    fireEvent.change(screen.getByTestId("overlay-dte-lo"), {
      target: { value: "30" },
    });
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls.at(-1)![0] as OverlayValues;
    expect(next.overlay_dte_range).toEqual([30, 45]);
  });

  it("symbol picker shows allowlist as select options", () => {
    render(
      <OverlayForm
        value={{ underlying_holdings: [{ symbol: "QQQ", shares: 100, cost_basis: 0 }] }}
        onChange={() => {}}
        supportedUnderlyings={MOCK_UNDERLYINGS}
      />,
    );
    const select = screen.getByRole("combobox", { name: /holding 0 symbol/i }) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toEqual(MOCK_UNDERLYINGS);
  });
});

describe("EarningsForm", () => {
  it("defaults to all 24 paper tickers selected", () => {
    render(<EarningsForm value={{}} onChange={() => {}} />);
    // All 24 chips should be aria-pressed=true by default
    const chips = screen.getAllByRole("button").filter(
      (b) => b.getAttribute("aria-pressed") !== null,
    );
    expect(chips.length).toBe(24);
    expect(chips.every((c) => c.getAttribute("aria-pressed") === "true")).toBe(true);
  });

  it("toggling a chip deselects it and dispatches onChange", () => {
    const onChange = vi.fn();
    render(<EarningsForm value={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("earnings-chip-AAPL"));
    const next = onChange.mock.calls.at(-1)![0] as EarningsValues;
    expect(next.tickers).not.toContain("AAPL");
    expect(next.tickers?.length).toBe(23);
  });

  it("Select all / Clear all / Reset to paper utilities work", () => {
    const onChange = vi.fn();
    render(<EarningsForm value={{ tickers: [] }} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("earnings-select-all"));
    expect((onChange.mock.calls.at(-1)![0] as EarningsValues).tickers?.length).toBe(24);
    fireEvent.click(screen.getByTestId("earnings-clear-all"));
    expect((onChange.mock.calls.at(-1)![0] as EarningsValues).tickers?.length).toBe(0);
    fireEvent.click(screen.getByTestId("earnings-reset"));
    expect((onChange.mock.calls.at(-1)![0] as EarningsValues).tickers?.length).toBe(24);
  });

  it("selects a quarter via radio", () => {
    const onChange = vi.fn();
    render(<EarningsForm value={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("earnings-quarter-2025Q3"));
    const next = onChange.mock.calls.at(-1)![0] as EarningsValues;
    expect(next.quarter).toBe("2025Q3");
  });
});

describe("IntradayForm", () => {
  it("shows the read-only SPY preset underlying banner", () => {
    render(<IntradayForm value={{}} onChange={() => {}} />);
    const banner = screen.getByTestId("intraday-underlying");
    expect(banner).toBeTruthy();
    expect(banner.textContent).toMatch(/SPY/);
    expect(banner.textContent).toMatch(/preset/i);
  });

  it("renders interval radios and propagates selection", () => {
    const onChange = vi.fn();
    render(<IntradayForm value={{}} onChange={onChange} />);
    fireEvent.click(screen.getByTestId("intraday-interval-5"));
    const next = onChange.mock.calls.at(-1)![0] as IntradayValues;
    expect(next.decision_interval_minutes).toBe(5);
  });

  it("renders LLM choices when provided", () => {
    render(
      <IntradayForm
        value={{}}
        onChange={() => {}}
        llmChoices={["deepseek-chat", "gemini-2.5-flash"]}
      />,
    );
    const sel = screen.getByTestId("intraday-llm-model") as HTMLSelectElement;
    expect(sel.options.length).toBe(3); // default + 2
  });

  it("clamps prompt override length via maxLength", () => {
    const onChange = vi.fn();
    render(<IntradayForm value={{ prompt_override: "hi" }} onChange={onChange} />);
    const ta = screen.getByTestId("intraday-prompt") as HTMLTextAreaElement;
    expect(ta.value).toBe("hi");
    expect(ta.maxLength).toBe(16384);
  });
});
