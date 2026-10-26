/**
 * Playwright routing helper — installs the hermetic FastAPI fixtures defined
 * in `handlers.ts` onto a `BrowserContext`. Uses `context.route()` so every
 * page/tab in the context sees the same stubs.
 *
 * Matching semantics:
 *   - Method + pathname must match exactly (query string ignored for match,
 *     but handlers can still read it via the `url` arg).
 *   - `:param` segments are extracted and passed to handler functions.
 *   - First match wins; override with `routing.use(h)` to prepend handlers.
 */
import type { BrowserContext, Route } from "@playwright/test";
import { handlers as defaultHandlers, type RouteHandler } from "./handlers";

function compile(path: string): { re: RegExp; keys: string[] } {
  const keys: string[] = [];
  const re = new RegExp(
    "^" +
      path
        .replace(/\//g, "\\/")
        .replace(/:([A-Za-z_][A-Za-z0-9_]*)/g, (_m, k) => {
          keys.push(k);
          return "([^/]+)";
        }) +
      "$",
  );
  return { re, keys };
}

export class Routing {
  private readonly stack: RouteHandler[];

  constructor(initial: RouteHandler[] = defaultHandlers) {
    this.stack = [...initial];
  }

  /** Prepend a handler so it wins over defaults. */
  use(h: RouteHandler): void {
    this.stack.unshift(h);
  }

  async install(context: BrowserContext): Promise<void> {
    await context.route(
      (url) =>
        url.pathname.startsWith("/v1/") ||
        url.pathname.startsWith("/api/"),
      (route: Route) => this.handle(route),
    );
  }

  private async handle(route: Route): Promise<void> {
    const req = route.request();
    const url = new URL(req.url());
    // The httpClient rewrites /v1/* → /api/* before fetching (same-origin
    // proxy contract). Normalise back to /v1/* so handler paths match.
    // e.g. /api/runs/run_beta → /v1/runs/run_beta
    const pathname = url.pathname.startsWith("/api/")
      ? "/v1/" + url.pathname.slice("/api/".length)
      : url.pathname;
    const method = req.method().toUpperCase();

    for (const h of this.stack) {
      if (h.method !== method) continue;
      const { re, keys } = compile(h.path);
      const m = re.exec(pathname);
      if (!m) continue;
      const params: Record<string, string> = {};
      keys.forEach((k, i) => {
        params[k] = decodeURIComponent(m[i + 1]!);
      });
      const body =
        typeof h.body === "function" ? h.body(params, url) : h.body;
      await route.fulfill({
        status: h.status ?? 200,
        contentType: h.contentType ?? "application/json",
        body: JSON.stringify(body),
      });
      return;
    }
    // Unhandled /v1 or /api request — fail loudly so tests stay hermetic.
    await route.fulfill({
      status: 501,
      contentType: "application/problem+json",
      body: JSON.stringify({
        type: "about:blank",
        title: "E2E: unhandled route",
        status: 501,
        detail: `No fixture for ${method} ${pathname}`,
      }),
    });
  }
}
