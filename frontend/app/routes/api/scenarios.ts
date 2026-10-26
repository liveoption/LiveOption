/** API file route for /api/scenarios — proxies GET to upstream /v1/scenarios. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/scenarios")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/scenarios${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
