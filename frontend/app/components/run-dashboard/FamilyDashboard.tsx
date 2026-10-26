import { useGetRunMetricsV1RunsRunIdMetricsGet } from "../../lib/orval/runs/runs";
import { HttpError } from "../../lib/http-client";
import OverlayDashboard from "./OverlayDashboard";
import EarningsDashboard from "./EarningsDashboard";
import IntradayDashboard from "./IntradayDashboard";
import BehavioralPanel from "./BehavioralPanel";
import { useLocale } from "../../lib/locale";
import GreeksAttribution from "./GreeksAttribution";
import { pluck } from "./cards";
import { attributionSummary } from "../../lib/attribution";

/**
 * `FamilyDashboard` — Phase D §6.2 entry point.
 *
 * Switches on `family` and mounts the right run-level dashboard. The
 * shared "Behavioral Analysis" panel renders below the family cards
 * regardless of family. Loading/empty states are handled inline so the
 * component can be dropped into `runs/$runId.tsx` with a one-line mount:
 *
 *   {run?.family && <FamilyDashboard family={run.family} runId={run.id} />}
 */
export type FamilyKind = "overlay" | "earnings" | "intraday";

export interface FamilyDashboardProps {
  family: FamilyKind;
  runId: string;
}

export default function FamilyDashboard({ family, runId }: FamilyDashboardProps) {
  const {zh, ja } = useLocale();
  const { data, isLoading, isError, error } =
    useGetRunMetricsV1RunsRunIdMetricsGet(runId);

  if (isLoading) {
    return (
      <div
        data-testid="family-dashboard-loading"
        className="rounded-lg border border-neutral-200 bg-white p-4 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-950"
      >
        {zh ? "正在加载场景指标…" : ja("Loading family metrics…")}
      </div>
    );
  }

  if (isError) {
    // 404 metrics-not-found is a normal state for in-flight, orphaned, or
    // pre-evaluator runs — show a neutral pending panel rather than a red
    // error banner. (httpClient throws HttpError for non-2xx; React Query
    // surfaces it via `error`.)
    const httpStatus = error instanceof HttpError ? error.status : null;
    if (httpStatus === 404) {
      return (
        <div
          data-testid="family-dashboard-pending"
          role="status"
          className="rounded-lg border border-neutral-200 bg-white p-4 text-sm text-neutral-500 dark:border-neutral-800 dark:bg-neutral-950"
        >
          {zh ? "任务完成后，这里会显示场景指标。" : ja("Family metrics will appear here once the run completes.")}
        </div>
      );
    }
    return (
      <div
        role="alert"
        data-testid="family-dashboard-error"
        className="rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
      >
        {zh ? "加载任务指标失败。" : ja("Failed to load run metrics.")}
      </div>
    );
  }

  const body = (data && data.status === 200 ? data.data : {});
  const familySpecific = body.family_specific ?? null;
  const behavioral = body.behavioral ?? null;
  const familyGreeks = pluck(familySpecific, "greeks_attribution");
  const useFamily = attributionSummary(familyGreeks).measured > 0;
  const attribution = useFamily ? familyGreeks : pluck(behavioral, "greeks_attribution_summary");

  return (
    <section
      data-testid={`family-dashboard-${family}`}
      className="flex flex-col gap-4"
    >
      <header className="flex items-baseline gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          {zh ? ({ overlay: "期权覆盖策略", earnings: "财报事件", intraday: "日内交易" }[family] + "分析") : ja("{0} dashboard", family.charAt(0).toUpperCase() + family.slice(1))}
        </h2>
      </header>

      {family === "overlay" && (
        <OverlayDashboard familySpecific={familySpecific} generic={body.generic} />
      )}
      {family === "earnings" && (
        <EarningsDashboard familySpecific={familySpecific} />
      )}
      {family === "intraday" && (
        <IntradayDashboard familySpecific={familySpecific} />
      )}

      <GreeksAttribution attribution={attribution} source={useFamily ? "family" : "behavioral"} />
      <BehavioralPanel behavioral={behavioral} />
    </section>
  );
}
