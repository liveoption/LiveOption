import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  clearSessionCookie,
  isAuthenticated,
  sessionCookie,
  validAccessToken,
} from "../../app/lib/server-token-auth";

const token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

beforeEach(() => {
  process.env.LIVEOPTION_ACCESS_TOKEN = token;
});

afterEach(() => {
  delete process.env.LIVEOPTION_ACCESS_TOKEN;
});

describe("access-token authentication", () => {
  it("rejects wrong tokens and refuses an unset or short configured token", async () => {
    expect(await validAccessToken(token)).toBe(true);
    expect(await validAccessToken("wrong")).toBe(false);
    process.env.LIVEOPTION_ACCESS_TOKEN = "short";
    expect(await validAccessToken("short")).toBe(false);
    delete process.env.LIVEOPTION_ACCESS_TOKEN;
    expect(await validAccessToken(token)).toBe(false);
  });

  it("accepts only a matching HTTP-only session cookie", async () => {
    const request = new Request("https://liveoption.example/");
    const cookie = await sessionCookie(request);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).toContain("Secure");
    expect(cookie).not.toContain(token);
    expect(await isAuthenticated(new Request(request.url, { headers: { cookie } }))).toBe(true);
    expect(await isAuthenticated(new Request(request.url, { headers: { cookie: "lo_session=wrong" } }))).toBe(false);
    expect(clearSessionCookie(request)).toContain("Max-Age=0");
  });

  it("accepts the same access token as a bearer credential for the CLI", async () => {
    expect(await isAuthenticated(new Request("https://liveoption.example/api/tasks", {
      headers: { authorization: `Bearer ${token}` },
    }))).toBe(true);
    expect(await isAuthenticated(new Request("https://liveoption.example/api/tasks", {
      headers: { authorization: "Bearer wrong" },
    }))).toBe(false);
  });
});
