/// <reference types="vitest" />
import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsConfigPaths from "vite-tsconfig-paths";

/**
 * Vite config for TanStack Start 1.167+ (Vite-native; vinxi deprecated).
 *
 * The `tanstackStart` plugin owns dev/build/SSR orchestration and registers
 * the router plugin for file-based routing under `app/routes`. `tailwindcss`
 * is the Tailwind v4 Vite plugin (config moved to CSS, see
 * `app/styles/app.css`). `viteReact` provides React Fast Refresh.
 *
 * `test` block is read by Vitest directly — we keep the `~/` alias working
 * via `vite-tsconfig-paths` so unit tests resolve the same way as the app.
 */
/**
 * Vite plugin: replace `use-sync-external-store/shim*` with ESM re-exports
 * from React 19, which ships useSyncExternalStore natively.
 *
 * The shim uses `module.exports` (CJS) but is imported via ESM named import
 * in @tanstack/react-store's ESM bundle. Vite dev mode serves the nested dep
 * as-is without pre-bundling, so the browser gets a CJS file without ESM
 * named exports. React 19 ships these hooks natively, so we can just
 * re-export from "react" directly.
 *
 * Upstream: https://github.com/TanStack/query/issues/7964 — remove this plugin
 * once @tanstack/react-store v5+ ships a React 19-compatible ESM bundle.
 */
function fixUseSyncExternalStore() {
  return {
    name: "fix-use-sync-external-store",
    transform(code: string, id: string) {
      if (!id.includes("use-sync-external-store")) return null;
      if (!id.includes("/shim/")) return null;
      // Only transform if it's still CJS (has module.exports but no export keyword)
      if (code.includes("export ") || !code.includes("module.exports")) return null;

      if (id.includes("with-selector")) {
        // The with-selector shim builds useSyncExternalStoreWithSelector on top
        // of useSyncExternalStore — that fn DOES exist on React 19, so we
        // implement the with-selector variant inline instead of re-exporting
        // from React (React itself does not ship useSyncExternalStoreWithSelector).
        return {
          code: `import { useSyncExternalStore, useRef, useEffect, useMemo, useDebugValue } from "react";
export function useSyncExternalStoreWithSelector(subscribe, getSnapshot, getServerSnapshot, selector, isEqual) {
  const instRef = useRef(null);
  let inst;
  if (instRef.current === null) {
    inst = { hasValue: false, value: null };
    instRef.current = inst;
  } else {
    inst = instRef.current;
  }
  const [getSelection, getServerSelection] = useMemo(() => {
    let hasMemo = false;
    let memoizedSnapshot;
    let memoizedSelection;
    const memoizedSelector = (nextSnapshot) => {
      if (!hasMemo) {
        hasMemo = true;
        memoizedSnapshot = nextSnapshot;
        const nextSelection = selector(nextSnapshot);
        if (isEqual !== undefined) {
          if (inst.hasValue) {
            const currentSelection = inst.value;
            if (isEqual(currentSelection, nextSelection)) {
              memoizedSelection = currentSelection;
              return currentSelection;
            }
          }
        }
        memoizedSelection = nextSelection;
        return nextSelection;
      }
      const prevSnapshot = memoizedSnapshot;
      const prevSelection = memoizedSelection;
      if (Object.is(prevSnapshot, nextSnapshot)) return prevSelection;
      const nextSelection = selector(nextSnapshot);
      if (isEqual !== undefined && isEqual(prevSelection, nextSelection)) return prevSelection;
      memoizedSnapshot = nextSnapshot;
      memoizedSelection = nextSelection;
      return nextSelection;
    };
    const maybeGetServerSnapshot = getServerSnapshot === undefined ? null : getServerSnapshot;
    const getSnapshotWithSelector = () => memoizedSelector(getSnapshot());
    const getServerSnapshotWithSelector = maybeGetServerSnapshot === null ? undefined : () => memoizedSelector(maybeGetServerSnapshot());
    return [getSnapshotWithSelector, getServerSnapshotWithSelector];
  }, [getSnapshot, getServerSnapshot, selector, isEqual]);
  const value = useSyncExternalStore(subscribe, getSelection, getServerSelection);
  useEffect(() => {
    inst.hasValue = true;
    inst.value = value;
  }, [value]);
  useDebugValue(value);
  return value;
}
import React from "react";
export default React;`,
          map: null,
        };
      }
      // Plain shim — useSyncExternalStore is built into React 19
      return {
        code: `export { useSyncExternalStore } from "react";
import React from "react";
export default React;`,
        map: null,
      };
    },
  };
}

export default defineConfig({
  server: {
    port: 3000,
  },
  plugins: [
    tsConfigPaths({ projects: ["./tsconfig.json"] }),
    tailwindcss(),
    fixUseSyncExternalStore(),
    tanstackStart({
      srcDirectory: "app",
      router: {
        routesDirectory: "routes",
      },
    }),
    viteReact(),
  ],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["tests/setup.ts"],
    include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
  },
});
