import { useLocale } from "../lib/locale";
import { useEffect, useRef, useState } from "react";
import { PromptEditor, PromptTokens } from "./PromptText";
import PromptDialog from "./PromptDialog";
import { templateParts } from "../lib/prompts";

export type PromptVariable = { name: string; description: string; description_en?: string; source: string; source_en?: string; example: string };
export type PromptBase = { id: string; prompt_templates: Record<string, string>; prompt_variables?: Record<string, PromptVariable[]> };
const requiredFields: Record<string, string> = { user: "chain_sample_table", analyzer_user: "CHAIN_CONTEXT", executor_user: "TRADABLE_CONTRACTS", pre_user: "CHAIN_CONTEXT", post_user: "CHAIN_CONTEXT" };
const marker = /(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]+)?\}(?!\})/g;
const labels: Record<string, [string, string]> = {
  system: ["System · 系统指令", "System"], user: ["User · 决策输入", "User"],
  analyzer_system: ["分析 · 系统指令", "Analysis · system"], analyzer_user: ["分析 · 决策输入", "Analysis · user"],
  executor_system: ["执行 · 系统指令", "Execution · system"], executor_user: ["执行 · 决策输入", "Execution · user"],
  pre_system: ["财报前 · 系统指令", "Pre-earnings · system"], pre_user: ["财报前 · 决策输入", "Pre-earnings · user"],
  post_system: ["财报后 · 系统指令", "Post-earnings · system"], post_user: ["财报后 · 决策输入", "Post-earnings · user"],
};

export function promptProblem(stage: string, text: string, variables: PromptVariable[] | undefined, zh: boolean, ja: (text: string) => string = (text) => text): string {
  if (!text.trim()) return zh ? "请填写 Prompt，可参考本类别的示例。" : ja("Enter a prompt or use the example.");
  const fields = [...text.matchAll(marker)].map((match) => match[1]);
  const allowed = new Set(variables?.map((variable) => variable.name));
  const extra = [...new Set(fields.filter((name) => variables && !allowed.has(name)))];
  if (extra.length) return `${zh ? "不支持的变量" : ja("Unsupported variables")}: ${extra.map((name) => `{${name}}`).join(", ")}`;
  const required = requiredFields[stage];
  return required && !fields.includes(required) ? `${zh ? "缺少必需的行情变量" : ja("Required market variable missing")}: {${required}}` : "";
}

