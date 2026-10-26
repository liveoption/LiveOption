import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";

import DecisionInspector from "../../components/DecisionInspector";
import type { DecisionRow } from "../../components/DecisionTimeline";
import { useTimelineV1RunsRunIdTimelineGet } from "../../lib/orval/runs/runs";
import { parseContract, zTimelinePage } from "../../lib/contracts";

/**
 * Drawer route nested under `/runs/:runId` that scopes the inspector to a
 * single decision (`seq`).
 *
 * Renders into the parent's `<Outlet/>` inside the Inspector pane. Closing
 * the drawer navigates back to `/runs/:runId` and clears `search.seq` so the
 * timeline selection state matches the URL.
 */

export const Route = createFileRoute("/runs/$runId/decisions/$seq")({
  component: DecisionDrawer,
});

function DecisionDrawer() {
  const { runId, seq } = Route.useParams();
  const navigate = useNavigate();

  const seqNum = useMemo(() => {
    const n = Number(seq);
    return Number.isFinite(n) ? n : null;
  }, [seq]);

  // Share the cached timeline; TanStack Query dedupes with the parent route's
  // request so this is free. We pull the single row rather than adding a new
  // /decisions/{id} endpoint (not yet exposed by the backend).
  const timelineQuery = useTimelineV1RunsRunIdTimelineGet(runId);

  const decision = useMemo<DecisionRow | null>(() => {
    const raw = timelineQuery.data?.data;
    if (raw == null || seqNum == null) return null;
    let list: DecisionRow[] = [];
    try {
      list = parseContract(zTimelinePage, raw).items as DecisionRow[];
    } catch {
      return null;
    }
    return list.find((d) => d.seq === seqNum) ?? null;
  }, [timelineQuery.data, seqNum]);

  const close = () => {
    navigate({
      to: "/runs/$runId",
      params: { runId },
      search: { section: "timeline" },
    });
  };

  if (timelineQuery.isLoading) {
    return (
      <div className="p-4 text-sm text-neutral-500" data-testid="decision-drawer-loading">
        Loading decision #{seq}…
      </div>
    );
  }

  return <DecisionInspector decision={decision} requestedSeq={seqNum} onClose={close} onOpenRecord={decision ? () => navigate({ to: "/runs/$runId", params: { runId }, search: { section: "history", seq: decision.seq } }) : undefined} />;
}
