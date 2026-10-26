/** API file route for /api/checkpoints/:checkpointId — fetch a single checkpoint. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/checkpoints/$checkpointId")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { checkpointId } = params;
        const upstream = await backendFetch(
          request,
          `/v1/checkpoints/${checkpointId}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
