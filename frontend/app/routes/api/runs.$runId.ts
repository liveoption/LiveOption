/** API file route for /api/runs/:runId — detail passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs/$runId")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { runId } = params;
        const upstream = await backendFetch(request, `/v1/runs/${runId}`, {
          method: "GET",
        });
        return passthroughJson(upstream);
      },
    },
  },
});
