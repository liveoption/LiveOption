/** API file route for /api/questions — list + create. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/questions")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/questions${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
      POST: async ({ request }) => {
        const body = await request.text();
        const upstream = await backendFetch(request, `/v1/questions`, {
          method: "POST",
          body,
          headers: { "content-type": "application/json" },
        });
        return passthroughJson(upstream);
      },
    },
  },
});
