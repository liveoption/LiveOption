/** API file route for /api/forks/:forkId — fork metadata / lineage. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/forks/$forkId")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { forkId } = params;
        const upstream = await backendFetch(request, `/v1/forks/${forkId}`, {
          method: "GET",
        });
        return passthroughJson(upstream);
      },
    },
  },
});
