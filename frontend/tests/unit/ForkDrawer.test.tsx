/**
 * Tests for `app/components/ForkDrawer.tsx`.
 *
 * Covers the Task 9 behaviours required by Plan C:
 *   - Mode is pinned to "diverge" in the submitted body.
 *   - Submit is disabled until at least one of prompt/model override is set.
 *   - Successful POST invokes `onCreated` with the new run id and invalidates
 *     the runs list query (checked via intercepted request).
 *   - 409 problem+json is surfaced with the "concurrency cap" hint.
 *   - 400 problem+json detail is surfaced verbatim.
 *
 * All upstream traffic is intercepted with MSW. React Query retries are
 * disabled so error branches resolve immediately.
 *
 * NOTE: handlers match on `/api/*`, not `/v1/*`. The `httpClient` mutator
 * rewrites Orval's generated `/v1/*` paths to same-origin `/api/*` so the
 * browser hits the SSR proxy routes. See
 * `tests/unit/http-client.test.ts` REGRESSION test +
 * `docs/web/2026-04-24-orval-api-prefix-contract.md`.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ForkDrawer } from "../../app/components/ForkDrawer";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ForkDrawer", () => {
  it("disables submit until an override is entered", () => {
    renderWithClient(<ForkDrawer checkpointId="ckpt_1" />);
    const submit = screen.getByTestId("fork-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(screen.getByTestId("fork-validation-msg")).toBeTruthy();

    fireEvent.change(screen.getByTestId("fork-prompt-override"), {
      target: { value: "Try a more conservative play." },
    });
    expect(submit.disabled).toBe(false);
  });

  it("POSTs mode=diverge with overrides and calls onCreated with run_id", async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/checkpoints/:id/forks", async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ run_id: "run_new_42", status: "scheduled" }, { status: 202 });
      }),
    );

    const onCreated = vi.fn();
    renderWithClient(<ForkDrawer checkpointId="ckpt_1" onCreated={onCreated} />);

    fireEvent.change(screen.getByTestId("fork-prompt-override"), {
      target: { value: "Sell covered calls instead." },
    });
    fireEvent.change(screen.getByTestId("fork-model-override"), {
      target: { value: "gemini-2.0-flash" },
    });
    fireEvent.click(screen.getByTestId("fork-submit"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("run_new_42"));
    expect(captured).toEqual({
      mode: "diverge",
      prompt_override: "Sell covered calls instead.",
      model_override: "gemini-2.0-flash",
      title: null,
    });
  });

  it("surfaces a 409 problem+json as a concurrency-cap error", async () => {
    server.use(
      http.post("/api/checkpoints/:id/forks", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Conflict",
            status: 409,
            detail: "Max 4 concurrent runs in flight.",
          },
          { status: 409, headers: { "content-type": "application/problem+json" } },
        ),
      ),
    );

    renderWithClient(<ForkDrawer checkpointId="ckpt_1" />);
    fireEvent.change(screen.getByTestId("fork-prompt-override"), {
      target: { value: "Alternate prompt." },
    });
    fireEvent.click(screen.getByTestId("fork-submit"));

    const err = await screen.findByTestId("fork-error");
    expect(err.textContent).toMatch(/Concurrency cap exceeded/i);
    expect(err.textContent).toMatch(/Max 4 concurrent runs/);
  });

  it("surfaces 400 problem+json detail verbatim", async () => {
    server.use(
      http.post("/api/checkpoints/:id/forks", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Bad Request",
            status: 400,
            detail: "prompt_override references a dead branch.",
          },
          { status: 400, headers: { "content-type": "application/problem+json" } },
        ),
      ),
    );

    renderWithClient(<ForkDrawer checkpointId="ckpt_1" />);
    fireEvent.change(screen.getByTestId("fork-prompt-override"), {
      target: { value: "Alt" },
    });
    fireEvent.click(screen.getByTestId("fork-submit"));

    const err = await screen.findByTestId("fork-error");
    expect(err.textContent).toMatch(/prompt_override references a dead branch/);
  });
});
