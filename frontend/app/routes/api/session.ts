import { serverFileRoute } from "../../lib/createServerRoute";
import {
  clearSessionCookie,
  isAuthenticated,
  sessionCookie,
  validAccessToken,
} from "../../lib/server-token-auth";

const headers = { "cache-control": "no-store", "content-type": "application/json" };

export const Route = serverFileRoute("/api/session")({
  server: {
    handlers: {
      GET: async ({ request }) =>
        new Response(JSON.stringify({ authenticated: await isAuthenticated(request) }), { headers }),
      POST: async ({ request }) => {
        let token = "";
        try {
          if (Number(request.headers.get("content-length") ?? 0) > 4096) throw new Error("large body");
          const body = await request.json();
          token = typeof body.token === "string" ? body.token : "";
        } catch {
          return new Response(JSON.stringify({ error: "Invalid request" }), { status: 400, headers });
        }
        if (!await validAccessToken(token)) {
          return new Response(JSON.stringify({ error: "Invalid access token" }), { status: 401, headers });
        }
        return new Response(JSON.stringify({ authenticated: true }), {
          headers: { ...headers, "set-cookie": await sessionCookie(request) },
        });
      },
      DELETE: async ({ request }) =>
        new Response(JSON.stringify({ authenticated: false }), {
          headers: { ...headers, "set-cookie": clearSessionCookie(request) },
        }),
    },
  },
});
