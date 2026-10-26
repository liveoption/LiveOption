export const greekFields = ["delta_pnl", "gamma_pnl", "theta_pnl", "vega_pnl", "vanna_pnl", "volga_pnl", "residual"] as const;
export type GreekField = typeof greekFields[number];
export const finiteMetric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
export function greekValues(value: unknown): { key: GreekField; value: number | null }[] {
  const data = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return greekFields.map(key => ({ key, value: finiteMetric(data[key]) }));
}
export function attributionSummary(value: unknown) {
  const items = greekValues(value), measured = items.filter(item => item.value !== null);
  const positive = measured.reduce((sum, item) => sum + Math.max(0, item.value!), 0);
  const negative = measured.reduce((sum, item) => sum + Math.min(0, item.value!), 0);
  return { items, measured: measured.length, positive, negative, net: measured.length ? positive + negative : null };
}
/** A shared, zero-centred scale. symlog preserves signs and true zero. */
export function attributionPosition(value: number, maxAbs: number, smallValues = false): number {
  if (!Number.isFinite(value) || !Number.isFinite(maxAbs) || maxAbs <= 0) return 0;
  const ratio = smallValues ? Math.log1p(Math.abs(value)) / Math.log1p(maxAbs) : Math.abs(value) / maxAbs;
  return Math.sign(value) * Math.min(1, ratio);
}
