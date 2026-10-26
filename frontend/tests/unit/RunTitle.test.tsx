/**
 * Tests for `app/components/RunTitle.tsx` — inline rename affordance for
 * the run-detail page header.
 *
 * Covers:
 *   - Renders the title fallback chain (`title || scenario || id`).
 *   - Click swaps to an input editor, Escape cancels without PATCH.
 *   - Pressing Enter sends a PATCH request with the trimmed title and
 *     calls `onSaved` afterwards.
 *
 * Network is intercepted via MSW; orval's `httpClient` rewrites `/v1/*`
 * to `/api/*` so handlers register the same-origin path.
 */
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RunTitle } from "../../app/components/RunTitle";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("RunTitle", () => {
  it("renders the title when present, scenario fallback otherwise", () => {
    const { rerender } = renderWithClient(
      <RunTitle
        runId="run_x"
        title="Friendly name"
        scenario="baseline_2024"
      />,
    );
    expect(screen.getByTestId("run-title").textContent).toBe("Friendly name");
    rerender(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <RunTitle runId="run_x" title={null} scenario="baseline_2024" />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("run-title").textContent).toBe("baseline_2024");
  });

  it("Escape cancels editing without a PATCH", async () => {
    let patched = false;
    server.use(
      http.patch("/api/runs/run_x", () => {
        patched = true;
        return HttpResponse.json({});
      }),
    );

    renderWithClient(
      <RunTitle runId="run_x" title="Original" scenario={null} />,
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("run-title"));
    });

    const input = (await screen.findByTestId(
      "run-title-input",
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Other" } });
    fireEvent.keyDown(input, { key: "Escape" });

    await waitFor(() =>
      expect(screen.queryByTestId("run-title-input")).toBeNull(),
    );
    expect(patched).toBe(false);
  });

  it("Enter PATCHes the trimmed title and fires onSaved", async () => {
    const seen: { url?: string; body?: unknown } = {};
    server.use(
      http.patch("/api/runs/run_x", async ({ request }) => {
        seen.url = request.url;
        seen.body = await request.json();
        return HttpResponse.json({});
      }),
    );

    const onSaved = vi.fn();
    renderWithClient(
      <RunTitle
        runId="run_x"
        title="Old"
        scenario={null}
        onSaved={onSaved}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("run-title"));
    });
    const input = (await screen.findByTestId(
      "run-title-input",
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  Renamed  " } });
    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(seen.body).toEqual({ title: "Renamed" });
  });
});
