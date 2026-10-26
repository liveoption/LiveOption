import { SignedIn, SignedOut, useAccessAuth } from "../lib/access-token-auth";
import { Button, Drawer, useOverlayState } from "@heroui/react";
import { useNavigate } from "@tanstack/react-router";
import { ClientOnly } from "./ClientOnly";
import { ThemeToggle } from "./ThemeToggle";
import { useLocale, type Locale } from "../lib/locale";

/**
 * Top navigation bar.
 *
 * HeroUI v3 does not ship a Navbar primitive, so this is a custom semantic
 * <nav> styled to the LiveOption editorial system (hairline bottom rule,
 * monogram wordmark, uppercase mono nav links, indigo underline-hover).
 * HeroUI's Button is used for the auth action so focus states, motion, and
 * press behaviour match the rest of the UI.
 *
 * The skip-to-content link remains first in DOM order for keyboard users;
 * it targets `#main-content` (set in `__root.tsx`).
 */
export default function Navbar() {
  const drawerState = useOverlayState();
  const navigate = useNavigate();
  const { signOut } = useAccessAuth();
  const {zh, locale, change, ja } = useLocale();

  return (
    <nav
      aria-label="Primary"
      className="sticky top-0 z-40 border-b border-[var(--color-rule)] bg-white/95 backdrop-blur-md dark:bg-[var(--color-paper-2)]"
    >
      <a
        href="#main-content"
        aria-label="Skip to main content and jump to the primary content area"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-[var(--color-ink)] focus:px-3 focus:py-1.5 focus:text-sm focus:text-[var(--color-paper)] focus:shadow-lg"
      >
        Skip to main content
      </a>

      <div className="mx-auto flex h-12 w-full max-w-7xl items-center justify-between gap-2 px-4 sm:gap-6 sm:px-10">
        {/* Brand + nav */}
        <div className="flex items-center gap-8">
          <a
            href="/"
            aria-label="LiveOption home"
            className="group inline-flex shrink-0 items-center gap-2 text-[color:var(--color-ink)]"
          >
            <span
              aria-hidden
              className="inline-block h-3 w-3 rounded-[3px] bg-[color:var(--color-primary-600)] transition group-hover:scale-110"
            />
            <span className="text-sm font-bold tracking-tight">
              LiveOption
            </span>
            <span className="lo-mono hidden text-[10px] text-[color:var(--color-ink-soft)] tracking-[0.18em] sm:inline">
              / OPTIONS
            </span>
          </a>

          {/* Desktop nav — hidden on mobile */}
          <div className="hidden items-center gap-5 sm:flex">
            <NavLink href="/runs">{zh ? "运行记录" : ja("Runs")}</NavLink>
            <NavLink href="/checkpoints">{zh ? "检查点" : ja("Checkpoints")}</NavLink>
            <NavLink href="/settings/models">{zh ? "API 设置" : ja("API settings")}</NavLink>
          </div>
        </div>

        {/* Mobile hamburger + drawer — only visible on mobile */}
        <div className="flex items-center gap-3 sm:hidden">
          {/* Hamburger button */}
          <button
            aria-label="Open navigation menu"
            onClick={drawerState.open}
            className="flex items-center justify-center rounded-md p-2"
          >
            <svg
              className="h-6 w-6"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5"
              />
            </svg>
          </button>

          {/* Drawer overlay */}
          <Drawer state={drawerState}>
            <Drawer.Backdrop />
            <Drawer.Content placement="right">
              <Drawer.Dialog>
                {/* Close button */}
                <button
                  aria-label="Close navigation menu"
                  onClick={drawerState.close}
                  className="absolute right-4 top-4 flex items-center justify-center rounded-md p-2"
                >
                  <svg
                    className="h-5 w-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.5}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
                <Drawer.Body className="pt-16">
                  <nav className="flex flex-col gap-1">
                    <MobileNavLink href="/runs/new" onClose={drawerState.close}>
                      {zh ? "新建测试" : ja("Create run")}
                    </MobileNavLink>
                    <MobileNavLink href="/runs" onClose={drawerState.close}>
                      {zh ? "运行记录" : ja("Runs")}
                    </MobileNavLink>
                    <MobileNavLink
                      href="/checkpoints"
                      onClose={drawerState.close}
                    >
                      {zh ? "检查点" : ja("Checkpoints")}
                    </MobileNavLink>
                    <MobileNavLink href="/settings/models" onClose={drawerState.close}>
                      {zh ? "API 设置" : ja("API settings")}
                    </MobileNavLink>
                  </nav>
                </Drawer.Body>
              </Drawer.Dialog>
            </Drawer.Content>
          </Drawer>
        </div>

        <ClientOnly
          fallback={
            <div
              aria-hidden
              className="h-9 w-24 rounded-md bg-[var(--color-paper-2)]"
            />
          }
        >
          <div className="flex shrink-0 items-center gap-1 sm:gap-3">
            <select value={locale} onChange={(event) => change(event.target.value as Locale)}
              aria-label="Language / 语言 / 言語" data-testid="language-selector"
              className="max-w-24 rounded border border-[var(--color-rule)] bg-[var(--color-paper)] px-1 py-1 text-xs sm:max-w-none">
              <option value="en">English</option>
              <option value="zh">中文</option>
              <option value="ja">日本語</option>
            </select>
            <ThemeToggle />
            <SignedIn>
              <span className="hidden sm:inline"><NewRunCTA /></span>
              <Button
                variant="outline"
                size="sm"
                className="lo-mono !text-[11px] !tracking-[0.14em] uppercase"
                onPress={() => { void signOut(); }}
              >
                {zh ? "退出" : ja("Sign out")}
              </Button>
            </SignedIn>
            <SignedOut>
              <Button
                variant="primary"
                size="sm"
                className="lo-mono !bg-[var(--color-primary-700)] !text-white !text-[11px] !tracking-[0.14em] uppercase"
                onPress={() => navigate({ to: "/sign-in" })}
              >
                {zh ? "登录" : ja("Sign in")}
              </Button>
            </SignedOut>
          </div>
        </ClientOnly>
      </div>
    </nav>
  );
}

