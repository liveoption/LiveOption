import { useEffect, useMemo, useRef, useState } from "react";
import { useLocale } from "../lib/locale";
import { promptCalls, type PromptMessage } from "../lib/prompts";
import { ReadablePrompt } from "./PromptText";

export default function PromptDialog({ prompt, title, onClose, template = false, initialPhase, initialRole = "user" }: {
  prompt: unknown; title: string; onClose: () => void; template?: boolean; initialPhase?: string; initialRole?: "system" | "user";
}) {
  const { zh, ja } = useLocale();
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  const calls = useMemo(() => promptCalls(prompt, template), [prompt, template]);
  const [phase, setPhase] = useState(() => Math.max(0, calls.findIndex(call => call.phase === initialPhase)));
  const [view, setView] = useState<"readable" | "fields" | "raw">(calls.length ? "readable" : "raw");
  const [role, setRole] = useState<"system" | "user">(initialRole);
  const [highlight, setHighlight] = useState(true);
  const [copyState, setCopyState] = useState("");
  const call = calls[Math.min(phase, calls.length - 1)];
  const raw = useMemo(() => JSON.stringify(prompt, null, 2) ?? "null", [prompt]);
  const fields = (["system", "user"] as const).flatMap(role => {
    const found = new Set<string>();
    return (call?.[role]?.parts ?? []).filter(part => {
      if (!part.variable) return false;
      const key = JSON.stringify([part.variable, part.format, part.text]);
      if (found.has(key)) return false;
      found.add(key); return true;
    }).map(part => ({ ...part, role }));
  });
  useEffect(() => {
    opener.current ??= document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.showModal();
    return () => {
      document.body.style.overflow = overflow;
      // Restore after the dialog has left the native top layer. Keep the
      // original opener through StrictMode's effect setup/cleanup replay.
      requestAnimationFrame(() => { const previous = opener.current; if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); });
    };
  }, []);
  function download() {
    const url = URL.createObjectURL(new Blob([raw], { type: "application/json;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url;
    link.download = `${title.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 80) || "prompt"}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copy() {
    try { await navigator.clipboard.writeText(raw); setCopyState(zh ? "已复制" : ja("Copied")); }
    catch { setCopyState(zh ? "复制失败，请下载 JSON" : ja("Copy failed. Download JSON instead.")); }
  }
  const pane = (name: "system" | "user", message?: PromptMessage) => <section key={name} className={`${role === name ? "flex" : "hidden md:flex"} min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-[var(--color-rule)]`} data-testid={`prompt-pane-${name}`}>
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-3"><h3 className="font-semibold">{name === "system" ? "System" : "User"}</h3><span className="text-xs text-[var(--color-ink-soft)]">{message?.text.length ?? 0} {zh ? "字符" : ja("characters")}</span></header>
    <div className="min-h-0 flex-1 overflow-auto overscroll-contain p-4">
      {!message ? <p className="text-sm text-[var(--color-ink-soft)]">{zh ? "未保存这一部分" : ja("This part was not saved.")}</p> : <>
        {message.provenance === "unavailable" && <p className="mb-4 rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100">{zh ? "此记录未保存变量位置，以下显示原文，不推测哪些内容来自字段填充。" : ja("Variable locations were not saved. Original text is shown without guessing which content was filled from fields.")}</p>}
        <ReadablePrompt parts={highlight ? message.parts : [{ text: message.text }]} />
      </>}
    </div>
  </section>;
  return <dialog ref={dialog} aria-labelledby="prompt-dialog-title" onCancel={onClose} onClose={onClose} className="m-auto h-[92dvh] max-h-none w-[min(1500px,96vw)] max-w-none rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-0 text-[var(--color-ink)] shadow-2xl backdrop:bg-black/50" data-testid="prompt-dialog">
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--color-rule)] p-4"><div className="min-w-0"><p className="lo-eyebrow">PROMPT INSPECTOR</p><h2 id="prompt-dialog-title" className="break-words text-lg font-semibold">{title}</h2></div><button type="button" onClick={onClose} className="rounded border border-[var(--color-rule)] px-3 py-2 text-sm" aria-label={zh ? "关闭 Prompt 窗口" : ja("Close prompt window")}>{zh ? "关闭" : ja("Close")}</button></header>
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--color-rule)] px-4 py-3">
        <nav className="flex flex-wrap gap-2" aria-label={zh ? "Prompt 查看方式" : ja("Prompt view")} role="tablist">{(["readable", "fields", "raw"] as const).map(id => <button type="button" role="tab" aria-selected={view === id} key={id} onClick={() => setView(id)} className={`rounded px-3 py-2 text-sm ${view === id ? "bg-[var(--color-primary-600)] text-white" : "border border-[var(--color-rule)]"}`}>{id === "readable" ? (zh ? "可读 Prompt" : ja("Readable prompt")) : id === "fields" ? (zh ? "字段数据" : ja("Field data")) : "Raw JSON"}</button>)}</nav>
        <div className="flex flex-wrap items-center gap-2 text-xs"><button type="button" onClick={() => void copy()} className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "复制 JSON" : ja("Copy JSON")}</button><button type="button" onClick={download} className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "下载原始 JSON" : ja("Download raw JSON")}</button>{copyState && <span role="status">{copyState}</span>}</div>
      </div>
      {view !== "raw" && <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 px-4 py-3 text-xs">
        <label className="flex items-center gap-2">{zh ? "调用阶段" : ja("Call phase")}<select aria-label={zh ? "调用阶段" : ja("Call phase")} value={phase} onChange={(event) => setPhase(Number(event.target.value))} className="max-w-52 rounded border border-[var(--color-rule)] bg-[var(--color-paper)] p-2">{calls.map((item, index) => <option key={index} value={index}>{index + 1} · {item.phase}</option>)}</select></label>
        <span>{template ? (zh ? "蓝色为模板变量，运行时会填入实际值。" : ja("Blue marks template variables; actual values are filled at runtime.")) : (zh ? "蓝色为运行时填充数据；悬停可看变量名。" : ja("Blue marks runtime field data. Hover to see variable names."))}</span>
        {view === "readable" && <label className="flex items-center gap-2"><input type="checkbox" checked={highlight} onChange={(event) => setHighlight(event.target.checked)} />{zh ? "高亮字段" : ja("Highlight fields")}</label>}
      </div>}
      {view === "raw" ? <pre tabIndex={0} className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-[var(--color-paper-2)] p-5 font-mono text-xs" data-testid="prompt-raw-json">{raw}</pre> : view === "fields" ? <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 pb-4">
        {!fields.length && <p className="rounded border border-dashed border-[var(--color-rule)] p-5 text-sm">{zh ? "没有可用的字段标记。旧记录仍可在可读视图或 Raw JSON 中查看原文。" : ja("No field metadata is available. Original text remains in the readable and raw JSON views.")}</p>}
        {fields.map((part, index) => <details key={index} open={fields.length < 5} className="rounded-lg border border-[var(--color-rule)] p-3"><summary className="cursor-pointer break-all text-sm"><code className="rounded bg-blue-100 px-1 text-blue-950">{`{${part.variable}${part.format ? `:${part.format}` : ""}}`}</code> · {part.role} · {part.text.length} {zh ? "字符" : ja("characters")}</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-paper-2)] p-3 font-mono text-xs">{part.text || (zh ? "（空值）" : ja("(empty)"))}</pre></details>)}
      </div> : <>
        <nav className="flex shrink-0 gap-2 px-4 pb-3 md:hidden" aria-label={zh ? "Prompt 角色" : ja("Prompt role")}>{(["system", "user"] as const).map(name => <button type="button" key={name} aria-pressed={role === name} onClick={() => setRole(name)} className={`rounded border px-4 py-2 text-sm ${role === name ? "border-blue-600 bg-blue-50 text-blue-950" : "border-[var(--color-rule)]"}`}>{name === "system" ? "System" : "User"}</button>)}</nav>
        <div className="grid min-h-0 flex-1 gap-4 px-4 pb-4 md:grid-cols-2">{pane("system", call?.system)}{pane("user", call?.user)}</div>
      </>}
    </div>
  </dialog>;
}
