export type RunStatus = "pending" | "running" | "completed" | "failed" | string;

/** Live = run is still producing data (SSE active, REST disabled). */
export function isLiveStatus(status: RunStatus | undefined | null): boolean {
  return status === "pending" || status === "running";
}
