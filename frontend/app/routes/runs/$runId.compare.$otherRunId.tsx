import { createFileRoute } from "@tanstack/react-router";
import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import ClientOnly from "../../components/ClientOnly";
import { formatMoney } from "../../lib/format";
import { useCompareRunsV1RunsACompareBGet } from "../../lib/orval/runs/runs";
import { parseContract, zCompareResponse } from "../../lib/contracts";

/**
 * /runs/:runId/compare/:otherRunId — side-by-side run comparison.
 *
 * Data source: `GET /v1/runs/:a/compare/:b` (Plan B compare endpoint). The
 * OpenAPI schema flattens the response into `{ [key: string]: unknown }`, so
 * we re-describe the narrow shape actually returned by
 * `liveoption_web/runs/service.py::compare_runs`:
 *
 *   - `a`, `b`                 run metadata (id, scenario, status, ...)
 *   - `days[i]`                aligned-by-trading_day PnL rows
 *                              { trading_day, a_pnl, b_pnl }
 *   - `decisions[i]`           aligned-by-seq decision rows
 *                              { seq, a: row|null, b: row|null }
 *
 * Missing-sided rows (either `a` or `b` is `null`) are highlighted so the
 * reader instantly sees where the two runs diverge.
 */

export const Route = createFileRoute("/runs/$runId/compare/$otherRunId")({
  component: CompareRunsPage,
});

interface RunMeta {
  id?: string | null;
  scenario?: string | null;
  status?: string | null;
  title?: string | null;
  date_start?: string | null;
  date_end?: string | null;
  model_id?: string | null;
  provider?: string | null;
  final_pnl?: number | null;
  n_decisions?: number | null;
  n_days?: number | null;
}

interface CompareDay {
  trading_day?: string | null;
  a_pnl?: number | null;
  b_pnl?: number | null;
}

interface CompareDecisionSide {
  seq?: number | null;
  trading_day?: string | null;
  has_trade?: boolean | null;
  cum_pnl_after?: number | null;
  model_id?: string | null;
}

interface CompareDecisionPair {
  seq: number;
  a?: CompareDecisionSide | null;
  b?: CompareDecisionSide | null;
}

interface CompareResponse {
  a?: RunMeta | null;
  b?: RunMeta | null;
  days?: CompareDay[] | null;
  decisions?: CompareDecisionPair[] | null;
}

