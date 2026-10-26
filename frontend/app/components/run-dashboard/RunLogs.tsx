import { useCallback, useEffect, useState } from "react";
import { useLocale } from "../../lib/locale";

type LogPage = { available: boolean; text: string; next_before: number | null; start?: number; end?: number; total_bytes?: number; exit_code?: number; error_message?: string; long_line_omitted?: boolean };
export default function RunLogs({ runId, status }: { runId: string; status?: string | null }) {
  const {zh, ja } = useLocale();
  const [page, setPage] = useState<LogPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [latest, setLatest] = useState(true);
  const read = useCallback(async (before?: number) => {
    setLoading(true);
    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(runId)}/logs${before === undefined ? "" : `?before=${before}`}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setPage(await response.json()); setError(""); setLatest(before === undefined);
    } catch (cause) { setError(String(cause)); } finally { setLoading(false); }
  }, [runId]);
  useEffect(() => { setPage(null); read(); }, [read]);
  useEffect(() => {
    if (!latest || !["pending", "running"].includes(status ?? "")) return;
    const timer = window.setInterval(() => read(), 5000);
    return () => window.clearInterval(timer);
  }, [read, status, latest]);
  return <section className="lo-panel space-y-4 p-5" data-testid="run-logs">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">{zh ? "运行日志" : ja("Run logs")}</h2><div className="flex gap-3 text-sm"><button type="button" disabled={loading} onClick={() => read()} className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "查看最新日志" : ja("Latest logs")}</button>{page?.available && <a href={`/api/tasks/${encodeURIComponent(runId)}/logs?download=true`} download className="rounded border border-[var(--color-rule)] px-3 py-2">{zh ? "下载完整日志" : ja("Download full log")}</a>}</div></div>
    <p className="text-xs text-[var(--color-ink-soft)]">{zh ? "任务进程的 stdout / stderr 合并日志；已过滤服务端已知的密钥。运行中每 5 秒刷新最新页，查看旧页时暂停刷新。日志时间沿用原始记录的时区。" : ja("Combined worker stdout / stderr, with known server credentials redacted. The latest page refreshes every 5 seconds while running; older pages pause refresh. Timestamps retain their original timezone.")}</p>
    {page?.error_message && <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800"><p className="font-medium">{zh ? "最近错误" : ja("Latest error")}{page.exit_code != null ? ` · exit ${page.exit_code}` : ""}</p><p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">{page.error_message}</p></div>}
    {error && <p role="alert" className="text-sm text-red-700">{zh ? "无法读取日志" : ja("Could not read logs")}: {error}</p>}
    {!page && !error && <p>{zh ? "正在读取日志…" : ja("Loading logs…")}</p>}
    {page && !page.available && <p className="rounded border border-dashed border-[var(--color-rule)] p-5 text-sm">{zh ? "尚未生成进程日志，或旧任务未保留该文件。可在「进度与盈亏」查看已保存的事件。" : ja("No worker log has been generated, or this legacy run did not retain it. Saved events remain under Progress & P&L.")}</p>}
    {page?.available && <><div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--color-ink-soft)]"><span>{zh ? "字节范围" : ja("Byte range")}: {page.start}–{page.end} / {page.total_bytes}</span><button type="button" disabled={loading || page.next_before === null} onClick={() => { if (page.next_before !== null) read(page.next_before); }} className="rounded border border-[var(--color-rule)] px-3 py-2 disabled:opacity-40">{zh ? "查看更早日志" : ja("Older logs")}</button></div><pre aria-label={zh ? "任务日志内容" : ja("Worker log content")} tabIndex={0} className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-100">{page.text || (zh ? "日志暂时为空。" : ja("Log is empty."))}</pre>{page.long_line_omitted && <p className="text-xs text-amber-700">{zh ? "超长日志行未在此页展示，请下载完整日志。" : ja("An unusually long line was omitted; download the full log to inspect it.")}</p>}</>}
  </section>;
}
