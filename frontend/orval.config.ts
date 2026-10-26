import { defineConfig } from "orval";

/**
 * Orval config.
 *
 * `target` and `schemas` live in sibling directories so that generated
 * tag-split files (under `app/lib/orval/<tag>/<tag>.ts`) can import schema
 * types via `../../model/...` without colliding with the tag-file layout.
 * (Orval's `tags-split` mode emits a `<tag>/<tag>.ts` per tag and a barrel
 * at `target`; the schemas directory must not share a name with the target
 * or with any tag, otherwise generated imports resolve to a non-existent
 * barrel.)
 *
 * The `httpClient` mutator is a plain fetch-based client whose signature
 * matches Orval's default `react-query` client expectation:
 *   `(url: string, options: RequestInit) => Promise<T>`
 */
export default defineConfig({
  liveoption: {
    input: "./openapi.json",
    output: {
      target: "./app/lib/orval/endpoints.ts",
      schemas: "./app/lib/orval/model",
      client: "react-query",
      mode: "tags-split",
      mock: true,
      prettier: false,
      override: {
        mutator: {
          path: "./app/lib/http-client.ts",
          name: "httpClient",
        },
      },
    },
  },
});
