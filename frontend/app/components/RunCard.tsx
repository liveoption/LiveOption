import { Card, Chip } from "@heroui/react";
import { formatDateTime } from "../lib/format";

/**
 * Minimal run row shape consumed by `<RunCard>`.
 *
 * Mirrors the subset of columns the `/v1/runs` list endpoint returns that
 * the card actually renders. Kept permissive (`string | null | undefined`)
 * because the Orval-generated response type for `ListRunsV1RunsGet200` is
 * opaque (`{ [key: string]: unknown }`) — callers pass raw items and this
 * component tolerates missing fields.
 */
export type RunCardItem = {
  id: string;
  scenario?: string | null;
  status?: string | null;
  kind?: string | null;
  title?: string | null;
  created_at?: string | null;
};

export interface RunCardProps {
  run: RunCardItem;
  /** Optional override for the card's link target. Defaults to `/runs/:id`. */
  href?: string;
}

/**
 * Editorial run card — the atomic unit on the home page and the runs list
 * preview.
 *
 * Design principles:
 *   1. Status-first: kind + status chips sit on the top rule so the reader
 *      identifies what they're looking at before parsing the title.
 *   2. Numerals are mono and tabular so ids line up down the grid.
 *   3. The card is a real `<a>`; right-click "open in new tab" works and
 *      the hover indigo left-rail signals it's a link.
 */
export default function RunCard({ run, href }: RunCardProps) {
  const target = href ?? `/runs/${run.id}`;
  const title = run.title?.trim() || run.scenario || run.id;

  return (
    <a
      href={target}
      data-testid="run-card"
      aria-label={`Open run ${title}${run.status ? `, status ${run.status}` : ""}`}
      className="group focus:outline-none"
    >
      <Card
        className={[
          "relative h-full overflow-hidden !rounded-2xl !border",
          "!border-[var(--color-rule)] !bg-[var(--color-paper)]",
          "transition duration-200",
          "hover:-translate-y-0.5",
          "hover:!border-[color:var(--color-primary-300)]",
          "hover:shadow-[0_12px_30px_-18px_rgba(70,50,160,0.35)]",
        ].join(" ")}
      >
        {/* Left indigo rail — signature hover tell. */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 left-0 w-[3px] origin-top scale-y-0 bg-[color:var(--color-primary-500)] transition-transform duration-300 ease-out group-hover:scale-y-100"
        />
        <Card.Content className="flex h-full flex-col gap-4 p-5 pl-6">
          <header className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <KindChip kind={run.kind} />
              <StatusChip status={run.status} />
            </div>
            <span className="lo-mono text-[10px] uppercase tracking-[0.18em] text-[color:var(--color-ink-soft)]">
              {formatDateTime(run.created_at) || "—"}
            </span>
          </header>

          <div>
            <h3 className="truncate text-xl font-medium leading-tight text-[color:var(--color-ink)]">
              {title}
            </h3>
            <p className="lo-mono mt-1.5 truncate text-[11px] text-[color:var(--color-ink-soft)]">
              {run.id}
            </p>
          </div>

          <footer className="mt-auto flex items-center justify-between border-t border-dashed border-[var(--color-rule)] pt-3">
            <span className="lo-eyebrow truncate">
              {run.scenario ?? "no scenario"}
            </span>
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.25"
              className="h-4 w-4 shrink-0 text-[color:var(--color-ink-soft)] transition group-hover:translate-x-0.5 group-hover:text-[color:var(--color-primary-600)]"
            >
              <path
                d="M5 12h14M13 6l6 6-6 6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </footer>
        </Card.Content>
      </Card>
    </a>
  );
}

function KindChip({ kind }: { kind?: string | null }) {
  const label = (kind ?? "run").toLowerCase();
  // HeroUI v3 Chip `color` accepts: accent | danger | default | success | warning
  const color: React.ComponentProps<typeof Chip>["color"] =
    label === "fork" ? "accent" : label === "live" ? "success" : "default";
  return (
    <Chip
      size="sm"
      color={color}
      variant="soft"
      className="lo-mono !text-[10px] !tracking-[0.18em] uppercase"
    >
      {label}
    </Chip>
  );
}

function StatusChip({ status }: { status?: string | null }) {
  if (!status) return null;
  const s = status.toLowerCase();
  const color: React.ComponentProps<typeof Chip>["color"] =
    s === "finished" || s === "completed" || s === "done"
      ? "success"
      : s === "errored" || s === "failed" || s === "error"
        ? "danger"
        : s === "running"
          ? "warning"
          : "default";
  return (
    <Chip
      size="sm"
      color={color}
      variant="soft"
      className="lo-mono !text-[10px] !tracking-[0.18em] uppercase"
    >
      {status}
    </Chip>
  );
}
