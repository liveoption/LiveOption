/** API file route for /api/runs/:runId/pnl — cumulative PnL series passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs/$runId/pnl")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { runId } = params;
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/runs/${runId}/pnl${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
