/**
 * Clerk auth fixture for E2E — avoids hitting production Clerk.
 *
 * Strategy: the app gates signed-in UI on Clerk's `<SignedIn>` component,
 * which flips true once `clerk.loaded && clerk.user` is truthy. We cannot
 * reliably mint a real Clerk JWT without network access, so tests operate in
 * one of two modes:
 *
 *   1. **Signed-out** (default): navigate as an anonymous visitor and assert
 *      the `<SignedOut>` UI. Works without any Clerk setup.
 *   2. **Signed-in** (env-gated): set `E2E_CLERK_SESSION` to a dev JWT and
 *      the harness installs it as the `__session` cookie. If the var is
 *      absent, specs that require signed-in state call `test.skip()`.
 *
 * Hermeticity (Task 15 follow-up): even in signed-in mode we install
 * Playwright route stubs that block outbound calls to Clerk hosts
 * (`clerk.com`, `clerk.dev`, `clerk.accounts.dev`, `*.clerk.com`) and return
 * deterministic fixtures for `/v1/client`, `/v1/environment`, and the JWKS
 * endpoint. This prevents specs from depending on a live Clerk project being
 * reachable during CI. `@clerk/testing` is the preferred long-term path, but
 * is not yet installed in this workspace; the route stubs give the same
 * hermeticity without a new devDependency.
 */
import type { BrowserContext, Page } from "@playwright/test";

export function hasClerkSession(): boolean {
  return !!process.env.E2E_CLERK_SESSION;
}

/** Hosts we treat as Clerk traffic and block by default. */
const CLERK_HOST_PATTERNS = [
  /(^|\.)clerk\.com$/i,
  /(^|\.)clerk\.dev$/i,
  /(^|\.)clerk\.accounts\.dev$/i,
  /(^|\.)clerk\.services$/i,
];

function isClerkHost(hostname: string): boolean {
  return CLERK_HOST_PATTERNS.some((re) => re.test(hostname));
}

/**
 * Install Playwright route handlers that fulfil Clerk network traffic with
 * deterministic fixtures. Call this BEFORE `context.addCookies` / `visit()`.
 *
 * Signed-out mode: returns an empty client/environment so Clerk loads as
 * signed-out. Signed-in mode: returns a synthetic user/session so
 * `<SignedIn>` flips true without ever hitting the real service.
 */
export async function installClerkStubs(
  context: BrowserContext,
  opts: { signedIn: boolean } = { signedIn: false },
): Promise<void> {
  // Deterministic JWKS — Clerk's frontend fetches this to verify tokens.
  // An empty keys array is sufficient because we short-circuit the session
  // lookup below; the frontend never performs a real signature check.
  const jwks = { keys: [] as unknown[] };
  const now = Math.floor(Date.now() / 1000);
  const baseClient = {
    id: "client_e2e",
    object: "client",
    sessions: [] as unknown[],
    sign_in: null,
    sign_up: null,
    last_active_session_id: null,
    created_at: now,
    updated_at: now,
  };
  const signedInClient = {
    ...baseClient,
    last_active_session_id: "sess_e2e",
    sessions: [
      {
        id: "sess_e2e",
        object: "session",
        status: "active",
        last_active_at: now,
        expire_at: now + 3600,
        user: {
          id: "user_e2e",
          object: "user",
          first_name: "E2E",
          last_name: "Tester",
          image_url: "",
          primary_email_address_id: "idn_e2e",
          email_addresses: [
            {
              id: "idn_e2e",
              object: "email_address",
              email_address: "e2e@test.local",
              verification: { status: "verified" },
            },
          ],
        },
      },
    ],
  };
  const environment = {
    auth_config: { object: "auth_config", single_session_mode: true },
    display_config: {
      object: "display_config",
      application_name: "LiveOption E2E",
      home_url: "/",
    },
    user_settings: { object: "user_settings" },
  };

  await context.route(/https?:\/\/[^/]*clerk\.[^/]+\/.*/i, async (route) => {
    const url = route.request().url();
    let body: unknown = {};
    if (/\/\.well-known\/jwks(\.json)?$/i.test(url) || /\/v1\/jwks/i.test(url)) {
      body = jwks;
    } else if (/\/v1\/client/i.test(url)) {
      body = opts.signedIn ? signedInClient : baseClient;
    } else if (/\/v1\/environment/i.test(url)) {
      body = environment;
    } else if (/\/v1\/dev_browser/i.test(url)) {
      body = { id: "dvb_e2e", token: "dvb_token_e2e" };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify(body),
    });
  });

  // Belt-and-suspenders: abort anything else that still tries to leave to
  // Clerk so a missed endpoint surfaces as a clean test failure instead of a
  // long network timeout.
  await context.route("**/*", async (route) => {
    try {
      const host = new URL(route.request().url()).hostname;
      if (isClerkHost(host)) {
        await route.abort("failed");
        return;
      }
    } catch {
      /* non-URL request, let it through */
    }
    await route.fallback();
  });
}

export async function applySignedInSession(
  context: BrowserContext,
  baseURL: string,
): Promise<void> {
  const token = process.env.E2E_CLERK_SESSION ?? "e2e_synthetic_session";
  const url = new URL(baseURL);
  await context.addCookies([
    {
      name: "__session",
      value: token,
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure: url.protocol === "https:",
      sameSite: "Lax",
    },
  ]);
}

/**
 * Opportunistic sign-in helper. If a dev session token is present, applies
 * it; otherwise leaves the context anonymous. Returns whether the context is
 * signed-in. In both modes this installs the Clerk route stubs so no test
 * ever hits a real Clerk host.
 */
export async function maybeSignIn(
  context: BrowserContext,
  baseURL: string,
): Promise<boolean> {
  const signedIn = hasClerkSession();
  await installClerkStubs(context, { signedIn });
  if (!signedIn) return false;
  await applySignedInSession(context, baseURL);
  return true;
}

/** Convenience: goto + wait for hydration. */
export async function visit(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: "domcontentloaded" });
}
