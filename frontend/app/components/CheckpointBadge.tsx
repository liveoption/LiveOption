/**
 * Small pill that indicates a checkpoint promotion.
 *
 * Pure / props-driven so it can be composed anywhere — notably the decision
 * timeline (`~/components/DecisionTimeline`) and the upcoming checkpoint page
 * (`/checkpoints/:ckptId`, Task 8). No routing or data-fetching is done here;
 * callers wire the optional `href` to navigate.
 */

export interface CheckpointBadgeProps {
  /**
   * Promoted checkpoint id, if any. When nullish the badge renders nothing,
   * letting callers place it unconditionally without extra guard logic.
   */
  checkpointId?: string | null;
  /** Optional link target, e.g. `/checkpoints/${id}`. */
  href?: string;
  /** Visual density. `sm` is the default; `xs` is for inline list rows. */
  size?: "xs" | "sm";
  /** Optional extra classes (merged after the base classes). */
  className?: string;
}

export default function CheckpointBadge({
  checkpointId,
  href,
  size = "sm",
  className,
}: CheckpointBadgeProps) {
  if (!checkpointId) return null;

  const sizeCls =
    size === "xs"
      ? "px-1.5 py-0.5 text-[10px]"
      : "px-2 py-0.5 text-xs";

  const base =
    `inline-flex items-center gap-1 rounded-full bg-amber-100 font-medium uppercase tracking-wide text-amber-800 dark:bg-amber-900/40 dark:text-amber-300 ${sizeCls}`;

  const cls = className ? `${base} ${className}` : base;

  const content = (
    <>
      <span aria-hidden="true">★</span>
      <span>checkpoint</span>
    </>
  );

  if (href) {
    return (
      <a
        href={href}
        data-testid="checkpoint-badge"
        data-checkpoint-id={checkpointId}
        className={`${cls} hover:bg-amber-200 dark:hover:bg-amber-900/60`}
      >
        {content}
      </a>
    );
  }

  return (
    <span
      data-testid="checkpoint-badge"
      data-checkpoint-id={checkpointId}
      className={cls}
    >
      {content}
    </span>
  );
}
