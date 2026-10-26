import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { SignedIn, SignedOut } from "../lib/access-token-auth";
import { Button, Card, Chip, EmptyState, Skeleton } from "@heroui/react";
import { ClientOnly } from "../components/ClientOnly";
import RunCard, { type RunCardItem } from "../components/RunCard";
import { useListRunsV1RunsGet } from "../lib/orval/runs/runs";
import { useGetMeV1MeGet } from "../lib/orval/catalog/catalog";
import { parseContract, zRunsPage } from "../lib/contracts";
import { useLocale } from "../lib/locale";

/**
 * Home page at `/`.
 *
 * Gated behind access-token login: signed-out visitors get a sign-in CTA, signed-in
 * users see an editorial hero + quick links + the five most recent runs.
 * The runs preview calls the Orval-generated `useListRunsV1RunsGet` hook
 * directly — the list endpoint's response schema is opaque in Orval's
 * output, so we narrow to the actual service contract at the read
 * boundary only (`parseContract(zRunsPage, ...)`).
 *
 * The redesign uses HeroUI v3 primitives (Card, Button, Chip, EmptyState,
 * Skeleton) but keeps the data flow and test-ids untouched so Plan C's
 * Playwright suite continues to pass.
 */
export const Route = createFileRoute("/")({
  component: HomePage,
});

function HomePage() {
  return (
    <ClientOnly fallback={<HomeSkeleton />}>
      <SignedIn>
        <HomeAuthed />
      </SignedIn>
      <SignedOut>
        <HomeSignedOut />
      </SignedOut>
    </ClientOnly>
  );
}

