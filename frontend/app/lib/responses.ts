export type ResponseCall = {
  phase: string;
  raw: string | null;
  content: unknown;
  format: "json" | "text" | "structured" | "missing";
  saved?: Record<string, unknown>;
};

export function responseRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

/** Parse only complete JSON documents or a single complete Markdown fence.
 * Never repair malformed output, evaluate code, or discard surrounding prose. */
export function parseResponse(raw: string): Pick<ResponseCall, "content" | "format"> {
  const trimmed = raw.trim();
  const fence = trimmed.match(/^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/i);
  const candidate = fence ? fence[1] : trimmed;
  try { return { content: JSON.parse(candidate), format: "json" }; }
  catch { return { content: raw, format: "text" }; }
}

export function responseCalls(value: unknown): ResponseCall[] {
  const call = (value: unknown, index: number): ResponseCall => {
    const record = responseRecord(value);
    const phase = typeof record?.phase === "string" ? record.phase : `#${index + 1}`;
    if (typeof record?.raw === "string") {
      const saved = Object.fromEntries(Object.entries(record).filter(([key]) => key !== "phase" && key !== "raw"));
      return { phase, raw: record.raw, ...parseResponse(record.raw), ...(Object.keys(saved).length ? { saved } : {}) };
    }
    if (typeof value === "string") return { phase, raw: value, ...parseResponse(value) };
    const content = record ? Object.fromEntries(Object.entries(record).filter(([key]) => key !== "phase" && key !== "raw")) : value;
    return { phase, raw: null, content, format: content == null || (record && !Object.keys(content as object).length) ? "missing" : "structured" };
  };
  const root = responseRecord(value);
  return Array.isArray(root?.calls) && root.calls.length ? root.calls.map(call) : [call(value, 0)];
}

export function responseSummary(value: unknown): Record<string, unknown> | null {
  const root = responseRecord(value);
  if (!Array.isArray(root?.calls) || !root.calls.length) return null;
  const summary = Object.fromEntries(Object.entries(root).filter(([key]) => key !== "calls"));
  return Object.keys(summary).length ? summary : null;
}