function CompareRunsPage() {
  const { runId, otherRunId } = Route.useParams();
  const query = useCompareRunsV1RunsACompareBGet(runId, otherRunId);

  const payload = useMemo<CompareResponse | null>(() => {
    // The Orval mutator returns { data, status, headers } on success.
    const raw = (query.data as { data?: unknown } | undefined)?.data;
    if (raw == null) return null;
    try {
      // Validate envelope shape; inner rows stay loosely typed via the
      // page-local CompareResponse interface (the backend compare projection
      // is service-specific and not worth schema-pinning field-by-field).
      parseContract(zCompareResponse, raw);
      return raw as unknown as CompareResponse;
    } catch {
      return null;
    }
  }, [query.data]);

  const a = payload?.a ?? null;
  const b = payload?.b ?? null;
  const days = useMemo<CompareDay[]>(
    () => (Array.isArray(payload?.days) ? (payload!.days as CompareDay[]) : []),
    [payload],
  );
  const decisions = useMemo<CompareDecisionPair[]>(
    () =>
      Array.isArray(payload?.decisions)
        ? (payload!.decisions as CompareDecisionPair[])
        : [],
    [payload],
  );

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Compare runs</h1>
        <span className="font-mono text-xs text-neutral-500">
          {runId} vs {otherRunId}
        </span>
        {query.isLoading && (
          <span className="text-xs text-neutral-500">Loading…</span>
        )}
      </header>

      {query.isError && (
        <div
          data-testid="compare-error"
          className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          Failed to load comparison.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <MetricsPanel label="A" runId={runId} run={a} tone="indigo" />
        <MetricsPanel label="B" runId={otherRunId} run={b} tone="amber" />
      </div>

      <section className="overflow-hidden rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
          Cumulative PnL (aligned by trading day)
        </h2>
        <ClientOnly
          fallback={
            <div
              data-testid="compare-chart-fallback"
              className="flex h-[280px] items-center justify-center text-sm text-neutral-500"
            >
              Loading chart…
            </div>
          }
        >
          <DualPnLChart days={days} />
        </ClientOnly>
      </section>

      <section className="overflow-hidden rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-950">
        <h2 className="border-b border-neutral-200 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:border-neutral-800">
          Decisions diff (aligned by seq)
        </h2>
        <DecisionsDiffTable decisions={decisions} />
      </section>
    </div>
  );
}

/* ------------------------------- Metrics ------------------------------- */

function MetricsPanel({
  label,
  runId,
  run,
  tone,
}: {
  label: "A" | "B";
  runId: string;
  run: RunMeta | null;
  tone: "indigo" | "amber";
}) {
  const chip =
    tone === "indigo"
      ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300"
      : "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300";
  return (
    <div
      data-testid={`compare-metrics-${label.toLowerCase()}`}
      className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <div className="mb-2 flex items-center gap-2">
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${chip}`}
        >
          Run {label}
        </span>
        <span className="font-mono text-xs text-neutral-500">
          {run?.id ?? runId}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        <Field k="Scenario" v={run?.scenario} />
        <Field k="Status" v={run?.status} />
        <Field k="Model" v={run?.model_id} />
        <Field k="Provider" v={run?.provider} />
        <Field k="Date start" v={run?.date_start} />
        <Field k="Date end" v={run?.date_end} />
        <Field
          k="Final PnL"
          v={
            typeof run?.final_pnl === "number"
              ? formatMoney(run.final_pnl)
              : null
          }
        />
        <Field
          k="Decisions"
          v={
            typeof run?.n_decisions === "number"
              ? String(run.n_decisions)
              : null
          }
        />
      </dl>
    </div>
  );
}

function Field({ k, v }: { k: string; v?: string | null }) {
  return (
    <>
      <dt className="text-neutral-500">{k}</dt>
      <dd className="font-medium">{v ?? "—"}</dd>
    </>
  );
}

/* ------------------------------- Chart --------------------------------- */

/** Exported for unit tests. */
export function DualPnLChart({ days }: { days: CompareDay[] }) {
  if (!days || days.length === 0) {
    return (
      <div
        data-testid="compare-chart-empty"
        className="flex h-[280px] items-center justify-center rounded-md border border-dashed border-neutral-300 text-sm text-neutral-500 dark:border-neutral-700"
      >
        No overlapping trading days yet.
      </div>
    );
  }

  const data = days.map((d) => ({
    trading_day: d.trading_day ?? "",
    a_pnl: typeof d.a_pnl === "number" ? d.a_pnl : undefined,
    b_pnl: typeof d.b_pnl === "number" ? d.b_pnl : undefined,
  }));

  return (
    <div data-testid="compare-chart" style={{ width: "100%", height: 280 }}>
      <ResponsiveContainer>
        <LineChart
          data={data}
          margin={{ top: 8, right: 16, left: 8, bottom: 8 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            className="stroke-neutral-200 dark:stroke-neutral-800"
          />
          <XAxis dataKey="trading_day" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis
            tick={{ fontSize: 11 }}
            width={60}
            tickFormatter={(v: number) =>
              typeof v === "number"
                ? v.toLocaleString("en-US", { notation: "compact" })
                : String(v)
            }
          />
          <Tooltip
            formatter={(v: unknown) =>
              typeof v === "number"
                ? v.toLocaleString("en-US", { maximumFractionDigits: 2 })
                : String(v ?? "—")
            }
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="a_pnl"
            name="A"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="b_pnl"
            name="B"
            stroke="#f59e0b"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ------------------------------- Diff table ---------------------------- */

/** Exported for unit tests. */
export function DecisionsDiffTable({
  decisions,
}: {
  decisions: CompareDecisionPair[];
}) {
  if (!decisions || decisions.length === 0) {
    return (
      <div className="p-4 text-sm text-neutral-500">No aligned decisions.</div>
    );
  }
  return (
    <div className="max-h-[60vh] overflow-auto">
      <table
        data-testid="compare-decisions-table"
        className="w-full border-collapse text-sm"
      >
        <thead className="sticky top-0 bg-neutral-50 text-left text-xs uppercase tracking-wide text-neutral-500 dark:bg-neutral-900">
          <tr>
            <th className="px-3 py-2">Seq</th>
            <th className="px-3 py-2">A · trading day</th>
            <th className="px-3 py-2">A · trade</th>
            <th className="px-3 py-2">A · cum PnL</th>
            <th className="px-3 py-2">B · trading day</th>
            <th className="px-3 py-2">B · trade</th>
            <th className="px-3 py-2">B · cum PnL</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((row) => {
            const aMissing = row.a == null;
            const bMissing = row.b == null;
            const divergent = aMissing || bMissing;
            return (
              <tr
                key={row.seq}
                data-testid={`compare-decision-row-${row.seq}`}
                data-divergent={divergent ? "true" : "false"}
                className={
                  divergent
                    ? "bg-amber-50 dark:bg-amber-950/30"
                    : "odd:bg-white even:bg-neutral-50 dark:odd:bg-neutral-950 dark:even:bg-neutral-900"
                }
              >
                <td className="px-3 py-1.5 font-mono text-xs">{row.seq}</td>
                <SideCells side={row.a} missing={aMissing} />
                <SideCells side={row.b} missing={bMissing} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SideCells({
  side,
  missing,
}: {
  side?: CompareDecisionSide | null;
  missing: boolean;
}) {
  if (missing || !side) {
    return (
      <>
        <td
          colSpan={3}
          className="px-3 py-1.5 text-xs italic text-amber-700 dark:text-amber-300"
        >
          None — no matching decision on this side
        </td>
      </>
    );
  }
  const trade = side.has_trade ? "yes" : side.has_trade === false ? "no" : "—";
  const cum =
    typeof side.cum_pnl_after === "number"
      ? formatMoney(side.cum_pnl_after)
      : "—";
  return (
    <>
      <td className="px-3 py-1.5">{side.trading_day ?? "—"}</td>
      <td className="px-3 py-1.5">{trade}</td>
      <td className="px-3 py-1.5 tabular-nums">{cum}</td>
    </>
  );
}
