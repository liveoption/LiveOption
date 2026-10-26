/** API file route for /api/runs/:runId/checkpoints — list checkpoints for a run. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs/$runId/checkpoints")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { runId } = params;
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/runs/${runId}/checkpoints${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
