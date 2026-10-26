/** API file route for /api/decisions/:decisionId — single decision. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/decisions/$decisionId")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { decisionId } = params;
        const upstream = await backendFetch(
          request,
          `/v1/decisions/${decisionId}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
