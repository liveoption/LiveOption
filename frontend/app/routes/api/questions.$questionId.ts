/** API file route for /api/questions/:questionId — single question. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/questions/$questionId")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { questionId } = params;
        const upstream = await backendFetch(
          request,
          `/v1/questions/${questionId}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
