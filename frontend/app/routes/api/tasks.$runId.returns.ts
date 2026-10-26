import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks/$runId/returns")({
  server: { handlers: { GET: async ({ request, params }) => {
    const query = new URL(request.url).searchParams;
    const allowed = new URLSearchParams();
    for (const name of ["symbols", "account"]) { const value = query.get(name); if (value !== null) allowed.set(name, value); }
    return passthroughJson(await backendFetch(request, `/v1/tasks/${encodeURIComponent(params.runId)}/returns?${allowed}`, { method: "GET" }));
  } } },
});
