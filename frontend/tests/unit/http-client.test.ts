/**
 * Tests for `app/lib/http-client.ts`.
 *
 * Covers both sides of the module:
 *   - `httpClient` (browser Orval mutator): JSON decoding, 204 no-body,
 *     RFC 7807 problem+json error propagation. Signature matches Orval's
 *     fetch client: `httpClient(url, options: RequestInit)`.
 *   - `backendFetch` (server proxy helper): internal-secret header,
 *     access-token actor header, 401 on missing session, upstream URL
 *     resolution, and that the upstream `ReadableStream` is forwarded
 *     through `passthroughSse` without buffering.
 *
 * All upstream traffic is intercepted with MSW (`setupServer`) — no real
 * sockets. Access-token sessions are exercised with a test-only token.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { sessionCookie } from "../../app/lib/server-token-auth";

import {
  backendFetch,
  getUpstreamUrl,
  HttpError,
  httpClient,
  passthroughJson,
  passthroughSse,
} from "../../app/lib/http-client";

const UPSTREAM = "http://upstream.test";

const server = setupServer();

beforeAll(() => {
  process.env.LIVEOPTION_UPSTREAM_URL = UPSTREAM;
  process.env.LIVEOPTION_INTERNAL_SECRET = "test-secret";
  process.env.LIVEOPTION_ACCESS_TOKEN = "test-token-0123456789abcdef0123456789abcdef";
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

// ---------------------------------------------------------------------------
// httpClient (browser mutator)
// ---------------------------------------------------------------------------

describe("httpClient", () => {
  // ------------------------------------------------------------------
  // REGRESSION GUARD — DO NOT REMOVE
  //
  // Two load-bearing contracts live here. See
  // `docs/web/2026-04-24-orval-api-prefix-contract.md`.
  //
  // CONTRACT 1 — URL rewrite: `/v1/*` → same-origin `/api/*`
  //
  // Orval generates bare upstream paths (`/v1/runs`, `/v1/runs/:id`, ...)
  // because `openapi.json` has no `servers[]`. In production the browser
  // must hit the `/api/*` proxy routes under `app/routes/api/*.ts` so
  // Access-token auth + X-Internal-Secret + X-Actor-* are injected server-side.
  // Without the rewrite Caddy returns 404/500 and pages render errors or
  // empty state.
  //
  // CONTRACT 2 — envelope return shape `{ data, status, headers }`
  //
  // Orval's `client: "react-query"` generates types assuming the mutator
  // returns the fetch-client envelope, and pages read `res.data` +
  // `res.status`. Our first cut of this mutator returned the raw decoded
  // body, so `res.data` was `undefined` and pages either swallowed the
  // error silently (home page) or surfaced "Failed to load" (/runs page
  // using `parseContract` on `res.data`). This was the follow-up bug
  // discovered after the URL-rewrite fix shipped.
  //
  // Both contracts MUST hold together. Do not relax either test. If you
  // swap the fetcher for another library, preserve both contracts or
  // rewrite every page call-site.
  // ------------------------------------------------------------------
  it(
    "REGRESSION (Contract 1): rewrites relative /v1/* to same-origin /api/* " +
      "so generated Orval hooks hit the SSR proxy, not the upstream path",
    async () => {
      const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
        async () =>
          new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
      );

      try {
        await httpClient("/v1/runs?limit=5", { method: "GET" });
        await httpClient("/v1/checkpoints/ck_42/forks", { method: "POST" });

        const urls = fetchSpy.mock.calls.map((c) => String(c[0]));
        expect(urls[0]).toBe("/api/runs?limit=5");
        expect(urls[1]).toBe("/api/checkpoints/ck_42/forks");

        // Non-/v1 inputs (absolute URLs from tests, existing /api/* callers)
        // must pass through unchanged — no double-prefixing.
        await httpClient("/api/health", { method: "GET" });
        await httpClient("http://upstream.test/v1/runs", { method: "GET" });

        expect(fetchSpy.mock.calls[2][0]).toBe("/api/health");
        expect(fetchSpy.mock.calls[3][0]).toBe("http://upstream.test/v1/runs");
      } finally {
        fetchSpy.mockRestore();
      }
    },
  );

  it(
    "REGRESSION (Contract 2): returns the Orval fetch-client envelope " +
      "`{ data, status, headers }` so generated types and page call-sites " +
      "that read `res.data` / `res.status` work at runtime",
    async () => {
      server.use(
        http.get("http://api.test/v1/runs", () =>
          HttpResponse.json(
            { items: [{ id: "run_1" }], next_cursor: null },
            { headers: { "x-example": "yes" } },
          ),
        ),
      );

      const result = await httpClient<{
        data: { items: Array<{ id: string }>; next_cursor: string | null };
        status: number;
        headers: Headers;
      }>("http://api.test/v1/runs", { method: "GET" });

      expect(result.status).toBe(200);
      expect(result.data).toEqual({
        items: [{ id: "run_1" }],
        next_cursor: null,
      });
      expect(result.headers).toBeInstanceOf(Headers);
      // Upstream headers are preserved so callers can read metadata
      // (ETag, X-Total-Count, etc.) without a second fetch.
      expect(result.headers.get("x-example")).toBe("yes");
    },
  );

  it("forwards method and URL verbatim (Orval composes the query string)", async () => {
    let capturedUrl = "";
    server.use(
      http.get("http://api.test/v1/runs", ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ ok: true });
      }),
    );

    await httpClient(
      "http://api.test/v1/runs?status=running&limit=10",
      { method: "GET" },
    );

    const url = new URL(capturedUrl);
    expect(url.searchParams.get("status")).toBe("running");
    expect(url.searchParams.get("limit")).toBe("10");
  });

  it("POSTs bodies with content-type header supplied by Orval", async () => {
    let capturedBody = "";
    let capturedCt = "";
    server.use(
      http.post("http://api.test/v1/runs", async ({ request }) => {
        capturedBody = await request.text();
        capturedCt = request.headers.get("content-type") ?? "";
        return HttpResponse.json({ id: "run_new" }, { status: 201 });
      }),
    );

    const result = await httpClient<{
      data: { id: string };
      status: number;
      headers: Headers;
    }>("http://api.test/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: "smoke" }),
    });

    expect(capturedBody).toBe('{"scenario":"smoke"}');
    expect(capturedCt).toContain("application/json");
    expect(result.status).toBe(201);
    expect(result.data).toEqual({ id: "run_new" });
  });

  it("propagates AbortSignal from Orval's React Query integration", async () => {
    // If the signal is honored, aborting mid-request rejects the fetch.
    server.use(
      http.get("http://api.test/v1/slow", async () => {
        await new Promise((r) => setTimeout(r, 500));
        return HttpResponse.json({ ok: true });
      }),
    );

    const controller = new AbortController();
    const p = httpClient("http://api.test/v1/slow", {
      method: "GET",
      signal: controller.signal,
    });
    controller.abort();
    await expect(p).rejects.toThrow();
  });

  it("returns envelope with data=undefined for 204 No Content", async () => {
    server.use(
      http.delete("http://api.test/v1/runs/run_1", () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );

    const result = await httpClient<{
      data: undefined;
      status: number;
      headers: Headers;
    }>("http://api.test/v1/runs/run_1", { method: "DELETE" });

    expect(result.status).toBe(204);
    expect(result.data).toBeUndefined();
  });

  it("throws HttpError with parsed problem+json on non-2xx", async () => {
    const problem = {
      type: "about:blank",
      title: "Run not found",
      status: 404,
      detail: "no such run_id",
    };
    server.use(
      http.get("http://api.test/v1/runs/missing", () =>
        HttpResponse.json(problem, {
          status: 404,
          headers: { "content-type": "application/problem+json" },
        }),
      ),
    );

    const err = await httpClient(
      "http://api.test/v1/runs/missing",
      { method: "GET" },
    ).catch((e) => e);

    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(404);
    expect((err as HttpError).problem).toMatchObject({
      title: "Run not found",
      detail: "no such run_id",
    });
  });

  it("still throws HttpError when the error body is plain text", async () => {
    server.use(
      http.get("http://api.test/v1/boom", () =>
        new HttpResponse("upstream exploded", { status: 502 }),
      ),
    );

    const err = await httpClient("http://api.test/v1/boom", {
      method: "GET",
    }).catch((e) => e);

    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(502);
    expect((err as HttpError).body).toBe("upstream exploded");
    expect((err as HttpError).problem).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// getUpstreamUrl
// ---------------------------------------------------------------------------

describe("getUpstreamUrl", () => {
  it("prefers LIVEOPTION_UPSTREAM_URL", () => {
    expect(getUpstreamUrl()).toBe(UPSTREAM);
  });
});

// ---------------------------------------------------------------------------
// backendFetch (server proxy helper)
// ---------------------------------------------------------------------------

describe("backendFetch", () => {
  it("injects X-Internal-Secret + X-Actor-User", async () => {
    let headersSeen: Record<string, string> = {};
    server.use(
      http.get(`${UPSTREAM}/v1/runs`, ({ request }) => {
        headersSeen = Object.fromEntries(request.headers.entries());
        return HttpResponse.json({ items: [] });
      }),
    );

    const inbound = new Request("http://frontend.test/api/runs", {
      method: "GET",
      headers: { cookie: await sessionCookie(new Request("http://frontend.test")) },
    });
    const upstream = await backendFetch(inbound, "/v1/runs");
    expect(upstream.status).toBe(200);
    expect(headersSeen["x-internal-secret"]).toBe("test-secret");
    expect(headersSeen["x-actor-user"]).toBe("access-token-user");
    expect(headersSeen["x-actor-email"]).toBeUndefined();
  });

  it("throws a 401 problem+json Response when unauthenticated", async () => {
    const inbound = new Request("http://frontend.test/api/runs");
    const thrown = await backendFetch(inbound, "/v1/runs").catch((e) => e);

    expect(thrown).toBeInstanceOf(Response);
    expect((thrown as Response).status).toBe(401);
    expect((thrown as Response).headers.get("content-type")).toBe(
      "application/problem+json",
    );
  });

  it("supports skipAuth (no actor headers)", async () => {
    let headersSeen: Record<string, string> = {};
    server.use(
      http.get(`${UPSTREAM}/v1/health`, ({ request }) => {
        headersSeen = Object.fromEntries(request.headers.entries());
        return HttpResponse.json({ ok: true });
      }),
    );

    const inbound = new Request("http://frontend.test/api/health");
    const upstream = await backendFetch(inbound, "/v1/health", {
      skipAuth: true,
    });

    expect(upstream.status).toBe(200);
    expect(headersSeen["x-internal-secret"]).toBe("test-secret");
    expect(headersSeen["x-actor-user"]).toBeUndefined();
  });

  it("forwards POST body and method", async () => {
    let bodySeen = "";
    let methodSeen = "";
    server.use(
      http.post(`${UPSTREAM}/v1/runs`, async ({ request }) => {
        bodySeen = await request.text();
        methodSeen = request.method;
        return HttpResponse.json({ id: "run_new" }, { status: 201 });
      }),
    );

    const inbound = new Request("http://frontend.test/api/runs", {
      method: "POST",
      headers: { cookie: await sessionCookie(new Request("http://frontend.test")) },
    });
    const upstream = await backendFetch(inbound, "/v1/runs", {
      method: "POST",
      body: JSON.stringify({ scenario: "smoke" }),
      headers: { "content-type": "application/json" },
    });

    expect(upstream.status).toBe(201);
    expect(methodSeen).toBe("POST");
    expect(bodySeen).toBe('{"scenario":"smoke"}');
  });
});

// ---------------------------------------------------------------------------
// passthroughJson
// ---------------------------------------------------------------------------

describe("passthroughJson", () => {
  it("mirrors status + content-type + body verbatim (problem+json)", async () => {
    const upstream = new Response(
      JSON.stringify({ title: "conflict", status: 409 }),
      {
        status: 409,
        headers: { "content-type": "application/problem+json" },
      },
    );

    const out = await passthroughJson(upstream);
    expect(out.status).toBe(409);
    expect(out.headers.get("content-type")).toBe("application/problem+json");
    const body = await out.json();
    expect(body).toEqual({ title: "conflict", status: 409 });
  });
});

// ---------------------------------------------------------------------------
// passthroughSse — MUST stream without buffering
// ---------------------------------------------------------------------------

describe("passthroughSse", () => {
  it("forwards the upstream ReadableStream and sets SSE headers", async () => {
    // Build a ReadableStream we can push to asynchronously so we can prove
    // the proxy doesn't buffer — the consumer reads a chunk, we push the
    // next chunk, the consumer reads that too.
    const encoder = new TextEncoder();
    let pushChunk!: (s: string) => void;
    let closeStream!: () => void;
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        pushChunk = (s) => controller.enqueue(encoder.encode(s));
        closeStream = () => controller.close();
      },
    });

    const upstream = new Response(upstreamBody, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });

    const proxied = passthroughSse(upstream);

    expect(proxied.status).toBe(200);
    expect(proxied.headers.get("content-type")).toBe("text/event-stream");
    expect(proxied.headers.get("cache-control")).toContain("no-cache");
    expect(proxied.headers.get("x-accel-buffering")).toBe("no");

    const reader = proxied.body!.getReader();
    const decoder = new TextDecoder();

    pushChunk("event: tick\ndata: 1\n\n");
    const first = await reader.read();
    expect(first.done).toBe(false);
    expect(decoder.decode(first.value)).toBe("event: tick\ndata: 1\n\n");

    pushChunk("event: tick\ndata: 2\n\n");
    const second = await reader.read();
    expect(second.done).toBe(false);
    expect(decoder.decode(second.value)).toBe("event: tick\ndata: 2\n\n");

    closeStream();
    const end = await reader.read();
    expect(end.done).toBe(true);
  });
});
