/**
 * Rendered when the server-side loader throws a 401 Response (unauthenticated).
 * Provides a clear message and a sign-in link.
 */
export function Unauthorized({ error }: { error: unknown }) {
  const message =
    error instanceof Error ? error.message : "Unauthorized";

  return (
    <div className="flex flex-col items-center justify-center gap-6 py-24 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[color:oklch(0.92_0.08_25)]">
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          className="h-8 w-8 text-[color:oklch(0.45_0.15_25)]"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
          />
        </svg>
      </div>
      <div className="flex flex-col gap-2">
        <h1 className="lo-display text-3xl text-[color:var(--color-ink)]">
          401 — Unauthorized
        </h1>
        <p className="text-[color:var(--color-ink-soft)]">
          {message}
        </p>
      </div>
      <a
        href="/sign-in"
        className="rounded-xl bg-[color:var(--color-ink)] px-6 py-3 font-medium text-[color:var(--color-paper)] transition hover:opacity-90"
      >
        Sign in
      </a>
    </div>
  );
}
