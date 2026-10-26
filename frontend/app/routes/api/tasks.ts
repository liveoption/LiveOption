import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks")({
  server: { handlers: {
    POST: async ({ request }) => passthroughJson(await backendFetch(request, "/v1/tasks", {
      method: "POST", body: await request.text(), headers: { "content-type": "application/json" },
    })),
  } },
});
