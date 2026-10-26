/** API file route for /api/runs/:a/compare/:b — side-by-side run compare passthrough. */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs/$a/compare/$b")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { a, b } = params;
        const url = new URL(request.url);
        const upstream = await backendFetch(
          request,
          `/v1/runs/${a}/compare/${b}${url.search}`,
          { method: "GET" },
        );
        return passthroughJson(upstream);
      },
    },
  },
});
