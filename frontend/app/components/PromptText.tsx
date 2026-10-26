import { Fragment, type RefObject, useEffect, useRef, useState } from "react";
import { promptLines, sliceParts, templateParts, type PromptPart } from "../lib/prompts";

export function PromptTokens({ parts, plain = false, supported, rich = false }: { parts: PromptPart[]; plain?: boolean; supported?: Set<string>; rich?: boolean }) {
  return <>{parts.map((part, index) => part.variable ? <mark key={index} data-variable={part.variable} style={plain ? { color: "transparent" } : undefined} title={`{${part.variable}${part.format ? `:${part.format}` : ""}}`} className={`rounded px-0 ${supported && !supported.has(part.variable) ? "bg-red-100 text-red-900 dark:bg-red-900/50 dark:text-red-100" : "bg-blue-100 text-blue-950 dark:bg-blue-900/60 dark:text-blue-100"}`}>{part.text}</mark> : <Fragment key={index}>{rich ? part.text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g).map((text, item) => text.startsWith("**") && text.endsWith("**") ? <strong key={item}>{text.slice(2, -2)}</strong> : text.startsWith("`") && text.endsWith("`") ? <code key={item} className="rounded bg-[var(--color-paper-2)] px-1 font-mono text-xs">{text.slice(1, -1)}</code> : text) : part.text}</Fragment>)}</>;
}

/** Real textarea editing (selection, IME and undo stay native); a synchronized
 * backdrop supplies token backgrounds without making prompt text executable. */
export function PromptEditor({ value, onChange, inputRef, label, supported }: { value: string; onChange: (value: string) => void; inputRef: RefObject<HTMLTextAreaElement | null>; label: string; supported?: Set<string> }) {
  const backdrop = useRef<HTMLPreElement>(null);
  const [size, setSize] = useState<{ width: number; height: number }>();
  const sync = () => { if (backdrop.current && inputRef.current) { backdrop.current.scrollTop = inputRef.current.scrollTop; backdrop.current.scrollLeft = inputRef.current.scrollLeft; } };
  useEffect(() => {
    const input = inputRef.current; if (!input) return;
    const resize = () => { setSize({ width: input.clientWidth, height: input.clientHeight }); sync(); };
    resize();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    observer?.observe(input);
    return () => observer?.disconnect();
  }, [inputRef]);
  useEffect(sync, [value]);
  const typography = { fontFamily: "var(--font-mono, ui-monospace, monospace)", fontSize: 13, lineHeight: "22px", padding: "9px 11px", letterSpacing: "normal", tabSize: 8, overflowWrap: "break-word" as const };
  return <div className="relative overflow-hidden rounded-lg border border-[var(--color-rule)] focus-within:ring-2 focus-within:ring-blue-300">
    <pre ref={backdrop} aria-hidden="true" data-testid="prompt-editor-highlights" className="pointer-events-none absolute left-0 top-0 m-0 overflow-hidden whitespace-pre-wrap text-transparent" style={{ ...typography, ...size, boxSizing: "border-box" }}><PromptTokens parts={templateParts(value)} plain supported={supported} />{"\n"}</pre>
    <textarea ref={inputRef} aria-label={label} className="relative block min-h-80 w-full resize-y !rounded-none !border-0 !bg-transparent !shadow-none" style={typography} maxLength={16000} spellCheck={false} value={value} onChange={(event) => onChange(event.target.value)} onScroll={sync} />
  </div>;
}

/** A small safe Markdown reader: headings, lists, fenced code and tables.
 * Raw HTML, remote images and arbitrary links are never executed or fetched. */
export function ReadablePrompt({ parts }: { parts: PromptPart[] }) {
  const lines = promptLines(parts), blocks = []; let code = false;
  const text = (line: PromptPart[]) => line.map(part => part.text).join("");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i], raw = text(line);
    if (/^\s*```/.test(raw)) { code = !code; continue; }
    if (code) { blocks.push(<pre key={i} className="overflow-x-auto whitespace-pre-wrap break-words bg-[var(--color-paper-2)] px-3 py-1 font-mono text-xs"><PromptTokens parts={line} /></pre>); continue; }
    if (!raw.trim()) { blocks.push(<div key={i} className="h-3" />); continue; }
    if (raw.trim().startsWith("|") && raw.trim().endsWith("|")) {
      const rows = [];
      while (i < lines.length && text(lines[i]).trim().startsWith("|") && text(lines[i]).trim().endsWith("|")) {
        const full = text(lines[i]);
        if (!/^\s*\|[\s|:\-]+\|\s*$/.test(full)) {
          const separators = [...full.matchAll(/\|/g)].map(match => match.index!);
          rows.push(<tr key={i} className="border-b border-[var(--color-rule)]">{separators.slice(0, -1).map((start, column) => <td key={column} className="whitespace-pre-wrap px-3 py-2 align-top"><PromptTokens rich parts={sliceParts(lines[i], start + 1, separators[column + 1])} /></td>)}</tr>);
        }
        i++;
      }
      blocks.push(<div key={`table-${i}`} className="my-2 max-w-full overflow-x-auto rounded border border-[var(--color-rule)]"><table className="w-full text-left font-mono text-xs"><tbody>{rows}</tbody></table></div>); i--; continue;
    }
    const heading = raw.match(/^\s*#{1,6}\s+/), bullet = raw.match(/^\s*(?:[-*]|\d+\.)\s+/);
    blocks.push(heading ? <h4 key={i} className="mb-2 mt-5 text-base font-semibold"><PromptTokens rich parts={sliceParts(line, heading[0].length)} /></h4> : bullet ? <div key={i} className="flex gap-2 py-0.5"><span aria-hidden>•</span><p className="min-w-0 whitespace-pre-wrap break-words"><PromptTokens rich parts={sliceParts(line, bullet[0].length)} /></p></div> : <p key={i} className="whitespace-pre-wrap break-words leading-6"><PromptTokens rich parts={line} /></p>);
  }
  return <div className="min-w-0 text-sm" data-testid="readable-prompt">{blocks}</div>;
}
