/**
 * Accessibility regression tests (Plan C Task 14).
 *
 * These tests assert that shipped components expose the core landmark,
 * labelling, and live-region semantics an AT user needs. They are intentionally
 * lightweight: full axe-core sweeps run in Playwright e2e; here we lock in the
 * handful of attributes the Task 14 audit added so future edits can't
 * silently regress them.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import React from "react";

// jsdom does not implement ResizeObserver; Recharts' ResponsiveContainer
// crashes without it. Stub a no-op so `<PnLChart>` mounts in unit tests.
if (typeof globalThis.ResizeObserver === "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom does not implement matchMedia; ThemeToggle calls it to detect
// prefers-color-scheme. Stub it to return a light-mode matcher.
if (typeof globalThis.matchMedia === "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Navbar from "../../app/components/Navbar";
import RunCard from "../../app/components/RunCard";
import DecisionTimeline from "../../app/components/DecisionTimeline";
import PnLChart from "../../app/components/PnLChart";
import SSEIndicator from "../../app/components/SSEIndicator";
import EventStream from "../../app/components/EventStream";

// Minimal Clerk mock: Navbar mounts <SignedIn>/<SignedOut>/<SignOutButton>
// which otherwise pull in a ClerkProvider. Returning plain children is enough
// to exercise the nav landmark + skip link rendering under jsdom.
// NewRunCTA calls useAuth() to determine launch eligibility.
import { vi } from "vitest";
vi.mock("@clerk/tanstack-react-start", () => ({
  SignedIn: ({ children }: { children: React.ReactNode }) => children,
  SignedOut: () => null,
  SignOutButton: ({ children }: { children: React.ReactNode }) => children,
  useAuth: () => ({ userId: "test-user-id" }),
}));

describe("a11y: Navbar", () => {
  it("exposes a primary nav landmark and a skip link", () => {
    // NewRunCTA calls useAuth() which the Clerk mock provides.
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(Navbar),
      ),
    );
    const nav = container.querySelector("nav");
    expect(nav).not.toBeNull();
    expect(nav?.getAttribute("aria-label")).toBe("Primary");
    const skip = container.querySelector('a[href="#main-content"]');
    expect(skip?.textContent).toMatch(/skip to main/i);
  });
});

describe("a11y: RunCard", () => {
  it("provides an aria-label describing the target run", () => {
    const { container } = render(
      React.createElement(RunCard, {
        run: { id: "run_42", title: "My run", status: "running" },
      }),
    );
    const a = container.querySelector('[data-testid="run-card"]');
    expect(a?.getAttribute("aria-label")).toMatch(/My run/);
    expect(a?.getAttribute("aria-label")).toMatch(/running/);
  });
});

describe("a11y: DecisionTimeline", () => {
  it("labels the list and marks the selected row with aria-current", () => {
    const decisions = [
      { id: "d1", seq: 1, trading_day: "2024-01-02", has_trade: true },
      { id: "d2", seq: 2, trading_day: "2024-01-03", has_trade: false },
    ];
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { container } = render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(DecisionTimeline, {
          decisions,
          selectedSeq: 2,
          onSelect: () => {},
        }),
      ),
    );
    const list = container.querySelector('[data-testid="decision-timeline"]');
    expect(list?.getAttribute("aria-label")).toBe("Decision timeline");
    const rows = container.querySelectorAll('[data-testid="decision-row"]');
    expect(rows.length).toBe(2);
    expect(rows[0].getAttribute("aria-current")).toBeNull();
    expect(rows[1].getAttribute("aria-current")).toBe("true");
    // Each button carries a descriptive aria-label.
    expect(rows[0].getAttribute("aria-label")).toMatch(/Decision 1/);
  });
});

describe("a11y: PnLChart", () => {
  it("renders as role=img with a summary aria-label", () => {
    const { container } = render(
      React.createElement(PnLChart, {
        points: [
          { trading_day: "2024-01-02", cum_pnl: 100 },
          { trading_day: "2024-01-03", cum_pnl: 150.25 },
        ],
      }),
    );
    const chart = container.querySelector('[data-testid="pnl-chart"]');
    expect(chart?.getAttribute("role")).toBe("img");
    expect(chart?.getAttribute("aria-label")).toMatch(/Cumulative PnL/);
    expect(chart?.getAttribute("aria-label")).toMatch(/150\.25/);
  });
});

describe("a11y: SSEIndicator", () => {
  it("exposes role=status with aria-live=polite", () => {
    const { container } = render(
      React.createElement(SSEIndicator, { status: "open" }),
    );
    const pill = container.querySelector('[data-testid="sse-indicator"]');
    expect(pill?.getAttribute("role")).toBe("status");
    expect(pill?.getAttribute("aria-live")).toBe("polite");
    expect(pill?.getAttribute("aria-label")).toMatch(/Live/);
  });
});

describe("a11y: EventStream", () => {
  it("marks the list aria-live=polite so new events announce", () => {
    const { container } = render(
      React.createElement(EventStream, {
        events: [{ id: 1, ts: "2024-01-02T10:00:00Z", kind: "run.started", summary: "ok" }],
      }),
    );
    const ul = container.querySelector('[data-testid="event-stream"]');
    expect(ul?.getAttribute("aria-live")).toBe("polite");
    expect(ul?.getAttribute("aria-label")).toBe("Event stream");
  });
});
