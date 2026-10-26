import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Button,
  Chip as HeroChip,
  EmptyState,
  Spinner,
} from "@heroui/react";
import {
  listRunsV1RunsGet,
  getListRunsV1RunsGetQueryKey,
} from "../../lib/orval/runs/runs";
import type { ListRunsV1RunsGetParams } from "../../lib/orval/model";
import {
  parseContract,
  zRunsPage,
  type RunRow as ContractRunRow,
} from "../../lib/contracts";
import { requireAuth } from "../../lib/auth-guard";
import { useLocale } from "../../lib/locale";

/**
 * /runs — paginated, filterable list of all runs.
 *
 * URL is the source of truth for filters + paging. Search params:
 *   - scenario: string
 *   - status:   string (created|running|finished|errored|cancelled)
 *   - kind:     string (batch|live|fork)
 *   - featured: boolean
 *
 * The backend returns `{ items, next_cursor }`. We page via TanStack Query's
 * `useInfiniteQuery` and render a virtualized list (react-virtual) so the
 * page stays responsive at 10k+ rows. The filter bar is now built from
 * HeroUI v3 primitives (SearchField, Chip, Switch, Button); row rendering
 * keeps the fixed-height virtualizer design so the existing Playwright
 * `data-testid="runs-row"` + `data-testid="runs-virtual-scroll"` tests pass
 * unchanged.
 */
type RunsSearch = {
  scenario?: string;
  status?: string;
  kind?: string;
  featured?: boolean;
};

type RunRow = ContractRunRow;

function parseSearch(raw: Record<string, unknown>): RunsSearch {
  const out: RunsSearch = {};
  if (typeof raw.scenario === "string" && raw.scenario) out.scenario = raw.scenario;
  if (typeof raw.status === "string" && raw.status) out.status = raw.status;
  if (typeof raw.kind === "string" && raw.kind) out.kind = raw.kind;
  if (raw.featured === true || raw.featured === "true") out.featured = true;
  return out;
}

export const Route = createFileRoute("/runs/")({
  validateSearch: parseSearch,
  loader: async (ctx) => { await requireAuth(ctx); },
  component: RunsListPage,
});

const STATUS_OPTIONS = [
  "created",
  "running",
  "finished",
  "completed_with_errors",
  "errored",
  "cancelled",
] as const;

/** Human-readable label for the status filter chips. */
function statusLabel(s: string): string {
  return s === "completed_with_errors" ? "Completed w/ errors" : s;
}
const KIND_OPTIONS = ["batch", "live", "fork"] as const;
const PAGE_SIZE = 100;
const ROW_HEIGHT = 76;

