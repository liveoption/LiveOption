/** API file route for /api/checkpoints/:checkpointId/forks — fork create passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/checkpoints/$checkpointId/forks")({
  server: {
    handlers: {
      POST: async ({ request, params }) => {
        const { checkpointId } = params;
        const body = await request.text();
        const upstream = await backendFetch(
          request,
          `/v1/checkpoints/${checkpointId}/forks`,
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
