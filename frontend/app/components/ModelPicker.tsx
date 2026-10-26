import { useLocale } from "../lib/locale";
import { useEffect, useRef, useState } from "react";

export type PickerModel = { id: string; label: string; group_id: string; api_model?: string; available?: boolean };
export type PickerGroup = { id: string; label: string; has_api_key?: boolean; models_updated_at?: string | null };
export function discoveryMessage(code: string, zh: boolean, ja: (text: string) => string = (text) => text) {
  const hints: Record<string, [string, string]> = {
    api_key_required: ["请在 API 设置中填写密钥。", "Add an API key under API settings."],
    models_endpoint_http_401: ["模型接口拒绝了密钥，请检查 API 组的认证信息。", "The model endpoint rejected the key. Check this API group's credentials."],
    models_endpoint_http_403: ["当前密钥无权读取模型列表。", "This key cannot list models."],
    models_endpoint_http_404: ["未找到 /models 接口，请检查协议和 Base URL；也可手动添加模型。", "No /models endpoint was found. Check the protocol and Base URL, or add a model manually."],
    models_endpoint_http_429: ["模型列表接口限流，请稍后刷新。", "Model discovery is rate limited. Try again later."],
    models_endpoint_unreachable: ["模型列表接口暂时无法连接。已保存的模型列表仍保留。", "The model endpoint is unreachable. The saved catalog is retained."],
  };
  return ja(hints[code]?.[zh ? 0 : 1] ?? `${zh ? "无法获取模型列表" : ja("Model discovery failed")}: ${code}`);
}
export default function ModelPicker({ models, groups, value, onChange, onRegistry, onBusy, zh }: {
  models: PickerModel[]; groups: PickerGroup[]; value: string; onChange: (id: string) => void;
  onRegistry: (value: { models: PickerModel[]; groups: PickerGroup[] }) => void; onBusy: (busy: boolean) => void; zh: boolean;
}) {
  const { ja } = useLocale();
  const [groupId, setGroupId] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const attempted = useRef(new Set<string>());
  const current = models.find((m) => m.id === value);
  const selectedGroup = groups.find((g) => g.id === groupId);
  useEffect(() => {
    if (!groups.some((g) => g.id === groupId)) setGroupId(current?.group_id ?? groups.find((g) => g.has_api_key)?.id ?? groups[0]?.id ?? "");
  }, [groups, groupId, current?.group_id]);
  async function refresh(id = groupId) {
    if (!id || busy) return;
    attempted.current.add(id); setBusy(true); onBusy(true); setError("");
    try {
      const response = await fetch(`/api/model-settings/${encodeURIComponent(id)}/models/refresh`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
      onRegistry(body);
      const available = (body.models as PickerModel[]).filter((model) => model.group_id === id && model.available !== false);
      if (!available.some((model) => model.id === value)) onChange(available[0]?.id ?? "");
    } catch (cause) { setError(discoveryMessage(cause instanceof Error ? cause.message : String(cause), zh, ja)); }
    finally { setBusy(false); onBusy(false); }
  }
  useEffect(() => {
    if (selectedGroup?.has_api_key && selectedGroup.models_updated_at === null && !attempted.current.has(groupId)) void refresh(groupId);
  }, [groupId, selectedGroup?.has_api_key, selectedGroup?.models_updated_at]);
  const available = models.filter((model) => model.group_id === groupId && model.available !== false);
  const filtered = available.filter((model) => model.id === value || `${model.label} ${model.api_model ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="space-y-3" data-testid="model-picker">
    <div className="grid gap-3 sm:grid-cols-2"><label className="lo-field"><span>{zh ? "API 组" : ja("API group")}</span><select value={groupId} disabled={busy} onChange={(event) => {
      const id = event.target.value; setGroupId(id); setQuery(""); setError("");
      onChange(models.find((m) => m.group_id === id && m.available !== false)?.id ?? "");
    }}><option value="" disabled>{zh ? "选择 API 组" : ja("Choose API group")}</option>{groups.map((group) => <option key={group.id} value={group.id} disabled={!group.has_api_key}>{group.label}{group.has_api_key ? "" : (zh ? " · 未设置密钥" : ja(" · key missing"))}</option>)}</select></label>
      <label className="lo-field"><span>{zh ? "筛选模型" : ja("Filter models")}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={zh ? "按名称搜索" : ja("Search model names")} /></label></div>
    <label className="lo-field"><span>{zh ? "运行模型" : ja("Run model")}</span><select disabled={busy} value={current?.group_id === groupId ? value : ""} onChange={(event) => onChange(event.target.value)}><option value="" disabled>{busy ? (zh ? "获取模型中…" : ja("Discovering models…")) : (zh ? "选择模型" : ja("Choose model"))}</option>{filtered.map((model) => <option key={model.id} value={model.id}>{model.label}{model.api_model && model.api_model !== model.label ? ` · ${model.api_model}` : ""}</option>)}</select></label>
    <div className="flex flex-wrap items-center gap-3 text-xs"><button type="button" disabled={busy || !selectedGroup?.has_api_key} onClick={() => void refresh()} className="rounded border border-[var(--color-rule)] px-3 py-2 disabled:opacity-40">{busy ? (zh ? "获取中…" : ja("Loading…")) : (zh ? "刷新此组模型 /models" : ja("Refresh group models /models"))}</button><span>{available.length} {zh ? "个可选模型" : ja("available models")}{selectedGroup?.models_updated_at ? ` · ${zh ? "更新于" : ja("Updated")} ${new Date(selectedGroup.models_updated_at).toLocaleString()}` : ""}</span><a href="/settings/models" className="text-[var(--color-primary-600)]">{zh ? "管理 API 组和模型 →" : ja("Manage API groups and models →")}</a></div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {!busy && !available.length && <p className="text-sm text-amber-700">{zh ? "此组暂无可选模型。刷新模型列表，或到 API 设置中添加。" : ja("No selectable models in this group. Refresh its catalog or add a model in API settings.")}</p>}
    <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "列表来自所选 API 组。交易任务需要支持文本生成的模型；接口列出模型不代表该模型一定支持当前调用协议。" : ja("Models come from the selected API group. Trading requires text generation; a listing alone does not guarantee compatibility with this group's inference protocol.")}</p>
  </div>;
}
