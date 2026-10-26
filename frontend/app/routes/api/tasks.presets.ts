import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughJson } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks/presets")({
  server: { handlers: { GET: async ({ request }) =>
    passthroughJson(await backendFetch(request, "/v1/tasks/presets", { method: "GET" })),
  } },
});
