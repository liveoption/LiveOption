import { useEffect, useState } from "react";
import { Button } from "@heroui/react";

function getInitialDark(): boolean {
  if (typeof window === "undefined") return false;
  const saved = localStorage.getItem("lo-theme");
  if (saved === "dark") return true;
  if (saved === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/**
 * Theme toggle — cycles light / dark and persists to localStorage.
 *
 * A skeleton placeholder is rendered until mount to avoid a hydration
 * mismatch (the initial value depends on localStorage / matchMedia).
 *
 * Initial state is synchronised with the inline anti-flicker script in
 * __root.tsx so the icon is correct from the first render (no flash).
 */
export function ThemeToggle() {
  const [isDark, setIsDark] = useState(() => {
    if (typeof window !== "undefined") {
      // Mirror the same logic as the __root.tsx inline script so the
      // icon matches what the server rendered before React hydrates.
      const t = (window as Window & { __LO_THEME_INITIAL__?: string })
        .__LO_THEME_INITIAL__;
      if (t === "dark") return true;
      if (t === "light") return false;
    }
    return false;
  });
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setIsDark(getInitialDark());
    setMounted(true);

    const handleChange = () => {
      const saved = localStorage.getItem("lo-theme");
      const dark =
        saved === "dark" ||
        (saved !== "light" &&
          window.matchMedia("(prefers-color-scheme: dark)").matches);
      setIsDark(dark);
    };
    window.addEventListener("lo-theme-change", handleChange);
    window.addEventListener("storage", (e) => {
      if (e.key === "lo-theme") handleChange();
    });
    return () => window.removeEventListener("lo-theme-change", handleChange);
  }, []);

  const toggle = () => {
    const next = !isDark;
    setIsDark(next);
    localStorage.setItem("lo-theme", next ? "dark" : "light");
    if (next) {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
    window.dispatchEvent(new Event("lo-theme-change"));
  };

  if (!mounted) {
    return (
      <div
        aria-hidden
        className="h-9 w-9 rounded-md bg-[var(--color-paper-2)]"
      />
    );
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onPress={toggle}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className="px-2"
    >
      {isDark ? (
        <svg
          className="h-5 w-5"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z"
          />
        </svg>
      ) : (
        <svg
          className="h-5 w-5"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z"
          />
        </svg>
      )}
    </Button>
  );
}
