import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { requireAuth } from "../../lib/auth-guard";
import { useLocale } from "../../lib/locale";
import { discoveryMessage } from "../../components/ModelPicker";

export const Route = createFileRoute("/settings/models")({
  loader: async (ctx) => { await requireAuth(ctx); },
  component: ModelSettingsPage,
});

type Group = { id: string; label: string; provider: string; base_url: string; api_key?: string; has_api_key?: boolean; models_updated_at?: string | null };
type Model = { id: string; label: string; group_id: string; api_model: string; available?: boolean };
type Settings = { groups: Group[]; models: Model[] };
const providers = ["anthropic", "openai_compatible", "openai", "gemini", "deepseek", "qwen"];

function ModelSettingsPage() {
  const {zh, ja } = useLocale();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState("");
  const [queries, setQueries] = useState<Record<string, string>>({});
  const [pages, setPages] = useState<Record<string, number>>({});
  useEffect(() => {
    fetch("/api/model-settings").then(async (response) => {
      if (!response.ok) throw new Error(await response.text());
      setSettings(await response.json() as Settings);
    }).catch((cause) => setError(String(cause)));
  }, []);
  const updateGroup = (index: number, patch: Partial<Group>) => setSettings((current) => current && ({
    ...current, groups: current.groups.map((item, i) => i === index ? { ...item, ...patch } : item),
  }));
  const updateModel = (index: number, patch: Partial<Model>) => setSettings((current) => current && ({
    ...current, models: current.models.map((item, i) => i === index ? { ...item, ...patch } : item),
  }));
  const save = async () => {
    if (!settings) return null;
    setBusy(true); setSaved(false); setError("");
    try {
      const payload = {
        groups: settings.groups.map(({ id, label, provider, base_url, api_key }) => ({ id, label, provider, base_url, api_key: api_key ?? "" })),
        models: settings.models,
      };
      const response = await fetch("/api/model-settings", {
        method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await response.text());
      const value = await response.json() as Settings;
      setSettings(value);
      setSaved(true);
      return value;
    } catch (cause) { setError(String(cause)); return null; }
    finally { setBusy(false); }
  };
  async function refresh(groupId: string) {
    setRefreshing(groupId); setError("");
    try {
      if (!await save()) return;
      const response = await fetch(`/api/model-settings/${encodeURIComponent(groupId)}/models/refresh`, { method: "POST" });
      const value = await response.json();
      if (!response.ok) throw new Error(discoveryMessage(typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`, zh));
      setSettings(value); setPages((old) => ({ ...old, [groupId]: 0 })); setSaved(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setRefreshing(""); }
  }
  return <div className="space-y-6">
    <header className="border-b border-[var(--color-rule)] pb-5">
      <p className="lo-eyebrow">LIVEOPTION / API</p>
      <h1 className="mt-2 text-3xl font-semibold">{zh ? "API 与模型设置" : ja("API & model settings")}</h1>
      <p className="mt-2 text-sm text-[var(--color-ink-soft)]">{zh ? "密钥保存在运行服务器，不会返回给浏览器或写入任务配置。留空可保留原密钥。" : ja("Keys stay on the run server. Leave a key blank to keep its current value.")}</p>
    </header>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {!settings && !error && <p>{zh ? "加载中…" : ja("Loading…")}</p>}
    {settings && <fieldset disabled={busy || Boolean(refreshing)} className="space-y-6 min-w-0">
      <section className="lo-panel p-6 space-y-4">
        <div className="flex items-center justify-between"><h2 className="text-xl font-semibold">{zh ? "API 组" : ja("API groups")}</h2>
          <button type="button" onClick={() => setSettings({ ...settings, groups: [...settings.groups, { id: `group-${crypto.randomUUID().slice(0, 8)}`, label: zh ? "新 API 组" : ja("New API group"), provider: "anthropic", base_url: "", api_key: "" }] })}>{zh ? "+ 添加组" : ja("+ Add group")}</button></div>
        {settings.groups.map((group, index) => <div key={index} data-testid={`model-group-${group.id || index}`} className="grid gap-3 border-t border-[var(--color-rule)] pt-4 md:grid-cols-2">
          <Field label="ID"><input value={group.id} onChange={(event) => updateGroup(index, { id: event.target.value })} /></Field>
          <Field label={zh ? "名称" : ja("Label")}><input value={group.label} onChange={(event) => updateGroup(index, { label: event.target.value })} /></Field>
          <Field label={zh ? "协议" : ja("Provider protocol")}><select value={group.provider} onChange={(event) => updateGroup(index, { provider: event.target.value })}>{providers.map((provider) => <option key={provider}>{provider}</option>)}</select></Field>
          <Field label="Base URL"><input value={group.base_url} onChange={(event) => updateGroup(index, { base_url: event.target.value })} placeholder="https://api.example.com" /></Field>
          <Field label={zh ? "API 密钥" : ja("API key")}><input type="password" autoComplete="off" value={group.api_key ?? ""} onChange={(event) => updateGroup(index, { api_key: event.target.value })} placeholder={group.has_api_key ? (zh ? "已设置 · 留空保留" : ja("Set · leave blank to keep")) : (zh ? "尚未设置" : ja("Not set"))} /></Field>
          <div className="flex items-end"><button type="button" className="text-sm text-red-600" onClick={() => setSettings({ ...settings, groups: settings.groups.filter((_, i) => i !== index), models: settings.models.filter((model) => model.group_id !== group.id) })}>{zh ? "删除组" : ja("Remove group")}</button></div>
          <div className="space-y-3 rounded-xl border border-[var(--color-rule)] p-4 md:col-span-2">
            <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-medium">{zh ? "此组模型" : ja("Models in this group")} · {settings.models.filter((model) => model.group_id === group.id && model.available !== false).length}</h3><button type="button" onClick={() => void refresh(group.id)} className="rounded-lg bg-[var(--color-primary-600)] px-4 py-2 text-sm text-white">{refreshing === group.id ? (zh ? "获取中…" : ja("Loading…")) : (zh ? "保存并获取模型 /models" : ja("Save and fetch models /models"))}</button></div>
            <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "保存当前设置后，服务端使用此组密钥获取完整模型列表；启动任务时再选择运行模型。" : ja("Save current settings, then use this group's server-side key to fetch its complete catalog. Choose the run model when creating a task.")}{group.models_updated_at ? ` · ${new Date(group.models_updated_at).toLocaleString()}` : ""}</p>
            <label className="lo-field"><span>{zh ? "搜索此组模型" : ja("Search group models")}</span><input type="search" value={queries[group.id] ?? ""} onChange={(event) => { setQueries({ ...queries, [group.id]: event.target.value }); setPages({ ...pages, [group.id]: 0 }); }} /></label>
            {(() => { const items = settings.models.filter((model) => model.group_id === group.id && `${model.label} ${model.api_model}`.toLowerCase().includes((queries[group.id] ?? "").toLowerCase())); const count = Math.max(1, Math.ceil(items.length / 20)); const page = Math.min(pages[group.id] ?? 0, count - 1); return <><ul className="divide-y divide-[var(--color-rule)] text-sm">{items.slice(page * 20, page * 20 + 20).map((model) => <li key={model.id} className="flex flex-wrap items-center justify-between gap-2 py-2"><span className="break-all font-mono text-xs">{model.api_model}</span><span className={`text-xs ${model.available === false ? "text-amber-700" : "text-green-700"}`}>{model.available === false ? (zh ? "最近未被接口列出" : ja("Not listed on last refresh")) : (zh ? "可选" : ja("Selectable"))}</span></li>)}</ul>{!items.length && <p className="text-sm text-[var(--color-ink-soft)]">{zh ? "暂无模型。点击获取模型，或检查搜索条件。" : ja("No models. Fetch the catalog or check your search.")}</p>}{count > 1 && <div className="flex items-center justify-between text-xs"><button type="button" disabled={page === 0} onClick={() => setPages({ ...pages, [group.id]: page - 1 })}>{zh ? "上一页" : ja("Previous")}</button><span>{page + 1} / {count}</span><button type="button" disabled={page === count - 1} onClick={() => setPages({ ...pages, [group.id]: page + 1 })}>{zh ? "下一页" : ja("Next")}</button></div>}</>; })()}
          </div>
        </div>)}
      </section>
      <details className="lo-panel p-6 space-y-4">
        <summary className="cursor-pointer font-medium">{zh ? "高级 · 手动管理模型" : ja("Advanced · manually manage models")}</summary>
        <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "当接口不支持 /models，或需要添加模型别名时使用。通常直接获取组内模型即可。" : ja("Use this for gateways without /models or for model aliases. Normally, fetch the group's catalog above.")}</p>
        <div className="flex items-center justify-between"><h2 className="text-xl font-semibold">{zh ? "可选模型" : ja("Selectable models")}</h2>
          <button type="button" onClick={() => setSettings({ ...settings, models: [...settings.models, { id: "", label: "", group_id: settings.groups[0]?.id ?? "", api_model: "" }] })}>{zh ? "+ 添加模型" : ja("+ Add model")}</button></div>
        {settings.models.map((model, index) => <div key={index} className="grid gap-3 border-t border-[var(--color-rule)] pt-4 md:grid-cols-2">
          <Field label="ID"><input value={model.id} onChange={(event) => updateModel(index, { id: event.target.value })} /></Field>
          <Field label={zh ? "显示名称" : ja("Display name")}><input value={model.label} onChange={(event) => updateModel(index, { label: event.target.value })} /></Field>
          <Field label={zh ? "API 组" : ja("API group")}><select value={model.group_id} onChange={(event) => updateModel(index, { group_id: event.target.value })}>{settings.groups.map((group) => <option key={group.id} value={group.id}>{group.label || group.id}</option>)}</select></Field>
          <Field label={zh ? "实际 API 模型名" : ja("API model name")}><input value={model.api_model} onChange={(event) => updateModel(index, { api_model: event.target.value })} /></Field>
          <button type="button" className="text-left text-sm text-red-600" onClick={() => setSettings({ ...settings, models: settings.models.filter((_, i) => i !== index) })}>{zh ? "删除模型" : ja("Remove model")}</button>
        </div>)}
      </details>
      <div className="flex items-center gap-4"><button type="button" disabled={busy} className="rounded bg-[var(--color-primary-600)] px-5 py-2 text-white" onClick={() => void save()}>{busy ? (zh ? "保存中…" : ja("Saving…")) : (zh ? "保存设置" : ja("Save settings"))}</button>{saved && <span role="status">{zh ? "已保存" : ja("Saved")}</span>}</div>
    </fieldset>}
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="lo-field"><span>{label}</span>{children}</label>;
}
