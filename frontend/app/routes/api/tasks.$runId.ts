import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks/$runId")({
  server: { handlers: { GET: async ({ request, params }) =>
    passthroughJson(await backendFetch(request, `/v1/tasks/${encodeURIComponent(params.runId)}`, { method: "GET" })),
  } },
});
