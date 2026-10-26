/**
 * Thin typed wrapper over `createFileRoute` for server-only API routes.
 *
 * Rationale: the checked-in `routeTree.gen.ts` stub is narrower than the
 * file `@tanstack/router-plugin` generates at dev/build time, and does not
 * augment `FileRoutesByPath` with the `server.handlers` slot. Under a plain
 * `tsc --noEmit` (no Vite), `createFileRoute("/api/...")({ server: {...} })`
 * therefore errors with "Object literal may only specify known properties,
 * and 'server' does not exist in type …".
 *
 * At Vite dev/build time the plugin regenerates `routeTree.gen.ts` with the
 * full server-handler typing; this wrapper simply relaxes the options arg
 * to `any` so the CI `tsc --noEmit` pass is satisfied in the meantime.
 * Handler bodies remain fully typed on the `Request` side (native Fetch).
 */
import { createFileRoute } from "@tanstack/react-router";

export type ServerHandler = (ctx: {
  request: Request;
  params: Record<string, string>;
}) => Response | Promise<Response>;

export interface ServerRouteOptions {
  server: {
    handlers: Partial<{
      GET: ServerHandler;
      POST: ServerHandler;
      PUT: ServerHandler;
      PATCH: ServerHandler;
      DELETE: ServerHandler;
      OPTIONS: ServerHandler;
      HEAD: ServerHandler;
    }>;
  };
}

export function serverFileRoute(path: string) {
  // TODO: Remove the `as any` cast once TanStack Start's generated routeTree.gen.ts
  // includes the `server.handlers` slot in `FileRoutesByPath`. That will make the
  // `as unknown as (…)` cast unnecessary too. Trigger: TanStack Start 1.170+ with
  // `routeTree.gen.ts` that has `server: { handlers: … }` on file routes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const factory = createFileRoute(path as any) as unknown as (
    opts: ServerRouteOptions,
  ) => unknown;
  return factory;
}
