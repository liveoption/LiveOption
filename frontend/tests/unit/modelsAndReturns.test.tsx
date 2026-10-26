import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ModelPicker, { type PickerModel, type PickerGroup } from "../../app/components/ModelPicker";
import ReturnsChart from "../../app/components/run-dashboard/ReturnsChart";
import MarketCharts from "../../app/components/run-dashboard/MarketCharts";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });
const groups = [{ id: "g1", label: "Group 1", has_api_key: true }, { id: "g2", label: "Group 2", has_api_key: true }];
const models = [{ id: "old", label: "Model A", api_model: "model-a", group_id: "g1" }, { id: "other", label: "Model B", group_id: "g2" }, { id: "retired", label: "Retired", group_id: "g1", available: false }];
function Picker({ uninitialized = false }: { uninitialized?: boolean }) {
  const [registry, setRegistry] = useState<{ models: PickerModel[]; groups: PickerGroup[] }>({ models, groups: uninitialized ? groups.map((g) => ({ ...g, models_updated_at: null })) : groups });
  const [value, setValue] = useState("old");
  return <ModelPicker {...registry} value={value} onChange={setValue} onRegistry={setRegistry} onBusy={() => {}} zh={false} />;
}
it("separates API groups and excludes disappeared models", async () => {
  render(<Picker />);
  await waitFor(() => expect((screen.getByLabelText("API group") as HTMLSelectElement).value).toBe("g1"));
  expect(screen.getByRole("option", { name: /Model A/ })).toBeTruthy();
  expect(screen.queryByRole("option", { name: /Retired/ })).toBeNull();
  expect(screen.queryByRole("option", { name: /Model B/ })).toBeNull();
  fireEvent.change(screen.getByLabelText("API group"), { target: { value: "g2" } });
  expect((screen.getByLabelText("Run model") as HTMLSelectElement).value).toBe("other");
});
it("automatically fetches an uninitialized group catalog and selects discovered models", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ groups: groups.map((g) => ({ ...g, models_updated_at: "2026-10-22T00:00:00Z" })), models: [...models, { id: "new", label: "New model", api_model: "new-model", group_id: "g1" }] }) }));
  render(<Picker uninitialized />);
  await screen.findByRole("option", { name: /New model/ });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith("/api/model-settings/g1/models/refresh", { method: "POST" });
  fireEvent.change(screen.getByLabelText("Run model"), { target: { value: "new" } });
  expect((screen.getByLabelText("Run model") as HTMLSelectElement).value).toBe("new");
});
it("keeps the old catalog and explains upstream authentication failures", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 502, json: async () => ({ detail: "models_endpoint_http_401" }) }));
  render(<Picker />);
  await waitFor(() => expect((screen.getByLabelText("API group") as HTMLSelectElement).value).toBe("g1"));
  fireEvent.click(screen.getByRole("button", { name: /Refresh group models/ }));
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).toContain("rejected the key");
  expect(screen.getByRole("option", { name: /Model A/ })).toBeTruthy();
});

const dates = ["2025-01-06", "2025-01-07", "2025-01-08"];
const comparison = { baseline_date: dates[0], comparison_end: dates[2], provisional: false, unavailable: [], warnings: [], series: [
  { id: "QQQ", label: "QQQ", kind: "underlying", baseline_value: 100, points: dates.map((date, i) => ({ date, value: 100 + i, return_pct: i * 0.01, index_value: 100 + i })) },
  { id: "NVDA", label: "NVDA", kind: "underlying", baseline_value: 200, points: dates.map((date, i) => ({ date, value: i === 1 ? null : 200 - i, return_pct: i === 1 ? null : -i * 0.01, index_value: i === 1 ? null : 100 - i })) },
  { id: "account", label: "account", kind: "account", baseline_value: 1000, points: dates.map((date, i) => ({ date, value: 1000 + i * 10, return_pct: i * 0.01, index_value: 100 + i })) },
] };
it("compares account and all assets on shared dates, preserves gaps, and supports indexed facets", async () => {
  localStorage.setItem("lo-locale", "en");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => comparison }));
  const { container } = render(<ReturnsChart runId="r" universe={["QQQ", "NVDA"]} status="completed" />);
  await screen.findByText(/Shared baseline: 2025-01-06/);
  expect(container.querySelectorAll("polyline")).toHaveLength(4);
  fireEvent.change(screen.getByLabelText("Returns layout"), { target: { value: "facets" } });
  expect(screen.getAllByTestId("return-plot")).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("Y axis"), { target: { value: "index_value" } });
  expect(container.textContent).toContain("102.00");
  fireEvent.click(screen.getByLabelText("NVDA"));
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining("symbols=QQQ&account=true"), expect.anything()));
});
it("shows separate candle panels for multiple assets and switches to returns", async () => {
  localStorage.setItem("lo-locale", "en");
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve({ ok: true, json: async () => url.includes("returns") ? comparison : { source: "qfinzero.upq", resolution: "1d", bars: [] } })));
  render(<MarketCharts runId="r" universe={["QQQ", "NVDA"]} family="overlay" decisions={[]} onSelectDecision={() => {}} />);
  fireEvent.change(screen.getByLabelText("Candle layout"), { target: { value: "facets" } });
  expect(screen.getAllByTestId("kline-chart")).toHaveLength(2);
  fireEvent.click(screen.getByRole("tab", { name: "Asset & account returns" }));
  await screen.findByTestId("returns-chart");
  expect(screen.queryByTestId("kline-chart")).toBeNull();
});
