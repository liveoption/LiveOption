import OverlayView from "./OverlayView";
import EarningsView from "./EarningsView";
import IntradayView from "./IntradayView";

/**
 * `FamilyCheckpointView` — Phase D §6.3 entry point.
 *
 * Switches on `family_view.kind` from the checkpoint's
 * `portfolio_summary`. Returns `null` when the kind is missing or
 * unknown so the parent route can rely on the generic portfolio summary
 * remaining the only render path for null-family checkpoints.
 */
export interface FamilyCheckpointViewProps {
  portfolioSummary: unknown;
}

export default function FamilyCheckpointView({
  portfolioSummary,
}: FamilyCheckpointViewProps) {
  if (!portfolioSummary || typeof portfolioSummary !== "object") return null;
  const ps = portfolioSummary as Record<string, unknown>;
  const view = ps.family_view as Record<string, unknown> | null | undefined;
  if (!view || typeof view !== "object") return null;
  const kind = view.kind;

  if (kind === "overlay") return <OverlayView view={view} />;
  if (kind === "earnings") return <EarningsView view={view} />;
  if (kind === "intraday") return <IntradayView view={view} />;
  return null;
}
