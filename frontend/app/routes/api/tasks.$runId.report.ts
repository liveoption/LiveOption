import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch } from "../../lib/http-client";

export const Route = serverFileRoute("/api/tasks/$runId/report")({
  server: { handlers: { GET: async ({ request, params }) => {
    const format = new URL(request.url).searchParams.get("format") ?? "json";
    const upstream = await backendFetch(request, `/v1/tasks/${encodeURIComponent(params.runId)}/report?format=${encodeURIComponent(format)}`, { method: "GET" });
    const headers = new Headers({ "Cache-Control": "no-store" });
    for (const name of ["Content-Type", "Content-Disposition", "X-Content-Type-Options", "Content-Security-Policy"]) {
      const value = upstream.headers.get(name); if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } } },
});
