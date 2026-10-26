const COOKIE_NAME = "lo_session";

async function digest(value: string): Promise<Uint8Array> {
  const bytes = new TextEncoder().encode(value);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
}

function configuredToken(): string {
  return process.env.LIVEOPTION_ACCESS_TOKEN?.trim() ?? "";
}

async function sessionValue(token: string): Promise<string> {
  return Array.from(await digest(`liveoption-web-session-v1\0${token}`))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function equal(left: string, right: string): Promise<boolean> {
  const a = await digest(left);
  const b = await digest(right);
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}

export async function validAccessToken(candidate: string): Promise<boolean> {
  const expected = configuredToken();
  return expected.length >= 32 && await equal(candidate, expected);
}

export async function isAuthenticated(request: Request): Promise<boolean> {
  const expected = configuredToken();
  if (expected.length < 32) return false;
  const authorization = request.headers.get("authorization") ?? "";
  if (authorization.startsWith("Bearer ")) {
    return validAccessToken(authorization.slice(7).trim());
  }
  const cookie = request.headers.get("cookie") ?? "";
  const value = cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${COOKIE_NAME}=`))
    ?.slice(COOKIE_NAME.length + 1);
  return Boolean(value && await equal(value, await sessionValue(expected)));
}

function cookieAttributes(request: Request): string {
  const secure =
    request.headers.get("x-forwarded-proto") === "https" ||
    new URL(request.url).protocol === "https:";
  return `Path=/; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`;
}

export async function sessionCookie(request: Request): Promise<string> {
  return `${COOKIE_NAME}=${await sessionValue(configuredToken())}; ${cookieAttributes(request)}; Max-Age=604800`;
}

export function clearSessionCookie(request: Request): string {
  return `${COOKIE_NAME}=; ${cookieAttributes(request)}; Max-Age=0`;
}
