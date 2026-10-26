/**
 * /home — unauthenticated CTA + (optionally) signed-in hero.
 *
 * Covers design-spec golden path #1: first visit shows the sign-in CTA; once
 * signed in, the Hero + Quick Links + Recent runs preview render.
 */
import { test, expect } from "./fixtures/test";

test.describe("home page", () => {
  test("signed-out visitor sees the sign-in CTA", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /Options trading, on your terms/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Sign in to continue/i }),
    ).toBeVisible();
    await expect(page.getByRole("region", { name: /Sign in to continue/i }).getByRole("button", { name: /^Sign in$/i })).toBeVisible();
  });

  test("signed-in user sees Quick links + Recent runs", async ({
    page,
    routing,
  }) => {
    routing.use({ method: "GET", path: "/v1/session", body: { authenticated: true } });
    routing.use({ method: "GET", path: "/v1/me", body: { can_launch: true } });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /Start here/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Recent runs/i }),
    ).toBeVisible();
    // First fixture run should be linked.
    await expect(page.getByText("Alpha run")).toBeVisible();
  });
});
