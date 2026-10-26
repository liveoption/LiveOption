/**
 * Node adapter for TanStack Start 1.167+ SSR output.
 *
 * TanStack Start builds to `dist/client` (static assets) + `dist/server/server.js`
 * (a Web-Fetch handler). Edge targets (Cloudflare / Vercel) auto-serve static
 * files before hitting the handler; on plain Node we must do it ourselves.
 *
 * Order of precedence per request:
 *   1. If `dist/client<url.pathname>` resolves to a regular file, stream it
 *      with the right Content-Type + long immutable cache for hashed assets.
 *   2. Otherwise forward to the SSR handler's `fetch(Request)`.
 *
 * Env:
 *   PORT (default 3000), HOST (default 127.0.0.1)
 *   All Plan C runtime env (CLERK_SECRET_KEY, LIVEOPTION_INTERNAL_SECRET, etc.)
 *   is consumed by the SSR bundle via process.env — no re-export needed here.
 */
import { createServer } from "node:http";
import { createReadStream, promises as fs } from "node:fs";
import { Readable } from "node:stream";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const CLIENT_DIR = path.join(ROOT, "dist", "client");
const SERVER_ENTRY = path.join(ROOT, "dist", "server", "server.js");

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || "127.0.0.1";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
  ".map": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml; charset=utf-8",
};

function contentTypeFor(filePath) {
  return MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

/**
 * Resolve a URL pathname to a static file under CLIENT_DIR, or null if the
 * request should fall through to SSR. Rejects traversal (../) and directories.
 */
async function resolveStaticFile(urlPath) {
  // Strip query/hash (already gone for url.pathname but be defensive) and
  // normalise. Reject anything that escapes CLIENT_DIR.
  const decoded = decodeURIComponent(urlPath);
  const abs = path.normalize(path.join(CLIENT_DIR, decoded));
  if (!abs.startsWith(CLIENT_DIR + path.sep) && abs !== CLIENT_DIR) return null;

  try {
    const stat = await fs.stat(abs);
    if (stat.isFile()) return { abs, size: stat.size };
  } catch {
    /* ENOENT → fall through to SSR */
  }
  return null;
}

function isImmutableAsset(urlPath) {
  // Vite emits hashed filenames under /assets/ — safe to cache aggressively.
  // Everything else (e.g. /favicon.ico) gets a conservative short TTL.
  return urlPath.startsWith("/assets/");
}

/** Load the SSR handler exactly once. */
const serverModule = await import(SERVER_ENTRY);
const ssrServer = serverModule.default;
if (!ssrServer?.fetch) {
  throw new Error(
    `SSR entry ${SERVER_ENTRY} did not export a default { fetch } handler.`,
  );
}

function toWebHeaders(nodeHeaders) {
  const h = new Headers();
  for (const [k, v] of Object.entries(nodeHeaders)) {
    if (Array.isArray(v)) v.forEach((vv) => h.append(k, vv));
    else if (v !== undefined) h.set(k, String(v));
  }
  return h;
}

async function handle(req, res) {
  const host = req.headers.host || `${HOST}:${PORT}`;
  const url = new URL(req.url, `http://${host}`);

  // 1) Static asset pass-through.
  if (req.method === "GET" || req.method === "HEAD") {
    const hit = await resolveStaticFile(url.pathname);
    if (hit) {
      res.statusCode = 200;
      res.setHeader("Content-Type", contentTypeFor(hit.abs));
      res.setHeader("Content-Length", String(hit.size));
      res.setHeader(
        "Cache-Control",
        isImmutableAsset(url.pathname)
          ? "public, max-age=31536000, immutable"
          : "public, max-age=300",
      );
      if (req.method === "HEAD") {
        res.end();
        return;
      }
      createReadStream(hit.abs).pipe(res);
      return;
    }
  }

  // 2) Fall through to SSR / server routes.
  const init = { method: req.method, headers: toWebHeaders(req.headers) };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = Readable.toWeb(req);
    init.duplex = "half";
  }
  const request = new Request(url.toString(), init);
  const response = await ssrServer.fetch(request);

  res.statusCode = response.status;
  response.headers.forEach((value, key) => res.setHeader(key, value));

  if (response.body) {
    const reader = response.body.getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        res.write(value);
      }
    } finally {
      reader.releaseLock?.();
    }
  }
  res.end();
}

const httpServer = createServer((req, res) => {
  handle(req, res).catch((err) => {
    console.error("adapter error", err);
    if (!res.headersSent) {
      res.statusCode = 500;
      res.setHeader("Content-Type", "text/plain; charset=utf-8");
    }
    res.end("Internal Server Error");
  });
});

httpServer.listen(PORT, HOST, () => {
  console.log(`LiveOption frontend listening on http://${HOST}:${PORT}`);
  console.log(`  static: ${CLIENT_DIR}`);
  console.log(`  ssr:    ${SERVER_ENTRY}`);
});

function shutdown(signal) {
  console.log(`received ${signal}, draining…`);
  httpServer.close((err) => {
    if (err) {
      console.error("close error", err);
      process.exit(1);
    }
    process.exit(0);
  });
  // Force-exit if close hangs (e.g. stuck SSE).
  setTimeout(() => process.exit(0), 10_000).unref();
}
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
