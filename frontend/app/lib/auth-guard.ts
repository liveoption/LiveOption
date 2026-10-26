/**
 * Server-side auth guard for TanStack Start route loaders.
 *
 * Usage in a route file:
 *   import { requireAuth } from "~/lib/auth-guard";
 *   export const Route = createFileRoute("/protected")({
 *     loader: async () => { await requireAuth(); },
 *     component: ProtectedPage,
 *   });
 *
 * If the user is not authenticated, throws a 401 Response with
 * RFC 7807 problem+json body. The frontend router's errorComponent
 * maps 401 to the Unauthorized page.
 *
 * TanStack Start route loaders run on both server and client. We wrap
 * token-cookie verification in a `createServerFn` so loaders can call it
 * (runs locally during SSR, RPCs to the server during client navigation).
 *
 * E2E ESCAPE HATCH
 * ----------------
 * When `process.env.E2E_TEST_AUTH === "1"`, `requireAuth` short-circuits the
 * cookie verification and returns a synthetic `{ userId: "user_e2e" }`.
 *
 * SAFETY GUARD: At module load we refuse to start in a production NODE_ENV
 * unless `E2E_PLAYWRIGHT=1` is also set.
 */

import { createServerFn } from "@tanstack/react-start";
import { redirect } from "@tanstack/react-router";

if (
  process.env.E2E_TEST_AUTH === "1" &&
  process.env.NODE_ENV === "production" &&
  process.env.E2E_PLAYWRIGHT !== "1"
) {
  throw new Error(
    "E2E_TEST_AUTH=1 is set in production without E2E_PLAYWRIGHT=1 — refusing to start.",
  );
}

const checkAuthServer = createServerFn({ method: "GET" }).handler(
  async (): Promise<{ userId: string | null }> => {
    if (process.env.E2E_TEST_AUTH === "1") {
      return { userId: "user_e2e" };
    }
    const { isAuthenticated } = await import("./server-token-auth");
    const { getRequest } = await import("@tanstack/react-start/server");
    const request = getRequest();
    if (!request) return { userId: null };
    try {
      return { userId: await isAuthenticated(request) ? "access-token-user" : null };
    } catch {
      return { userId: null };
    }
  },
);

export async function requireAuth(
  _ctx?: unknown,
): Promise<{ userId: string }> {
  const { userId } = await checkAuthServer();
  if (!userId) {
    throw redirect({ to: "/sign-in" });
  }
  return { userId };
}
