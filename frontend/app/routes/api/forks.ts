/** API file route for /api/forks — list + create fork. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/forks")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const upstream = await backendFetch(request, `/v1/forks${url.search}`, {
          method: "GET",
        });
        return passthroughJson(upstream);
      },
      POST: async ({ request }) => {
        const body = await request.text();
        const upstream = await backendFetch(request, `/v1/forks`, {
          method: "POST",
          body,
          headers: { "content-type": "application/json" },
        });
        return passthroughJson(upstream);
      },
    },
  },
});
