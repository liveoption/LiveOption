import { SignedIn } from "../lib/access-token-auth";
import { useGuidedTour } from "../lib/useGuidedTour";
import { useLocale } from "../lib/locale";
import { useLocation } from "@tanstack/react-router";

/**
 * GuidedTour — self-contained first-visit onboarding overlay.
 *
 * Characteristics:
 *   - Pure client-side; no network calls, no tour library.
 *   - Gated behind `<SignedIn>` so it only auto-starts for signed-in
 *     users (matches plan spec).
 *   - Mount inside `<ClientOnly>` in `__root.tsx` to avoid SSR hydration
 *     drift on localStorage access.
 *   - Completion state is persisted to localStorage by the hook; repeat
 *     visits do not re-prompt.
 */

interface TourStep {
  title: string;
  body: string;
  /** Human-readable anchor hint (e.g. "Home CTAs") shown in the header. */
  anchor: string;
}

const STEPS: TourStep[] = [
  {
    anchor: "Home",
    title: "Welcome to LiveOption",
    body:
      "Launch a new scenario or jump back into a run from the home screen. " +
      "The primary CTAs live at the top of the page.",
  },
  {
    anchor: "New run",
    title: "Start with a preset",
    body:
      "Choose a preset, select a model, then edit the dates, " +
      "context and settings you want to test.",
  },
  {
    anchor: "Run results",
    title: "Read the chart and checkpoints",
    body:
      "Open a run from the Runs list. The result sections include candles, " +
      "decision checkpoints, progress, strategy analysis and performance.",
  },
  {
    anchor: "Decision records",
    title: "Inspect what the agent saw",
    body:
      "Click a chart checkpoint or open Decision records to inspect saved " +
      "prompts, model responses, trade proposals and raw events.",
  },
  {
    anchor: "Analysis",
    title: "Compare outcomes carefully",
    body:
      "Strategy analysis shows scenario-specific metrics; Performance and risk " +
      "shows portfolio results. Missing metrics are marked as unavailable.",
  },
];
const STEPS_ZH: TourStep[] = [
  { anchor: "首页", title: "欢迎使用 LiveOption", body: "从首页新建测试场景，或返回最近的运行记录。" },
  { anchor: "新建任务", title: "从预设开始", body: "选择场景与模型，再修改日期、Context 和测试设定。" },
  { anchor: "运行结果", title: "查看 K 线与检查点", body: "从运行列表打开任务；结果侧栏包含行情、决策、进度、策略分析及收益风险。" },
  { anchor: "决策记录", title: "查看 Agent 的依据", body: "点击 K 线检查点或打开决策记录，查看保存的 Prompt、模型响应、交易意图和原始事件。" },
  { anchor: "分析", title: "理解结果与缺失项", body: "策略分析展示场景指标，收益与风险展示组合结果；无法计算的指标会明确标注。" },
];

function ProgressDots({ step, total }: { step: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5" aria-label="tour progress">
      {Array.from({ length: total }).map((_, i) => (
        <span
          key={i}
          className={
            "h-1.5 w-1.5 rounded-full transition-colors " +
            (i === step ? "bg-primary" : "bg-default-300")
          }
        />
      ))}
    </div>
  );
}

function TourOverlay() {
  const {zh, ja } = useLocale();
  const steps = zh ? STEPS_ZH : STEPS;
  const tour = useGuidedTour({ totalSteps: steps.length });
  if (!tour.active) return null;
  const original = steps[tour.step];
  const current = { anchor: ja(original.anchor), title: ja(original.title), body: ja(original.body) };
  const isLast = tour.step === steps.length - 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="guided-tour-title"
      className="fixed inset-0 z-50 flex items-end justify-center bg-[var(--color-ink)]/40 p-4 sm:items-center"
    >
      <div className="w-full max-w-md rounded-2xl bg-content1 p-6 shadow-xl ring-1 ring-default-200">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-default-500">
            {current.anchor}
          </span>
          <button
            type="button"
            onClick={tour.dismiss}
            className="text-xs text-default-500 hover:text-default-700"
            aria-label={zh ? "跳过引导" : ja("Skip tour")}
          >
            {zh ? "跳过引导" : ja("Skip tour")}
          </button>
        </div>
        <h2
          id="guided-tour-title"
          className="mb-2 text-lg font-semibold text-foreground"
        >
          {current.title}
        </h2>
        <p className="mb-6 text-sm leading-relaxed text-default-600">
          {current.body}
        </p>
        <div className="flex items-center justify-between">
          <ProgressDots step={tour.step} total={steps.length} />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={tour.prev}
              disabled={tour.step === 0}
              className="rounded-md px-3 py-1.5 text-sm text-default-600 hover:bg-default-100 disabled:opacity-40 disabled:hover:bg-transparent"
            >
              {zh ? "上一步" : ja("Back")}
            </button>
            <button
              type="button"
              onClick={tour.next}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              {isLast ? (zh ? "完成" : ja("Finish")) : (zh ? "下一步" : ja("Next"))}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function GuidedTour() {
  const pathname = useLocation({ select: (location) => location.pathname });
  return (
    <SignedIn>
      {pathname === "/" && <TourOverlay />}
    </SignedIn>
  );
}

export default GuidedTour;