/**
 * "New run" CTA — visible to all signed-in users so the launch flow is
 * discoverable. Uses the access-token session to determine launch eligibility
 * (backend's can_launch = bool(user_id), so any authenticated user qualifies).
 */
function NewRunCTA() {
  const { authenticated } = useAccessAuth();
  const {zh, ja } = useLocale();
  const navigate = useNavigate();
  const canLaunch = authenticated;

  return (
    <span
      title={
        canLaunch
          ? "Configure an options run"
          : "Contact the admin to request launch access."
      }
    >
      <Button
        variant={canLaunch ? "primary" : "outline"}
        size="sm"
        isDisabled={!canLaunch}
        data-testid="navbar-new-run"
        aria-label={
          canLaunch
            ? "Launch a new run"
            : "Launch a new run (requires launch allowlist)"
        }
        className="lo-mono !bg-[var(--color-primary-600)] !text-[11px] !text-white !tracking-[0.14em] uppercase"
        onPress={() => {
          if (canLaunch) navigate({ to: "/runs/new" });
        }}
      >
        {zh ? "新建测试" : ja("Trade studio")}
      </Button>
    </span>
  );
}

/**
 * Uppercase mono nav link with an animated indigo underline on hover. The
 * underline uses a scale-x transform so it swipes in from the left rather
 * than fading in, keeping the motion deliberate.
 */
function NavLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      className="group relative inline-flex items-center py-2.5 text-[12px] font-medium uppercase tracking-[0.16em] text-[color:var(--color-ink)] transition hover:text-[color:var(--color-primary-600)]"
    >
      {children}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -bottom-0.5 h-[2px] origin-left scale-x-0 bg-[color:var(--color-primary-500)] transition-transform duration-300 ease-out group-hover:scale-x-100"
      />
    </a>
  );
}

/**
 * Mobile navigation link for the drawer menu. Uses py-3 for 48px touch
 * target and a similar animated underline to the desktop NavLink.
 */
function MobileNavLink({
  href,
  children,
  onClose,
}: {
  href: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <a
      href={href}
      onClick={onClose}
      className="group relative flex items-center py-3 text-base font-medium uppercase tracking-[0.16em] text-[color:var(--color-ink)] transition hover:text-[color:var(--color-primary-600)]"
    >
      {children}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-[2px] origin-left scale-x-0 bg-[color:var(--color-primary-500)] transition-transform duration-300 ease-out group-hover:scale-x-100"
      />
    </a>
  );
}
