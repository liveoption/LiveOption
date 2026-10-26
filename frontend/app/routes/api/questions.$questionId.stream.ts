/**
 * API file route for /api/questions/:questionId/stream — SSE passthrough.
 *
 * Accepts `Last-Event-ID` from either the HTTP header or the `last_event_id`
 * query param (browser `EventSource` reconnect fallback) and forwards it as
 * a header upstream. MUST NOT buffer — `passthroughSse` forwards the raw
 * `ReadableStream` verbatim.
 */
import { serverFileRoute } from "../../lib/createServerRoute";
import { backendFetch, passthroughSse } from "../../lib/http-client";

export const Route = serverFileRoute("/api/questions/$questionId/stream")({
  server: {
    handlers: {
      GET: async ({ request, params }) => {
        const { questionId } = params;
        const url = new URL(request.url);
        const lastEventId =
          request.headers.get("Last-Event-ID") ??
          url.searchParams.get("last_event_id") ??
          "0";
        const upstream = await backendFetch(
          request,
          `/v1/questions/${questionId}/stream`,
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
