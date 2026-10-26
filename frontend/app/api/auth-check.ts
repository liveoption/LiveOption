/**
 * SSR-safe auth check helper.
 *
 * Returns `{ userId }` if the access-token session on the current request
 * is valid, or `{ userId: null }` otherwise. Intended to be called from
 * server contexts (route `beforeLoad` hooks during SSR, or from server route
 * handlers in `app/routes/api/*.ts`) where `@tanstack/react-start/server`'s
 * `getRequest()` returns the active `Request` object.
 *
 * Uses Start's server-side `getRequest` helper.
 */
import { isAuthenticated } from "../lib/server-token-auth";
import { getRequest } from "@tanstack/react-start/server";

export interface AuthCheckResult {
  userId: string | null;
}

export async function authCheck(): Promise<AuthCheckResult> {
  const request = getRequest();
  if (!request) {
    return { userId: null };
  }
  try {
    return { userId: await isAuthenticated(request) ? "access-token-user" : null };
  } catch {
    return { userId: null };
  }
}