export default function PromptSettings({ zh, base, custom, context, onContext, templates, edits, onTemplate, onEdit, addenda, onAddendum, requestedStage }: {
  zh: boolean; base: PromptBase; custom: boolean; context: string; onContext: (value: string) => void;
  templates: Record<string, string>; edits: Record<string, boolean>; onTemplate: (stage: string, value: string) => void;
  onEdit: (stage: string, value: boolean) => void; addenda: Record<string, string>; onAddendum: (stage: string, value: string) => void;
  requestedStage?: string;
}) {
  const { ja } = useLocale();
  const stages = Object.keys(base.prompt_templates);
  const [stage, setStage] = useState(stages[0]);
  const [search, setSearch] = useState("");
  const [viewer, setViewer] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { setStage(Object.keys(base.prompt_templates)[0]); setSearch(""); }, [base.id]);
  useEffect(() => { if (requestedStage) setStage(requestedStage); }, [requestedStage]);
  const active = stages.includes(stage) ? stage : stages[0];
  const variables = base.prompt_variables?.[active] ?? [];
  const text = templates[active] ?? "";
  const editable = custom || edits[active];
  const problem = editable ? promptProblem(active, text, base.prompt_variables?.[active], zh, ja) : "";
  const samples = new Map(variables.map((variable) => [variable.name, variable.example]));
  const example = templateParts(text, samples).map(part => ({ ...part, text: part.text.replaceAll("{{", "{").replaceAll("}}", "}") }));
  const supported = base.prompt_variables?.[active] ? new Set(variables.map(variable => variable.name)) : undefined;
  function insert(name: string) {
    const start = editor.current?.selectionStart ?? text.length;
    const end = editor.current?.selectionEnd ?? start;
    const token = `{${name}}`;
    onTemplate(active, text.slice(0, start) + token + text.slice(end));
    requestAnimationFrame(() => { editor.current?.focus(); editor.current?.setSelectionRange(start + token.length, start + token.length); });
  }
  return <section className="lo-panel space-y-5 p-5 sm:p-6" data-testid="prompt-settings">
    <div><h2 className="text-xl font-semibold">{zh ? "Agent 指令" : ja("Agent instructions")}</h2><p className="mt-2 text-sm text-[var(--color-ink-soft)]">{custom ? (zh ? "已提供基础示例。直接修改 System 和 User，定义自己的交易逻辑。" : ja("Basic examples are ready. Edit System and User to define your own trading logic.")) : (zh ? "可以沿用预设指令，也可以逐项修改。" : ja("Keep the preset instructions or edit each prompt."))}</p></div>
    <label className="lo-field"><span>{zh ? "额外交易偏好（可选）" : ja("Extra trading preferences (optional)")}</span><textarea aria-label="Trading context" rows={3} maxLength={16000} value={context} onChange={(event) => onContext(event.target.value)} placeholder={zh ? "例如：证据不一致时持有现金；只交易流动性充足的期权。" : ja("Example: Hold cash when signals conflict; trade liquid options only.")} /></label>
    <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "留空即可。填写后会附加到 User Prompt，下方指令仍然生效。" : ja("Optional. Text here is appended to the User prompt.")}</p>
    <nav aria-label={zh ? "Prompt 阶段" : ja("Prompt stage")} className="flex flex-wrap gap-2 border-b border-[var(--color-rule)] pb-3">{stages.map((item) => <button type="button" key={item} aria-pressed={item === active} onClick={() => { setStage(item); setSearch(""); }} className={`rounded-lg px-3 py-2 text-sm ${item === active ? "bg-[var(--color-primary-600)] text-white" : "border border-[var(--color-rule)]"}`}>{ja(labels[item]?.[zh ? 0 : 1] ?? item)}</button>)}</nav>
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      {custom ? <span className="text-[var(--color-ink-soft)]">{zh ? "示例可自由改写" : ja("Edit the example freely")}</span> : <div className="flex gap-4"><label className="flex items-center gap-2"><input type="radio" name={`template-${active}`} checked={!edits[active]} onChange={() => { onEdit(active, false); onTemplate(active, base.prompt_templates[active]); }} />{zh ? "沿用默认" : ja("Use default")}</label><label className="flex items-center gap-2"><input type="radio" name={`template-${active}`} checked={Boolean(edits[active])} onChange={() => onEdit(active, true)} />{zh ? "修改指令" : ja("Edit prompt")}</label></div>}
      {editable && <button type="button" className="text-[var(--color-primary-600)]" onClick={() => onTemplate(active, base.prompt_templates[active])}>{zh ? (custom ? "恢复示例" : "恢复默认文本") : ja("Restore example")}</button>}
      <button type="button" onClick={() => setViewer(true)} className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "放大查看模板" : ja("Open template viewer")}</button>
    </div>
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_290px]">
      <div className="min-w-0">{editable ? <PromptEditor inputRef={editor} label={`${active} prompt`} value={text} onChange={(value) => onTemplate(active, value)} supported={supported} /> : <pre aria-label={`${active} prompt`} className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--color-rule)] p-4 font-mono text-sm"><PromptTokens parts={templateParts(text)} supported={supported} /></pre>}
        {problem && <p role="alert" className="mt-2 text-sm text-red-700">{problem}</p>}
        <details className="mt-3 rounded-lg border border-[var(--color-rule)] p-3"><summary className="cursor-pointer text-sm">{zh ? "示例预览" : ja("Preview with illustrative values")}</summary><p className="mt-2 text-xs text-[var(--color-ink-soft)]">{zh ? "仅用于理解模板。实际值由每次决策时可见的数据填入。" : ja("Illustrative only. Replay fills variables with data available at each decision.")}</p><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs"><PromptTokens parts={example} /></pre></details>
      </div>
      <aside className="min-w-0 rounded-lg border border-[var(--color-rule)] p-3"><h3 className="text-sm font-medium">{zh ? `可用变量 (${variables.length})` : ja("Supported variables ({0})", variables.length)}</h3><p className="mt-2 text-xs text-[var(--color-ink-soft)]">{zh ? "{变量} 会在运行时替换。点击插入可添加到光标位置。:,.2f 表示千分位和两位小数。" : ja("Variables are filled at runtime. Insert adds one at the cursor. :,.2f formats numbers with commas and two decimals.")}</p>
        {requiredFields[active] && <p className="mt-2 break-all text-xs text-amber-700">{zh ? "必须保留" : ja("Required")}: {`{${requiredFields[active]}}`}</p>}
        {variables.length > 5 && <input type="search" aria-label={zh ? "搜索变量" : ja("Search variables")} className="mt-3" placeholder={zh ? "搜索变量" : ja("Search variables")} value={search} onChange={(event) => setSearch(event.target.value)} />}
        <div className="mt-3 max-h-96 space-y-2 overflow-auto">{variables.filter((variable) => `${variable.name} ${variable.description} ${variable.description_en}`.toLowerCase().includes(search.toLowerCase())).map((variable) => <div key={variable.name} className="rounded border border-[var(--color-rule)] p-2 text-xs"><div className="flex items-start justify-between gap-2"><code className="break-all">{`{${variable.name}}`}</code>{editable && <button type="button" aria-label={`${zh ? "插入" : ja("Insert")} {${variable.name}}`} className="text-[var(--color-primary-600)]" onClick={() => insert(variable.name)}>{zh ? "插入" : ja("Insert")}</button>}</div><p className="mt-1">{zh ? variable.description : ja(variable.description_en ?? variable.description)}</p><p className="mt-1 text-[var(--color-ink-soft)]">{zh ? "来源" : ja("Source")}: {zh ? variable.source : ja(variable.source_en ?? variable.source)}</p><p className="mt-1 break-words text-[var(--color-ink-soft)]">{zh ? "示例" : ja("Example")}: {variable.example}</p></div>)}</div>
        {!variables.length && <p className="mt-3 text-xs">{zh ? "本阶段为纯文本，没有运行时变量。" : ja("This stage has no runtime variables.")}</p>}
      </aside>
    </div>
    <details className="rounded-lg border border-[var(--color-rule)] p-3"><summary className="cursor-pointer text-sm">{zh ? "本阶段追加指令（可选）" : ja("Additional stage instructions (optional)")}</summary><textarea aria-label={`${active} addendum`} className="mt-3 w-full" rows={3} maxLength={4000} value={addenda[active] ?? ""} onChange={(event) => onAddendum(active, event.target.value)} /></details>
    {viewer && <PromptDialog prompt={{ prompt_templates: templates }} template title="Prompt templates" initialRole={active.endsWith("system") ? "system" : "user"} initialPhase={active.replace(/_?(system|user)$/, "") || "decision"} onClose={() => setViewer(false)} />}
  </section>;
}
