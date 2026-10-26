/** API file route for /api/me — proxies GET to upstream /v1/me. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/me")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const upstream = await backendFetch(request, `/v1/me`, {
          method: "GET",
        });
        return passthroughJson(upstream);
      },
    },
  },
});