function RunsListPage() {
  const {zh, ja } = useLocale();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();

  const baseParams: ListRunsV1RunsGetParams = useMemo(
    () => ({
      scenario: search.scenario ?? undefined,
      status: search.status ?? undefined,
      kind: search.kind ?? undefined,
      featured: search.featured ?? undefined,
      limit: PAGE_SIZE,
    }),
    [search.scenario, search.status, search.kind, search.featured],
  );

  const query = useInfiniteQuery({
    queryKey: [...getListRunsV1RunsGetQueryKey(baseParams), "infinite"],
    queryFn: async ({ pageParam }) => {
      const params: ListRunsV1RunsGetParams = {
        ...baseParams,
        cursor: (pageParam ?? undefined) as string | undefined,
      };
      const res = await listRunsV1RunsGet(params);
      return parseContract(zRunsPage, res.data);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last?.next_cursor ?? undefined,
  });

  const rows: RunRow[] = useMemo(
    () => (query.data?.pages ?? []).flatMap((p) => p?.items ?? []),
    [query.data],
  );

  const parentRef = useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    measureElement: (element) => element.getBoundingClientRect().height,
    overscan: 8,
  });

  const virtualItems = rowVirtualizer.getVirtualItems();
  useEffect(() => {
    const last = virtualItems[virtualItems.length - 1];
    if (!last) return;
    if (
      last.index >= rows.length - 1 &&
      query.hasNextPage &&
      !query.isFetchingNextPage
    ) {
      query.fetchNextPage();
    }
  }, [virtualItems, rows.length, query]);

  const setFilter = (patch: Partial<RunsSearch>) => {
    const next: RunsSearch = { ...search, ...patch };
    (Object.keys(next) as (keyof RunsSearch)[]).forEach((k) => {
      const v = next[k];
      if (v === undefined || v === "" || v === false) delete next[k];
    });
    navigate({
      search: next,
      replace: true,
    });
  };

  const hasActiveFilters =
    !!search.scenario || !!search.status || !!search.kind || !!search.featured;

  const showFlagColumn = rows.some((r) => r.is_featured);
  const gridClass = showFlagColumn ? "lo-runs-grid" : "lo-runs-grid lo-runs-grid--5";

  return (
    <div className="flex flex-col gap-6">
      {/* Page masthead ------------------------------------------------- */}
      <header className="lo-rise lo-rise-1 flex flex-col gap-2 border-b border-[var(--color-rule)] pb-5">
        <p className="lo-eyebrow">§ Runs · tape</p>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <h1 className="lo-display text-4xl text-[color:var(--color-ink)] sm:text-5xl">
            {zh ? "运行记录" : ja("Runs")}
          </h1>
          <div className="flex items-baseline gap-4 font-mono text-xs tracking-wide text-[color:var(--color-ink-soft)]">
            <span>
              <span className="lo-mono text-lg text-[color:var(--color-ink)]">
                {rows.length}
              </span>{" "}
              {zh ? "条已加载" : ja("loaded")}
            </span>
            {query.hasNextPage && (
              <span className="lo-mono uppercase tracking-[0.16em]">
                · {zh ? "还有更多" : ja("more available")}
              </span>
            )}
            {query.isFetchingNextPage && (
              <span className="flex items-center gap-2">
                <Spinner size="sm" /> {zh ? "加载中" : ja("fetching")}
              </span>
            )}
          </div>
        </div>
      </header>

      {/* Filter bar ---------------------------------------------------- */}
      <FilterBar
        search={search}
        onChange={setFilter}
        hasActiveFilters={hasActiveFilters}
      />

      {/* Table shell --------------------------------------------------- */}
      <div className="lo-rise lo-rise-2 overflow-hidden rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)]">
        <div className={`${gridClass} lo-runs-header border-b border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5 py-3 text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-soft)]`}>
          <span>{zh ? "场景" : ja("Scenario")}</span>
          <span>{zh ? "状态" : ja("Status")}</span>
          <span>{zh ? "类型" : ja("Kind")}</span>
          <span>{zh ? "名称 · ID" : ja("Title · id")}</span>
          <span>{zh ? "日期范围" : ja("Window")}</span>
          {showFlagColumn && <span className="text-right">{zh ? "标记" : ja("Flag")}</span>}
        </div>

        <div
          ref={parentRef}
          className={`lo-scroll relative overflow-auto ${
            !query.isLoading && rows.length === 0 ? "" : "h-[70vh]"
          }`}
          data-testid="runs-virtual-scroll"
        >
          {query.isLoading ? (
            <div className="flex h-full items-center justify-center gap-3 text-sm text-[color:var(--color-ink-soft)]">
              <Spinner size="md" /> {zh ? "加载运行记录…" : ja("Loading runs…")}
            </div>
          ) : rows.length === 0 ? (
            <EmptyState className="p-12 text-center">
              {hasActiveFilters ? (
                <>
                  <p className="lo-eyebrow">{zh ? "没有匹配结果" : ja("No match")}</p>
                  <h3 className="lo-display mt-2 text-2xl text-[color:var(--color-ink)]">
                    {zh ? "当前筛选条件没有结果。" : ja("Nothing on the tape for these filters.")}
                  </h3>
                  <p className="mx-auto mt-2 max-w-md text-sm text-[color:var(--color-ink-soft)]">
                    {zh ? "调整场景或状态筛选。" : ja("Try widening the scenario query or clearing the status chip.")}
                  </p>
                </>
              ) : (
                <>
                  <p className="lo-eyebrow">{zh ? "尚无运行" : ja("No runs yet")}</p>
                  <h3 className="lo-display mt-2 text-2xl text-[color:var(--color-ink)]">
                    {zh ? "还没有运行记录。" : ja("The tape is empty.")}
                  </h3>
                  <p className="mx-auto mt-2 max-w-md text-sm text-[color:var(--color-ink-soft)]">
                    {zh ? "启动一次测试后会显示在这里。" : ja("Launch your first run to see it here.")}
                  </p>
                  <Link
                    to="/runs/new"
                    className="mt-5 inline-flex items-center rounded-full border border-[var(--color-rule)] bg-[var(--color-accent)] px-5 py-2 text-xs font-medium uppercase tracking-[0.16em] text-[color:var(--color-ink)] transition hover:brightness-95"
                  >
                    {zh ? "新建测试" : ja("New run")}
                  </Link>
                </>
              )}
            </EmptyState>
          ) : (
            <div
              style={{
                height: rowVirtualizer.getTotalSize(),
                position: "relative",
                width: "100%",
              }}
            >
              {virtualItems.map((v) => {
                const row = rows[v.index];
                if (!row) return null;
                return (
                  <div
                    key={row.id}
                    ref={rowVirtualizer.measureElement}
                    data-index={v.index}
                    data-testid="runs-row"
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      transform: `translateY(${v.start}px)`,
                    }}
                  >
                    <RunRowView row={row} showFlag={showFlagColumn} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {query.isError && (
        <div
          role="alert"
          className="rounded-xl border border-[color:oklch(0.82_0.12_25)] bg-[color:oklch(0.96_0.04_25)] p-4 text-sm text-[color:oklch(0.35_0.15_25)]"
        >
          {zh ? "运行记录加载失败，请刷新页面。" : ja("Failed to load runs. Try refreshing; the backend may be warming up.")}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Filter bar                                                         */
/* ------------------------------------------------------------------ */

function FilterBar({
  search,
  onChange,
  hasActiveFilters,
}: {
  search: RunsSearch;
  onChange: (patch: Partial<RunsSearch>) => void;
  hasActiveFilters: boolean;
}) {
  const {zh, ja } = useLocale();
  return (
    <section
      aria-label="Filter runs"
      className="lo-rise lo-rise-2 flex flex-col gap-4 rounded-2xl border border-[var(--color-rule)] bg-[color-mix(in_oklab,var(--color-paper-2)_90%,white)] p-5"
    >
      <div className="flex flex-wrap items-start gap-5">
        {/* Scenario search -------------------------------------------- */}
        <div className="flex min-w-[260px] flex-col gap-1.5">
          <label
            htmlFor="runs-scenario"
            className="lo-eyebrow"
          >
            {zh ? "场景" : ja("Scenario")}
          </label>
          <div className="relative">
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[color:var(--color-ink-soft)]"
            >
              <path
                d="m21 21-4.3-4.3M10.8 18a7.2 7.2 0 1 1 0-14.4 7.2 7.2 0 0 1 0 14.4Z"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <input
              id="runs-scenario"
              type="search"
              value={search.scenario ?? ""}
              onInput={(e) =>
                onChange({ scenario: e.currentTarget.value || undefined })
              }
              aria-label="Scenario"
              placeholder={zh ? "搜索场景…" : ja("Search scenarios...")}
              className="h-11 w-full rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] py-2 pl-10 pr-9 text-sm text-[color:var(--color-ink)] shadow-[0_8px_24px_-18px_rgba(30,25,20,0.45)] outline-none placeholder:text-[color:var(--color-ink-soft)] focus:border-[color:var(--color-primary-400)]"
            />
            {!!search.scenario && (
              <button
                type="button"
                onClick={() => onChange({ scenario: undefined })}
                aria-label="Clear scenario filter"
                className="absolute right-2 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-[color:var(--color-ink-soft)] hover:bg-[var(--color-paper-2)] hover:text-[color:var(--color-ink)]"
              >
                x
              </button>
            )}
          </div>
        </div>

        {/* Status -------------------------------------------------- */}
        <div className="flex flex-col gap-1.5">
          <span className="lo-eyebrow">{zh ? "状态" : ja("Status")}</span>
          <div className="flex flex-wrap items-center gap-1.5">
            <FilterChip
              active={!search.status}
              onClick={() => onChange({ status: undefined })}
              label={zh ? "全部" : ja("all")}
            />
            {STATUS_OPTIONS.map((s) => (
              <FilterChip
                key={s}
                active={search.status === s}
                onClick={() =>
                  onChange({ status: search.status === s ? undefined : s })
                }
                label={statusLabel(s)}
              />
            ))}
          </div>
        </div>

        {/* Kind ---------------------------------------------------- */}
        <div className="flex flex-col gap-1.5">
          <span className="lo-eyebrow">{zh ? "类型" : ja("Kind")}</span>
          <div className="flex flex-wrap items-center gap-1.5">
            <FilterChip
              active={!search.kind}
              onClick={() => onChange({ kind: undefined })}
              label={zh ? "全部" : ja("all")}
            />
            {KIND_OPTIONS.map((k) => (
              <FilterChip
                key={k}
                active={search.kind === k}
                onClick={() =>
                  onChange({ kind: search.kind === k ? undefined : k })
                }
                label={k}
              />
            ))}
          </div>
        </div>

        {/* Featured + reset --------------------------------------- */}
        <div className="ml-auto flex items-center gap-4">
          <FeaturedToggle
            value={!!search.featured}
            onChange={(v) => onChange({ featured: v || undefined })}
          />
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onPress={() =>
                onChange({
                  scenario: undefined,
                  status: undefined,
                  kind: undefined,
                  featured: undefined,
                })
              }
              className="lo-mono !text-[11px] !uppercase !tracking-[0.14em]"
            >
              {zh ? "重置" : ja("Reset")}
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * Filter chip — a pressable pill shaped like an editorial byline chip. Uses
 * HeroUI's Chip visual vocabulary (soft/solid variants) but implemented as a
 * real <button> so keyboard + click semantics are transparent.
 */
function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={[
        "lo-mono inline-flex items-center rounded-full px-3 py-1 text-[11px] uppercase tracking-[0.14em]",
        "transition duration-150",
        active
          ? "bg-[color:var(--color-ink)] text-[color:var(--color-paper)] shadow-[0_1px_0_rgba(0,0,0,0.12)]"
          : "border border-[var(--color-rule)] bg-[var(--color-paper)] text-[color:var(--color-ink-soft)] hover:border-[color:var(--color-primary-300)] hover:text-[color:var(--color-ink)]",
      ].join(" ")}
    >
      {label}
    </button>
  );
}

/**
 * Self-contained on/off toggle for the "Featured only" filter. We avoid the
 * HeroUI v3 `<Switch>` because that ships as a compound API
 * (`Switch.Control` + `Switch.Thumb` children) and the previous bare
 * `<Switch isSelected … />` rendered an empty wrapper with no track or thumb.
 */
function FeaturedToggle({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      aria-label="Featured only"
      onClick={() => onChange(!value)}
      className="group inline-flex items-center gap-2.5"
    >
      <span
        className={[
          "relative inline-flex h-5 w-9 items-center rounded-full border transition",
          value
            ? "border-transparent bg-[color:var(--color-ink)]"
            : "border-[var(--color-rule)] bg-[var(--color-paper)]",
        ].join(" ")}
      >
        <span
          className={[
            "inline-block h-3.5 w-3.5 rounded-full bg-[var(--color-paper)] shadow-[0_1px_2px_rgba(0,0,0,0.2)] transition-transform",
            value
              ? "translate-x-[18px]"
              : "translate-x-[3px] bg-[color:var(--color-ink-soft)]",
          ].join(" ")}
        />
      </span>
      <span className="lo-mono text-[11px] uppercase tracking-[0.14em] text-[color:var(--color-ink)]">
        Featured only
      </span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Row view                                                           */
/* ------------------------------------------------------------------ */

function RunRowView({ row, showFlag }: { row: RunRow; showFlag: boolean }) {
  const status = (row.status ?? "").toLowerCase();
  const isCompletedWithErrors = status === "completed_with_errors";
  const statusColor =
    status === "finished" || status === "completed"
      ? "success"
      : status === "errored" || status === "failed" || isCompletedWithErrors
        ? "danger"
        : status === "running"
          ? "warning"
          : "default";
  const statusLabel = isCompletedWithErrors
    ? "\u26A0 Completed"
    : (row.status ?? "—");
  const kind = (row.kind ?? "run").toLowerCase();
  const kindColor =
    kind === "fork" ? "accent" : kind === "live" ? "success" : "default";

  const rowGridClass = showFlag ? "lo-runs-grid" : "lo-runs-grid lo-runs-grid--5";

  return (
    <Link
      to="/runs/$runId"
      params={{ runId: row.id }}
      className={`${rowGridClass} border-b border-[var(--color-rule)] px-5 py-4 transition hover:bg-[var(--color-paper-2)]`}
      style={{ minHeight: ROW_HEIGHT }}
    >
      <span className="lo-mono self-center truncate text-[11px] tracking-wide text-[color:var(--color-ink)]">
        {row.scenario ?? "—"}
      </span>
      <span
        className="self-center"
        data-testid="runs-row-status"
        data-status={status || undefined}
      >
        <HeroChip
          size="sm"
          color={statusColor}
          variant="soft"
          className={`lo-mono !text-[10px] !uppercase !tracking-[0.16em] ${
            isCompletedWithErrors ? "!text-red-700 dark:!text-red-300" : ""
          }`}
        >
          {statusLabel}
        </HeroChip>
      </span>
      <span className="lo-mono self-center text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-soft)]">
        <HeroChip
          size="sm"
          color={kindColor}
          variant="soft"
          className="lo-mono !text-[10px] !uppercase !tracking-[0.16em]"
        >
          {row.kind ?? "—"}
        </HeroChip>
      </span>
      <span className="flex min-w-0 flex-col justify-center self-center">
        <span className="lo-display truncate text-base leading-tight text-[color:var(--color-ink)]">
          {row.title ?? row.id}
        </span>
        <span className="lo-mono truncate text-[10px] text-[color:var(--color-ink-soft)]">
          {row.created_by ? `${row.id} · by ${row.created_by}` : row.id}
        </span>
      </span>
      <span className="lo-mono self-center truncate text-[11px] text-[color:var(--color-ink-soft)]">
        {row.date_start && row.date_end
          ? `${row.date_start} → ${row.date_end}`
          : "—"}
      </span>
      {showFlag && (
        <span className="flex justify-end self-center">
          {row.is_featured ? (
            <HeroChip
              size="sm"
              color="warning"
              variant="soft"
              className="lo-mono !text-[10px] !uppercase !tracking-[0.16em]"
            >
              ★ feat
            </HeroChip>
          ) : (
            <span className="lo-mono text-[11px] text-[color:var(--color-ink-soft)]">—</span>
          )}
        </span>
      )}
    </Link>
  );
}
