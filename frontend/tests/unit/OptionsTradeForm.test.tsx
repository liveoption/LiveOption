import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { OptionsTradeForm } from "../../app/components/OptionsTradeForm";

const server = setupServer(
  http.get("/api/scenarios", () => HttpResponse.json({
    items: [
      { id: "intraday_spy_2025_q1" },
      { id: "overlay_spy_qqq_2025_q1" },
    ],
    llm_model_choices: ["gemini-3-flash", "deepseek-chat"],
  })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("OptionsTradeForm", () => {
  it("submits only the options scenario with user context and settings", async () => {
    const onSubmit = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><OptionsTradeForm onSubmit={onSubmit} /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByTestId("options-launch-submit")).not.toHaveProperty("disabled", true));
    fireEvent.change(screen.getByTestId("options-context"), { target: { value: "Prefer cash when spreads widen." } });
    fireEvent.click(screen.getByTestId("options-launch-submit"));

    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      scenario_id: "intraday_spy_2025_q1",
      overrides: {
        llm_model: "gemini-3-flash",
        prompt_override: "Prefer cash when spreads widen.",
        decision_interval_minutes: 15,
        max_concurrent_positions: 5,
        dte_range: [0, 7],
      },
    });
    expect(screen.queryByText(/Overlay QQQ/i)).toBeNull();
  });
});
