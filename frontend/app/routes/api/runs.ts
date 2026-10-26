/**
 * API file route for /api/runs — list + create.
 *
 * Migrated from `@tanstack/start/api` `createAPIFileRoute` to Start 1.167+:
 * a normal file route with a `server.handlers` block (wired through our
 * `serverFileRoute` tsc-stub shim — the Vite plugin regenerates full typing
 * at dev/build time). Still returns raw `Response` objects.
 */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const upstream = await backendFetch(request, `/v1/runs${url.search}`, {
          method: "GET",
        });
        return passthroughJson(upstream);
      },
      POST: async ({ request }) => {
        const body = await request.text();
        const upstream = await backendFetch(request, `/v1/runs`, {
          method: "POST",
          body,
          headers: { "content-type": "application/json" },
        });
        return passthroughJson(upstream);
      },
    },
  },
});
