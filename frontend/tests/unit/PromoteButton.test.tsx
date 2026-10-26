/**
 * Tests for app/components/PromoteButton.tsx (Phase D-full-1).
 *
 * Wraps the component in a QueryClientProvider; uses MSW to intercept
 * the /api/decisions/:id/promote endpoint (httpClient rewrites /v1/* →
 * /api/*). useNavigate is mocked via vi.mock so we can assert the
 * navigation target without booting the TanStack router.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
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

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
}));

import PromoteButton from "../../app/components/PromoteButton";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  navigateMock.mockReset();
});
afterAll(() => server.close());

function renderWithClient(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("PromoteButton", () => {
  it("renders Promote affordance for unpromoted decisions", () => {
    renderWithClient(<PromoteButton decisionId="dec-1" />);
    expect(screen.getByTestId("promote-button")).toBeTruthy();
    expect(screen.queryByTestId("promoted-link")).toBeNull();
  });

  it("renders Promoted → link when already promoted", () => {
    renderWithClient(
      <PromoteButton decisionId="dec-1" promotedCheckpointId="ckpt-9" />,
    );
    expect(screen.getByTestId("promoted-link")).toBeTruthy();
    expect(screen.queryByTestId("promote-button")).toBeNull();
  });

  it("navigates to checkpoint on successful promote", async () => {
    server.use(
      http.post("/api/decisions/dec-1/promote", () =>
        HttpResponse.json({ id: "ckpt-42", status: "ready" }, { status: 201 }),
      ),
    );
    renderWithClient(<PromoteButton decisionId="dec-1" />);

    await act(async () => {
      fireEvent.click(screen.getByTestId("promote-button"));
    });

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith({
        to: "/checkpoints/$ckptId",
        params: { ckptId: "ckpt-42" },
      }),
    );
  });

  it("surfaces an error message on 5xx", async () => {
    server.use(
      http.post("/api/decisions/dec-1/promote", () =>
        HttpResponse.json(
          { type: "server-error", title: "Internal", status: 500 },
          { status: 500, headers: { "content-type": "application/problem+json" } },
        ),
      ),
    );
    renderWithClient(<PromoteButton decisionId="dec-1" />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("promote-button"));
    });
    await waitFor(() =>
      expect(screen.queryByTestId("promote-error")).not.toBeNull(),
    );
  });

  it("captures conflict checkpoint id from 409 problem detail", async () => {
    server.use(
      http.post("/api/decisions/dec-1/promote", () =>
        HttpResponse.json(
          {
            type: "already-promoted",
            title: "Decision already promoted",
            status: 409,
            detail: "checkpoint_id=ckpt-existing-9",
          },
          { status: 409, headers: { "content-type": "application/problem+json" } },
        ),
      ),
    );
    renderWithClient(<PromoteButton decisionId="dec-1" />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("promote-button"));
    });
    await waitFor(() =>
      expect(screen.queryByTestId("promoted-link")).not.toBeNull(),
    );
  });
});
