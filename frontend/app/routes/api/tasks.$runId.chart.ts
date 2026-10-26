import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks/$runId/chart")({
  server: { handlers: { GET: async ({ request, params }) => {
    const url = new URL(request.url);
    const query = new URLSearchParams();
    for (const key of ["symbol", "day"]) {
      const value = url.searchParams.get(key);
      if (value) query.set(key, value);
    }
    return passthroughJson(await backendFetch(request, `/v1/tasks/${encodeURIComponent(params.runId)}/chart?${query}`, { method: "GET" }));
  } } },
});
