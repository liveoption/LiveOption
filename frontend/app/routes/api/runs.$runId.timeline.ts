/** API file route for /api/runs/:runId/timeline — decision timeline passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs/$runId/timeline")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { runId } = params;
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/runs/${runId}/timeline${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
