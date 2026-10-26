/**
 * HTTP client for LiveOption frontend.
 *
 * Two call paths:
 *
 *   1. `httpClient` — Orval mutator used by generated React Query hooks.
 *      Runs in the BROWSER. Calls the same-origin `/api/*` proxy routes
 *      that forward to the FastAPI Job Server. The browser never sees the
 *      internal secret or access token — the server-side proxy
 *      injects them (see `backendFetch`).
 *
 *      Orval's default `react-query` client invokes the custom mutator as
 *      `httpClient(url, options)` where `options` extends `RequestInit`
 *      (method, body, headers, signal). We match that signature exactly so
 *      the generated call-sites need no post-processing.
 *
 *   2. `backendFetch` — used inside TanStack Start API-file routes under
 *      `app/api/*.ts`. Runs on the SERVER. Resolves the upstream URL from
 *      env (`LIVEOPTION_UPSTREAM_URL` / `LIVEOPTION_API_URL`), injects
 *      `X-Internal-Secret` from `LIVEOPTION_INTERNAL_SECRET`, and derives
 *      `X-Actor-User` from the access-token session tied to the request.
 *
 * RFC 7807 `application/problem+json` responses are propagated status + body
 * verbatim so React Query surfaces them through the client. For non-2xx
 * `httpClient` throws an `HttpError` that carries the parsed problem details
 * when available.
 */

export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  [key: string]: unknown;
}

export class HttpError extends Error {
  readonly status: number;
  readonly problem?: ProblemDetails;
  readonly body: string;

  constructor(status: number, body: string, problem?: ProblemDetails) {
    const title = problem?.title ?? problem?.detail ?? `HTTP ${status}`;
    super(`HTTP ${status}: ${title}`);
    this.name = "HttpError";
    this.status = status;
    this.problem = problem;
    this.body = body;
  }
}

/**
 * Orval mutator. Runs in the browser and calls same-origin API proxy routes.
 *
 * Signature matches Orval's generated fetch-client call shape:
 *   `httpClient<T>(url, { method, headers, body, signal, ... })`
 *
 * On non-2xx the response body is surfaced via `HttpError`; when the
 * `content-type` is `application/problem+json` the body is parsed into
 * `HttpError.problem`.
 */
export async function httpClient<T = unknown>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  // Contract 1 — URL: Orval emits `/v1/*` (openapi.json has no
  // `servers[]`). The browser must hit same-origin `/api/*` proxy
  // routes so access-token auth + X-Internal-Secret + X-Actor-* are injected
  // server-side. Rewrite relative `/v1/*` → `/api/*`; pass everything
  // else untouched.
  //
  // Contract 2 — return shape: Orval's generated types + page call
  // sites (home, /runs, /checkpoints, etc.) read `res.data` and
  // `res.status`, matching the Orval fetch-client envelope. Return
  // `{ data, status, headers }` on 2xx; still throw `HttpError` on
  // non-2xx so React Query's error branch fires uniformly.
  //
  // Both contracts are pinned by tests/unit/http-client.test.ts. Doc:
  // docs/web/2026-04-24-orval-api-prefix-contract.md.
  const target = url.startsWith("/v1/") ? "/api" + url.slice(3) : url;
  const res = await fetch(target, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers ?? {}),
    },
  });

  const body = await res.text();

  if (!res.ok) {
    const contentType = res.headers.get("content-type") ?? "";
    let problem: ProblemDetails | undefined;
    if (contentType.includes("application/problem+json") && body) {
      try {
        problem = JSON.parse(body) as ProblemDetails;
      } catch {
        problem = undefined;
      }
    }
    throw new HttpError(res.status, body, problem);
  }

  const contentType = res.headers.get("content-type") ?? "";
  let data: unknown = undefined;
  if (body) {
    data = contentType.includes("application/json") ? JSON.parse(body) : body;
  }

  return {
    data,
    status: res.status,
    headers: res.headers,
  } as T;
}

export default httpClient;

// ---------------------------------------------------------------------------
// Server-side proxy helper
// ---------------------------------------------------------------------------

/** Resolve the upstream FastAPI Job Server base URL (server-only). */
export function getUpstreamUrl(): string {
  return (
    process.env.LIVEOPTION_UPSTREAM_URL ||
    process.env.LIVEOPTION_API_URL ||
    "http://127.0.0.1:19720"
  );
}

export interface BackendFetchOptions extends Omit<RequestInit, "headers"> {
  headers?: HeadersInit;
  /** Skip access-token auth resolution (e.g. for health checks). */
  skipAuth?: boolean;
}

/**
 * Server-side fetch helper used by `app/api/*.ts` proxy routes.
 *
 * - Checks the access-token session from the inbound `Request` (unless
 *   `skipAuth` is set) and throws a 401 Response if unauthenticated.
 * - Injects `X-Internal-Secret` from `LIVEOPTION_INTERNAL_SECRET`.
 * - Adds the fixed `X-Actor-User` identity for the token holder.
 * - Forwards body + method verbatim. Does NOT buffer response — caller is
 *   free to stream `upstream.body` back to the client (required for SSE).
 */
export async function backendFetch(
  request: Request,
  path: string,
  init: BackendFetchOptions = {},
): Promise<Response> {
  const headers = new Headers(init.headers);

  const secret = process.env.LIVEOPTION_INTERNAL_SECRET ?? "";
  if (secret) headers.set("X-Internal-Secret", secret);

  if (!init.skipAuth) {
    // Lazy import keeps server-side token verification out of browser calls.
    const { isAuthenticated } = await import("./server-token-auth");
    if (!await isAuthenticated(request)) {
      throw new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
        }),
        {
          status: 401,
          headers: { "content-type": "application/problem+json" },
        },
      );
    }
    headers.set("X-Actor-User", "access-token-user");
  }

  const { skipAuth: _ignored, ...fetchInit } = init;

  const upstream = await fetch(`${getUpstreamUrl()}${path}`, {
    ...fetchInit,
    headers,
  });
  return upstream;
}

/**
 * Helper: produce a Response that mirrors an upstream JSON/problem response
 * verbatim (status + content-type + body). Used by non-SSE proxy routes.
 */
export async function passthroughJson(upstream: Response): Promise<Response> {
  const body = await upstream.text();
  const contentType =
    upstream.headers.get("content-type") ?? "application/json";
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": contentType },
  });
}

/**
 * Helper: produce a streaming SSE Response that pipes `upstream.body`
 * through without buffering. Required for `/v1/runs/:id/events` and
 * `/v1/questions/:id/stream`.
 */
export function passthroughSse(upstream: Response): Response {
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
      Connection: "keep-alive",
    },
  });
}
