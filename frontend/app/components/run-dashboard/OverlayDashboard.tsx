import { useLocale } from "../../lib/locale";
import { MetricValue, pluck } from "./cards";

export interface OverlayDashboardProps { familySpecific: unknown; generic?: unknown }

export default function OverlayDashboard({ familySpecific, generic }: OverlayDashboardProps) {
  const {zh, ja } = useLocale();
  const f = familySpecific ?? {};
  const metrics = [
    { id: "overlay-net-pnl", title: zh ? "账户总盈亏" : ja("Account total P&L"), value: pluck(generic, "total_pnl") ?? pluck(f, "generic", "total_pnl"), note: zh ? "评估器按每日盈亏累计。" : ja("Evaluator total of daily P&L."), tag: "ACCOUNT / P&L" },
    { id: "overlay-income", title: zh ? "期权权利金收入（毛）" : ja("Option premium income (gross)"), value: pluck(f, "overlay_income_total") ?? pluck(f, "overlay_income"), note: zh ? "卖出期权收到的毛权利金；未扣买回成本与手续费，不等于期权净利润。" : ja("Gross premiums received on option sells; before buyback costs and fees, not net option profit."), tag: "OPTIONS / GROSS CREDIT" },
    { id: "overlay-drift", title: zh ? "扣除毛权利金后的盈亏（代理值）" : ja("P&L less gross premium (proxy)"), value: pluck(f, "underlying_drift_pnl") ?? pluck(f, "underlying_drift"), note: zh ? "账户总盈亏 − 毛权利金收入。并非单独统计的股票收益。" : ja("Account total P&L − gross premium income. Not a separately measured stock return."), tag: "PROXY / NON-ADDITIVE" },
  ];
  return <div data-testid="overlay-dashboard" className="grid grid-cols-1 gap-4 lg:grid-cols-3">{metrics.map(item => <article key={item.id} data-testid={item.id} className="lo-panel flex min-w-0 flex-col p-5"><p className="lo-eyebrow">{item.tag}</p><h3 className="mt-3 text-sm font-medium">{item.title}</h3><div className="mt-3 break-words text-2xl font-semibold tabular-nums"><MetricValue value={item.value} format="money" /></div><p className="mt-4 text-xs leading-5 text-[var(--color-ink-soft)]">{item.note}</p></article>)}</div>;
}
