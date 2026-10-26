/**
 * API file route for /api/runs/:runId/events — SSE passthrough (stream).
 *
 * The browser `EventSource` cannot set custom headers, so clients forward
 * `Last-Event-ID` via the `last_event_id` query parameter. This proxy reads
 * either source and promotes it to the `Last-Event-ID` header on the
 * upstream request for FastAPI's resumable-SSE handler. MUST NOT buffer —
 * `passthroughSse` forwards the raw `ReadableStream` verbatim.
 */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughSse } from "../../lib/http-client";

export const Route = serverFileRoute("/api/runs/$runId/events")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { runId } = params;
        const url = new URL(request.url);
        const lastEventId =
          request.headers.get("Last-Event-ID") ??
          url.searchParams.get("last_event_id") ??
          "0";
        const upstream = await backendFetch(
          request,
          `/v1/runs/${runId}/events`,
          {
            method: "GET",
            headers: {
              "Last-Event-ID": lastEventId,
              Accept: "text/event-stream",
            },
          },
        );
        return passthroughSse(upstream);
      },
    },
  },
});
