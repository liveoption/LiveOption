/// <reference types="vite/client" />
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import {
  createRootRoute,
  HeadContent,
  Outlet,
  Scripts,
} from "@tanstack/react-router";
import { AccessTokenProvider } from "../lib/access-token-auth";
import { QueryClientProvider } from "@tanstack/react-query";
import Navbar from "../components/Navbar";
import { ClientOnly } from "../components/ClientOnly";
import { GuidedTour } from "../components/GuidedTour";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { ErrorToastHost } from "../components/ErrorToastHost";
import { queryClient } from "../lib/queryClient";
import appCss from "../styles/app.css?url";
import { useLocale } from "../lib/locale";

declare global {
  interface Window {
    __LO_THEME_INITIAL__?: "dark" | "light";
  }
}

/**
 * Root route: mounts the full provider stack, owns the HTML document shell,
 * and renders the shared layout (Navbar + Outlet).
 *
 * Start 1.167+ requires the root route to render the `<html lang="en">` document
 * including `<HeadContent />` and `<Scripts />` — there is no external
 * SSR shell anymore (vinxi's `ssr.tsx` is gone).
 *
 * Provider order (outer -> inner):
 *   AccessTokenProvider   - auth context (HTTP-only session cookie)
 *   QueryClientProvider   - server-state cache (singleton QueryClient)
 */
export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "LiveOption" },
    ],
    links: [{ rel: "stylesheet", href: appCss }],
    scripts: [{
      children:
        "(function(){try{var t=localStorage.getItem('lo-theme');var d=false;if(t==='dark')d=true;else if(t==='light')d=false;else d=window.matchMedia('(prefers-color-scheme: dark)').matches;var c=document.documentElement.className.replace(/\\bdark\\b/g,'').trim();if(d)c+=' dark';document.documentElement.className=c;window.__LO_THEME_INITIAL__=d?'dark':'light';}catch(e){}})()",
    }],
  }),
  component: RootComponent,
});

function RootComponent() {
  return (
    <RootDocument>
      <AccessTokenProvider>
        <QueryClientProvider client={queryClient}>
          {/*
           * HeroUI v3 doesn't require a dedicated provider wrapper — its
           * styles + React Aria primitives are self-contained. We keep the
           * layout shell here (paper ground, max-width, focusable main
           * landmark for the skip-to-content link in Navbar).
           */}
          <div className="flex min-h-screen flex-col">
            <Navbar />
            <main
              id="main-content"
              tabIndex={-1}
              className="mx-auto w-full max-w-7xl flex-1 px-6 pb-20 pt-7 outline-none sm:px-10"
            >
              <ErrorBoundary>
                <Outlet />
              </ErrorBoundary>
            </main>
            <SiteFooter />
            <ClientOnly>
              <GuidedTour />
            </ClientOnly>
            {/*
             * Phase 3: global toast region for `worker.error` events.
             * Mounted as a sibling of <Outlet /> so toasts persist across
             * route transitions and overlay any route content (fixed top-4
             * right-4 z-50).
             */}
            <ClientOnly>
              <ErrorToastHost />
            </ClientOnly>
          </div>
        </QueryClientProvider>
      </AccessTokenProvider>
    </RootDocument>
  );
}

/**
 * Editorial footer — a hairline rule plus a pair of mono ticker lines. The
 * idea is to terminate the page with a definitive bottom rather than the
 * usual app-chrome drop-shadow band.
 */
function SiteFooter() {
  const { ja } = useLocale();
  return (
    <footer className="border-t border-[var(--color-rule)] px-6 py-6 sm:px-10">
      <div className="mx-auto flex w-full max-w-7xl flex-col items-start justify-between gap-2 text-xs text-[var(--color-ink-soft)] sm:flex-row sm:items-center">
        <span className="lo-mono tracking-wide">
          LIVEOPTION · {ja("options-trading research platform")}
        </span>
        <span className="lo-mono tracking-wide">
          v0.1 · {new Date().getFullYear()}
        </span>
      </div>
    </footer>
  );
}

function RootDocument({ children }: Readonly<{ children: ReactNode }>) {
  const [themeClass, setThemeClass] = useState(() => {
    if (typeof window === "undefined") return "";
    return window.__LO_THEME_INITIAL__ === "dark" ? "dark" : "";
  });

  useEffect(() => {
    const apply = () => {
      const saved = localStorage.getItem("lo-theme");
      const isDark =
        saved === "dark" ||
        (saved !== "light" &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      setThemeClass(isDark ? "dark" : "");
      if (isDark) {
        document.documentElement.classList.add("dark");
      } else {
        document.documentElement.classList.remove("dark");
      }
    };
    window.addEventListener("lo-theme-change", apply);
    window.addEventListener("storage", (e) => {
      if (e.key === "lo-theme") apply();
    });
    return () => window.removeEventListener("lo-theme-change", apply);
  }, []);

  return (
    <html lang="en" className={themeClass} suppressHydrationWarning>
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}
