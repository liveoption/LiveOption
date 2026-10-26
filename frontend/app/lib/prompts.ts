export type PromptPart = { text: string; variable?: string; format?: string };
export type PromptMessage = { text: string; parts: PromptPart[]; provenance: "recorded" | "template" | "unavailable" };
export type PromptCall = { phase: string; system?: PromptMessage; user?: PromptMessage };
const marker = /(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]+))?\}(?!\})/g;
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function templateParts(text: string, examples?: Map<string, string>): PromptPart[] {
  const parts: PromptPart[] = []; let previous = 0;
  for (const match of text.matchAll(marker)) {
    parts.push({ text: text.slice(previous, match.index) });
    parts.push({ text: examples?.get(match[1]) ?? match[0], variable: match[1], format: match[2] });
    previous = match.index! + match[0].length;
  }
  parts.push({ text: text.slice(previous) });
  return parts;
}

export function recordedParts(text: string, spans: unknown): PromptPart[] | null {
  if (!Array.isArray(spans) || spans.length > 10000) return null;
  const chars = Array.from(text); const parts: PromptPart[] = []; let previous = 0;
  for (const item of spans) {
    const span = record(item), start = span.start, end = span.end;
    if (typeof start !== "number" || typeof end !== "number" || !Number.isInteger(start) || !Number.isInteger(end) || start < previous || end < start || end > chars.length || typeof span.variable !== "string" || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(span.variable)) return null;
    parts.push({ text: chars.slice(previous, start).join("") });
    parts.push({ text: chars.slice(start, end).join(""), variable: span.variable, format: typeof span.format === "string" ? span.format : undefined });
    previous = end;
  }
  parts.push({ text: chars.slice(previous).join("") });
  return parts;
}

export function promptCalls(value: unknown, template = false): PromptCall[] {
  const root = record(value);
  const message = (text: unknown, meta: Record<string, unknown>, role: string): PromptMessage | undefined => {
    if (typeof text !== "string") return;
    const spans = meta.version === 1 && meta.offset_encoding === "unicode-codepoints" ? recordedParts(text, meta[role]) : null;
    return { text, parts: template ? templateParts(text) : spans ?? [{ text }], provenance: template ? "template" : spans ? "recorded" : "unavailable" };
  };
  const call = (value: unknown, index: number): PromptCall => {
    const item = record(value), meta = record(item._provenance);
    return { phase: typeof item.phase === "string" ? item.phase : `#${index + 1}`, system: message(item.system, meta, "system"), user: message(item.user, meta, "user") };
  };
  if (template && root.prompt_templates) {
    const calls = new Map<string, Record<string, unknown>>();
    for (const [key, text] of Object.entries(record(root.prompt_templates))) {
      const match = key.match(/^(?:(.+)_)?(system|user)$/); if (!match) continue;
      const phase = match[1] ?? "decision";
      const item = calls.get(phase) ?? { phase }; item[match[2]] = text; calls.set(phase, item);
    }
    return [...calls.values()].map(call);
  }
  if (Array.isArray(root.calls)) return root.calls.map(call).filter(item => item.system || item.user);
  const direct = call(root, 0);
  if (direct.system || direct.user) return [direct];
  const base = call({ phase: "base", system: root.base_system, user: root.base_user }, 0);
  if (base.system || base.user) return [base];
  return typeof value === "string" ? [{ phase: "prompt", user: message(value, {}, "user") }] : [];
}

export function sliceParts(parts: PromptPart[], start: number, end = Infinity): PromptPart[] {
  let offset = 0; const result = [];
  for (const part of parts) {
    const next = offset + part.text.length;
    if (next > start && offset < end) result.push({ ...part, text: part.text.slice(Math.max(0, start - offset), Math.min(part.text.length, end - offset)) });
    offset = next;
  }
  return result;
}

export function promptLines(parts: PromptPart[]): PromptPart[][] {
  const lines: PromptPart[][] = [[]];
  for (const part of parts) part.text.split("\n").forEach((text, index) => {
    if (index) lines.push([]);
    if (text) lines[lines.length - 1].push({ ...part, text });
  });
  return lines;
}
