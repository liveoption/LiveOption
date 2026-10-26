/** API file route for /api/checkpoints/:checkpointId/questions — list + create. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute(
  "/api/checkpoints/$checkpointId/questions",
)({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { checkpointId } = params;
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/checkpoints/${checkpointId}/questions${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
      POST: async ({ request, params }) => {
        const { checkpointId } = params;
        const body = await request.text();
        const upstream = await backendFetch(
          request,
          `/v1/checkpoints/${checkpointId}/questions`,
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
