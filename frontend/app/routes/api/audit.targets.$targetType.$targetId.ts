/** API file route for /api/audit/targets/:targetType/:targetId — audit detail passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute(
  "/api/audit/targets/$targetType/$targetId",
)({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { targetType, targetId } = params;
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/audit/targets/${targetType}/${targetId}${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