function HomeAuthed() {
  // Top 5 by created_at desc. Run ids are ULID-derived, so lexical DESC
  // (which the backend applies) matches created_at DESC.
  const { data, isLoading, error } = useListRunsV1RunsGet({ limit: 5 });

  let items: RunCardItem[] = [];
  if (data?.status === 200) {
    try {
      const page = parseContract(zRunsPage, data.data);
      items = page.items as RunCardItem[];
    } catch {
      items = [];
    }
  }

  return (
    <div className="space-y-8">
      <Hero />
      <QuickLinks />
      <RecentRunsSection
        items={items}
        isLoading={isLoading}
        hasError={!!error}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Hero                                                               */
/* ------------------------------------------------------------------ */

/**
 * Editorial hero. Two-column asymmetric grid on desktop: 7-col headline +
 * 5-col "systems card" with live run counters. The headline splits across
 * three lines with a serif italic pull-word to break AI-slop uniformity.
 */
function Hero() {
  const navigate = useNavigate();
  const {zh, ja } = useLocale();
  return (
    <section
      aria-labelledby="home-hero-title"
      className="lo-rise lo-rise-1 lo-panel grid grid-cols-1 gap-6 p-8"
    >
      <div className="lg:col-span-12">
        <p className="lo-eyebrow">
          <span className="inline-block h-1.5 w-1.5 translate-y-[-2px] animate-pulse rounded-full bg-[var(--color-accent)]" />
          <span className="ml-2">{zh ? "期权研究 / 历史回放" : ja("OPTIONS RESEARCH / REPLAY")}</span>
        </p>
        <h1
          id="home-hero-title"
          className="lo-display mt-4 text-3xl font-semibold text-[color:var(--color-ink)] sm:text-4xl"
        >
          {zh ? "按你的规则研究期权交易。" : ja("Options trading, on your terms.")}
          <span className="mt-1 block text-[color:var(--color-ink-soft)]">
            {zh ? "设计 Context，检验每次决策。" : ja("Design the context. Test the decisions.")}
          </span>
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-[color:var(--color-ink-soft)]">
          {zh ? "选择预设或自定义任务，设置模型与交易规则；在运行记录中查看决策、订单、风险事件和分析结果。" : ja("Replay options markets with presets or your own task settings. Inspect decisions, orders, risk events and analysis in the run timeline.")}
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            size="lg"
            className="!bg-[var(--color-primary-600)] !text-white"
            onPress={() => navigate({ to: "/runs/new" })}
          >
            {zh ? "配置期权测试" : ja("Configure options run")}
          </Button>
          <Button
            variant="outline"
            size="lg"
            onPress={() => navigate({ to: "/runs" })}
          >
            {zh ? "查看运行记录" : ja("Browse runs")}
          </Button>
        </div>
      </div>

    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Quick links                                                        */
 /* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Quick links                                                        */
/* ------------------------------------------------------------------ */

/**
 * Three-up editorial cards with numbered rail, serif display title and
 * thin right-arrow chevron. Cards use HeroUI Card primitives so the hover
 * ring + focus states come from the shared design system.
 */
function QuickLinks() {
  const {zh, ja } = useLocale();
  // When the signed-in user is on the launch allowlist, replace the
  // default "Account" card with a "New run" launcher card so the
  // feature surfaces the moment a user lands on `/`.
  const me = useGetMeV1MeGet();
  const canLaunch =
    me.data?.status === 200 &&
    typeof me.data.data === "object" &&
    me.data.data !== null &&
    (me.data.data as { can_launch?: unknown }).can_launch === true;

  const thirdCard = canLaunch
    ? {
        href: "/runs/new",
        code: "03",
        title: zh ? "新建测试" : ja("New run"),
        blurb:
          zh ? "编写 Context，选择模型与风险规则，回放期权市场。" : ja("Write your context, select models and risk rules, and replay options markets."),
      }
    : {
        href: "/sign-in",
        code: "03",
        title: zh ? "账号" : ja("Account"),
        blurb:
          zh ? "登录后查看运行记录和分析。" : ja("Sign in to inspect runs and analysis."),
      };

  const links: {
    href: string;
    title: string;
    blurb: string;
    code: string;
  }[] = [
    {
      href: "/runs",
      code: "01",
      title: zh ? "运行记录" : ja("Runs"),
      blurb:
        zh ? "按场景、状态和类型查找测试，查看决策与结果。" : ja("Browse and filter runs by scenario, status and kind."),
    },
    {
      href: "/checkpoints",
      code: "02",
      title: zh ? "检查点" : ja("Checkpoints"),
      blurb:
        zh ? "查看冻结的决策上下文，提问或从中创建分支。" : ja("Inspect frozen decision context, ask follow-ups and fork from a state."),
    },
    thirdCard,
  ];
  return (
    <section aria-labelledby="quick-links-title" className="lo-rise lo-rise-2">
      <div className="mb-6 flex items-end justify-between border-b border-[var(--color-rule)] pb-3">
        <h2
          id="quick-links-title"
          className="text-lg font-semibold text-[color:var(--color-ink)]"
        >
          {zh ? "从这里开始" : ja("Start here")}
        </h2>
        <span className="lo-eyebrow">WORKSPACE</span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {links.map((l) => (
          <a
            key={l.href}
            href={l.href}
            className="group focus:outline-none"
          >
            <Card className="h-full !rounded-lg !border !border-[var(--color-rule)] !bg-white transition hover:!border-[color:var(--color-primary-400)] hover:shadow-sm dark:!bg-[var(--color-paper-2)]">
              <Card.Content className="flex h-full flex-col gap-3 p-5">
                <div className="flex items-start justify-between">
                  <span className="lo-mono text-[10px] tracking-[0.12em] text-[color:var(--color-ink-soft)]">
                    {l.code}
                  </span>
                  <Chevron />
                </div>
                <h3 className="text-base font-semibold text-[color:var(--color-ink)]">
                  {l.title}
                </h3>
                <p className="text-sm leading-relaxed text-[color:var(--color-ink-soft)]">
                  {l.blurb}
                </p>
              </Card.Content>
            </Card>
          </a>
        ))}
      </div>
    </section>
  );
}

function Chevron() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      className="h-5 w-5 text-[color:var(--color-ink-soft)] transition group-hover:translate-x-0.5 group-hover:text-[color:var(--color-primary-600)]"
    >
      <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Recent runs                                                        */
/* ------------------------------------------------------------------ */

function RecentRunsSection({
  items,
  isLoading,
  hasError,
}: {
  items: RunCardItem[];
  isLoading: boolean;
  hasError: boolean;
}) {
  const {zh, ja } = useLocale();
  return (
    <section aria-labelledby="recent-runs-title" className="lo-rise lo-rise-3">
      <div className="mb-6 flex items-end justify-between border-b border-[var(--color-rule)] pb-3">
        <h2
          id="recent-runs-title"
          className="text-lg font-semibold text-[color:var(--color-ink)]"
        >
          {zh ? "最近运行" : ja("Recent runs")}
        </h2>
        <div className="flex items-center gap-4">
          <Chip size="sm" color="accent" variant="soft">
            {zh ? "最近 5 条" : ja("Last 5")}
          </Chip>
          <a
            href="/runs"
            className="lo-mono text-[11px] uppercase tracking-[0.16em] text-[color:var(--color-primary-600)] hover:text-[color:var(--color-primary-700)] hover:underline"
          >
            {zh ? "查看全部 →" : ja("View all →")}
          </a>
        </div>
      </div>

      {isLoading ? (
        <RecentRunsSkeleton />
      ) : hasError ? (
        <Card className="!border-[color:var(--color-rule)] !bg-[var(--color-paper)]">
          <Card.Content className="p-6">
            <p className="text-sm text-[color:oklch(0.55_0.18_25)]">
              {zh ? "加载失败，请刷新页面。" : ja("Failed to load recent runs. Try refreshing.")}
            </p>
          </Card.Content>
        </Card>
      ) : items.length === 0 ? (
        <EmptyState className="rounded-lg border border-dashed border-[var(--color-rule)] bg-white p-10 text-center dark:bg-[var(--color-paper-2)]">
          <p className="lo-eyebrow">{zh ? "尚无记录" : ja("Nothing staged yet")}</p>
          <h3 className="mt-2 text-xl font-medium text-[color:var(--color-ink)]">
            {zh ? "还没有运行记录。" : ja("No runs on the tape.")}
          </h3>
          <p className="mx-auto mt-2 max-w-md text-sm text-[color:var(--color-ink-soft)]">
            {zh ? "新建一次期权测试后，可以在这里查看决策时间线。" : ja("Configure an options run to see your first decision timeline.")}
          </p>
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {items.slice(0, 5).map((run) => (
            <RunCard key={run.id} run={run} />
          ))}
        </div>
      )}
    </section>
  );
}

function RecentRunsSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Skeleton
          key={i}
          className="h-[148px] !rounded-2xl !bg-[var(--color-paper-2)]"
        />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Skeleton & signed-out                                              */
/* ------------------------------------------------------------------ */

function HomeSkeleton() {
  return (
    <div className="space-y-10">
      <Skeleton className="h-64 !rounded-2xl" />
      <RecentRunsSkeleton />
    </div>
  );
}

function HomeSignedOut() {
  const navigate = useNavigate();
  const {zh, ja } = useLocale();
  return (
    <div className="space-y-14">
      <Hero />
      <section
        aria-labelledby="signed-out-title"
        className="lo-rise lo-rise-2 relative overflow-hidden rounded-2xl border border-[var(--color-rule)] bg-[color-mix(in_oklab,var(--color-paper-2)_70%,white)] p-10"
      >
        <div
          aria-hidden
          className="pointer-events-none absolute -right-16 -top-16 h-64 w-64 rounded-full bg-[color:var(--color-primary-200)] opacity-40 blur-3xl"
        />
        <p className="lo-eyebrow">{zh ? "需要登录" : ja("Authentication required")}</p>
        <h2
          id="signed-out-title"
          className="lo-display mt-2 text-4xl text-[color:var(--color-ink)]"
        >
          {zh ? "登录后继续" : ja("Sign in to continue")}
        </h2>
        <p className="mt-3 max-w-xl text-sm text-[color:var(--color-ink-soft)]">
          {zh ? "运行记录、检查点和分析需要登录。请输入访问令牌。" : ja("Access to runs, checkpoints and analysis requires an authenticated session. Enter your access token to continue.")}
        </p>
        <div className="mt-8">
          <Button
            variant="primary"
            size="lg"
            className="!bg-[var(--color-primary-700)] !text-white"
            onPress={() => navigate({ to: "/sign-in" })}
          >
            {zh ? "登录" : ja("Sign in")}
          </Button>
        </div>
      </section>
    </div>
  );
}
