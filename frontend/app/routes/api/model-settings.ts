import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/model-settings")({
  server: { handlers: {
    GET: async ({ request }) => passthroughJson(await backendFetch(request, "/v1/model-settings", { method: "GET" })),
    PUT: async ({ request }) => passthroughJson(await backendFetch(request, "/v1/model-settings", {
      method: "PUT", body: await request.text(), headers: { "content-type": "application/json" },
    })),
  } },
});
