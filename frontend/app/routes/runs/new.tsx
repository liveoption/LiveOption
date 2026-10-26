import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button, Card } from "@heroui/react";
import { useState } from "react";

import RunSpecEditor, { type TaskSpec } from "../../components/RunSpecEditor";
import { useLocale } from "../../lib/locale";
import { useGetMeV1MeGet } from "../../lib/orval/catalog/catalog";
import { requireAuth } from "../../lib/auth-guard";

/**
 * `/runs/new` — scenario-catalog launch page (Task 12 of the Web Run
 * Launcher plan).
 *
 * Flow:
 *   1. `GET /v1/me` → gate the form behind `can_launch`; non-allowlisted
 *      sessions see an "access required" card instead of the form.
 *   2. `LaunchForm` collects `{scenario_id, overrides}`.
 *   3. `POST /v1/runs` via the Orval `useCreateRunV1RunsPost` hook; on
 *      2xx the envelope's `data.run_id` is read and we navigate to
 *      `/runs/:runId` (which auto-tails SSE while the run is pending/running).
 *   4. RFC 7807 `application/problem+json` bodies (400 validation, 403
 *      not-allowlisted, 429 concurrency cap) bubble through `HttpError`
 *      and render via `<ProblemAlert>` above the form.
 */
export const Route = createFileRoute("/runs/new")({
  loader: async (ctx) => { await requireAuth(ctx); },
  component: NewRunPage,
});

type MeBody = { email?: string; can_launch?: boolean };

function readCanLaunch(data: unknown): boolean {
  if (!data || typeof data !== "object") return false;
  const obj = data as MeBody;
  return obj.can_launch === true;
}

function readRunId(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as { run_id?: unknown; id?: unknown };
  if (typeof obj.run_id === "string") return obj.run_id;
  if (typeof obj.id === "string") return obj.id;
  return null;
}

async function launchError(response: Response, zh: boolean, ja: (text: string) => string = (text) => text): Promise<string> {
  if (response.status === 429) return zh ? "同时运行的任务已达上限，请稍后再试。" : ja("Too many runs are active. Try again later.");
  if (response.status === 403) return zh ? "当前账号没有启动测试的权限。" : ja("This account cannot launch runs.");
  const body: unknown = await response.json().catch(() => null);
  const detail = body && typeof body === "object" && "detail" in body ? (body as { detail?: unknown }).detail : null;
  return typeof detail === "string" && detail.trim() ? detail : (zh ? "启动失败，请检查配置后重试。" : ja("Could not start the run. Check the settings and try again."));
}

function NewRunPage() {
  const navigate = useNavigate();
  const me = useGetMeV1MeGet();
  const {zh, ja } = useLocale();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (spec: TaskSpec) => {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/tasks", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(spec),
      });
      if (!response.ok) throw new Error(await launchError(response, zh, ja));
      const runId = readRunId(await response.json());
      if (!runId) throw new Error(zh ? "服务端未返回任务编号，请检查运行记录。" : ja("The server did not return a run ID. Check the runs list."));
      navigate({ to: "/runs/$runId", params: { runId } });
    } catch (cause) { setError(cause instanceof TypeError ? (zh ? "连接服务失败，请稍后重试。" : ja("Could not reach the service. Try again later.")) : cause instanceof Error ? cause.message : (zh ? "启动失败" : ja("Could not start the run"))); }
    finally { setBusy(false); }
  };

  if (me.isLoading) {
    return (
      <Card className="!rounded-2xl !border !border-[var(--color-rule)] !bg-[var(--color-paper)]">
        <Card.Content className="p-6 text-sm text-[color:var(--color-ink-soft)]">
          {zh ? "加载中…" : ja("Loading…")}
        </Card.Content>
      </Card>
    );
  }

  const canLaunch =
    me.data?.status === 200 && readCanLaunch(me.data.data);

  if (!canLaunch) {
    return (
      <Card
        className="!rounded-2xl !border !border-[var(--color-rule)] !bg-[var(--color-paper)]"
        data-testid="runs-new-denied"
      >
        <Card.Content className="flex flex-col gap-2 p-6">
          <h2 className="lo-display text-xl text-[color:var(--color-ink)]">
            {zh ? "需要访问权限" : ja("Access required")}
          </h2>
          <p className="text-sm text-[color:var(--color-ink-soft)]">
            {zh ? "当前账号没有启动测试的权限。" : ja("This account cannot launch runs.")}
          </p>
          <div className="pt-2">
            <Button
              variant="outline"
              size="sm"
              onPress={() => navigate({ to: "/" })}
            >
              {zh ? "返回首页" : ja("Back home")}
            </Button>
          </div>
        </Card.Content>
      </Card>
    );
  }

  return (
    <div className="space-y-4" data-testid="runs-new-page">
      <header className="flex flex-col gap-1 border-b border-[var(--color-rule)] pb-5">
        <p className="lo-eyebrow">OPTIONS / TRADE STUDIO</p>
        <h1 className="text-3xl font-semibold tracking-tight text-[color:var(--color-ink)]">
          {zh ? "创建期权测试" : ja("Create an options run")}
        </h1>
        <p className="text-sm text-[color:var(--color-ink-soft)]">
          {zh ? "选定测试范围、交易规则和 Agent 指令，确认后启动。" : ja("Set the scope, trading rules and agent instructions, then review and start.")}
        </p>
      </header>

      <RunSpecEditor onSubmit={(spec) => { void submit(spec); }} disabled={busy} submitError={error} />
    </div>
  );
}
