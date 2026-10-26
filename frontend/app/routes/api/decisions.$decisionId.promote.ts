/** API file route for /api/decisions/:decisionId/promote — promote decision to checkpoint. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/decisions/$decisionId/promote")({
  server: {
    handlers: {
      POST: async ({ request, params }) => {
        const { decisionId } = params;
        const body = await request.text();
        const upstream = await backendFetch(
          request,
          `/v1/decisions/${decisionId}/promote`,
          {
            method: "POST",
            body,
            headers: { "content-type": "application/json" },
          },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
