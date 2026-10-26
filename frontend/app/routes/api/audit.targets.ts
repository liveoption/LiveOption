/** API file route for /api/audit/targets — audit list passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/audit/targets")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/audit/targets${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
