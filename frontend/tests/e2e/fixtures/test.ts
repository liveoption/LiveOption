/**
 * Extended Playwright `test` fixture that installs hermetic FastAPI stubs
 * via `Routing` on every test by default. Specs can still add overrides per
 * test via `routing.use(handler)`.
 *
 * Import from this module instead of `@playwright/test` inside specs:
 *
 *     import { test, expect } from "./fixtures/test";
 */
import { test as base, expect } from "@playwright/test";
import { Routing } from "./routing";
import { maybeSignIn, installClerkStubs, applySignedInSession } from "./auth";

export { expect };

export const test = base.extend<{
  routing: Routing;
  signedIn: boolean;
}>({
  routing: [
    async ({ context }, use) => {
      const r = new Routing();
      await r.install(context);
      await use(r);
    },
    { auto: true },
  ],
  signedIn: async ({ context, baseURL }, use) => {
    // When the server-side auth escape hatch is active, default to signed-in
    // so specs gated on `<SignedIn>` UI render without needing a real Clerk
    // dev session token. The server already returns the synthetic user_e2e.
    if (process.env.E2E_TEST_AUTH === "1") {
      await installClerkStubs(context, { signedIn: true });
      await applySignedInSession(
        context,
        baseURL ?? "http://127.0.0.1:3000",
      );
      await use(true);
      return;
    }
    const ok = await maybeSignIn(context, baseURL ?? "http://127.0.0.1:3000");
    await use(ok);
  },
});
