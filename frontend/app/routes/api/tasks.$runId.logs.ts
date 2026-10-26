import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks/$runId/logs")({
  server: { handlers: { GET: async ({ request, params }) => {
    const query = new URL(request.url).searchParams;
    const allowed = new URLSearchParams();
    for (const name of ["before", "limit", "download"]) { const value = query.get(name); if (value !== null) allowed.set(name, value); }
    const response = await backendFetch(request, `/v1/tasks/${encodeURIComponent(params.runId)}/logs?${allowed}`, { method: "GET" });
    const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    for (const name of ["Content-Type", "Content-Disposition"]) { const value = response.headers.get(name); if (value) headers.set(name, value); }
    return new Response(response.body, { status: response.status, headers });
  } } },
});
