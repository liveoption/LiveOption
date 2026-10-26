import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/model-settings/$groupId/models/refresh")({
  server: { handlers: { POST: async ({ request, params }) =>
    passthroughJson(await backendFetch(request, `/v1/model-settings/${encodeURIComponent(params.groupId)}/models/refresh`, { method: "POST" })),
  } },
});
