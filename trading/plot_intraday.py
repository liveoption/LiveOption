"""
Plot intraday equity return curve with trade decision markers and trade log.

Usage:
    python trading/plot_intraday.py /tmp/intraday_20251231_results.json
"""

import json
import sys
from datetime import datetime, timedelta, timezone

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
import numpy as np


def parse_ts(ts_str: str) -> datetime:
    """Parse ISO timestamp to datetime, then convert UTC to ET (UTC-5)."""
    ts = ts_str.replace("+00:00", "").replace("Z", "")
    if "+" in ts[11:]:
        ts = ts[: ts.rfind("+")]
    elif ts.count("-") > 2:
        parts = ts.rsplit("-", 1)
        if ":" in parts[-1] and len(parts[-1]) <= 5:
            ts = parts[0]
    dt_utc = datetime.fromisoformat(ts)
    return dt_utc - timedelta(hours=5)


def _parse_contract(contract: str) -> dict:
    """Parse OPRA contract into components. E.g. O:SPY250106C00595000."""
    info = {"ticker": "?", "expiry": "?", "type": "?", "strike": "?"}
    if not contract.startswith("O:"):
        return info
    raw = contract[2:]  # strip "O:"
    # ticker = leading alpha chars
    i = 0
    while i < len(raw) and raw[i].isalpha():
        i += 1
    info["ticker"] = raw[:i]
    rest = raw[i:]  # e.g. "250106C00595000"
    if len(rest) >= 7:
        info["expiry"] = f"20{rest[:2]}-{rest[2:4]}-{rest[4:6]}"
        info["type"] = "Call" if rest[6] == "C" else "Put"
        strike_raw = rest[7:]
        if strike_raw.isdigit():
            info["strike"] = f"${int(strike_raw) / 1000:.0f}"
    return info


def main(results_path: str, output_path: str | None = None):
    with open(results_path) as f:
        data = json.load(f)

    equity_curve = data["equity_curve"]
    decisions = data["decisions"]
    initial_equity = 100_000.0

    # --- Parse equity curve ---
    times = [parse_ts(pt["ts"]) for pt in equity_curve]
    returns_bps = [(pt["equity"] / initial_equity - 1) * 10000 for pt in equity_curve]

    # --- Build trade log from FILL events (they have actual prices) ---
    fills = [d for d in decisions if d["action"].startswith("FILL_")]
    # Also collect BUY/SELL decisions for markers + rationale
    order_decisions = [d for d in decisions if d["action"] in ("BUY", "SELL")]

    # Build a rationale map: contract+action -> rationale (from order decisions)
    rationale_map = {}
    for d in order_decisions:
        key = (d.get("contract", ""), d["action"])
        rationale_map[key] = d.get("rationale", "")

    # --- Parse markers ---
    buys = []
    sells = []
    trade_rows = []  # for the table

    for d in order_decisions:
        t = parse_ts(d["ts"])
        idx = min(range(len(times)), key=lambda i: abs((times[i] - t).total_seconds()))
        ret_val = returns_bps[idx]
        contract = d.get("contract", "")
        cp_type = d.get("type", "?")
        rationale = d.get("rationale", "")
        entry = {"time": t, "ret": ret_val, "type": cp_type, "rationale": rationale}
        if d["action"] == "BUY":
            buys.append(entry)
        else:
            sells.append(entry)

    # Build trade log rows (pair BUY fills with SELL fills)
    open_fills = {}  # contract -> fill dict
    for f in fills:
        contract = f.get("contract", "")
        cinfo = _parse_contract(contract)
        if f["action"] == "FILL_BUY":
            open_fills[contract] = f
        elif f["action"] == "FILL_SELL" and contract in open_fills:
            buy_f = open_fills.pop(contract)
            buy_price = buy_f.get("price", 0)
            sell_price = f.get("price", 0)
            qty = buy_f.get("qty", 0)
            pnl = (sell_price - buy_price) * qty * 100
            fees = buy_f.get("fees", 0) + f.get("fees", 0)
            net_pnl = pnl - fees
            buy_rationale = rationale_map.get((contract, "BUY"), "")
            sell_rationale = rationale_map.get((contract, "SELL"), "")
            trade_rows.append({
                "entry_time": parse_ts(buy_f["ts"]).strftime("%H:%M"),
                "exit_time": parse_ts(f["ts"]).strftime("%H:%M"),
                "contract": f"{cinfo['ticker']} {cinfo['strike']} {cinfo['type']}",
                "expiry": cinfo["expiry"],
                "qty": qty,
                "entry_px": buy_price,
                "exit_px": sell_price,
                "pnl": net_pnl,
                "reason_in": buy_rationale.split(":")[0] if ":" in buy_rationale else buy_rationale,
                "reason_out": sell_rationale.split(":")[0] if ":" in sell_rationale else sell_rationale,
            })

    # --- Determine x-axis range ---
    all_trade_times = [b["time"] for b in buys] + [s["time"] for s in sells]
    if all_trade_times:
        t_min = min(all_trade_times) - timedelta(minutes=20)
        t_max = max(all_trade_times) + timedelta(minutes=20)
    else:
        t_min, t_max = times[0], times[-1]

    mask = [(t_min <= t <= t_max) for t in times]
    f_times = [t for t, m in zip(times, mask) if m]
    f_returns = [r for r, m in zip(returns_bps, mask) if m]

    # --- Collect unique tickers/contracts traded ---
    traded_contracts = set()
    for d in order_decisions:
        traded_contracts.add(d.get("contract", ""))
    underlyings = set()
    contract_descs = []
    for c in sorted(traded_contracts):
        ci = _parse_contract(c)
        underlyings.add(ci["ticker"])
        contract_descs.append(f"{ci['ticker']} {ci['strike']} {ci['type']} (exp {ci['expiry']})")

    # --- Figure layout: chart on top, trade table below ---
    has_trades = len(trade_rows) > 0
    if has_trades:
        fig = plt.figure(figsize=(14, 8))
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 1.2], hspace=0.35)
        ax = fig.add_subplot(gs[0])
        ax_table = fig.add_subplot(gs[1])
    else:
        fig, ax = plt.subplots(figsize=(14, 6))

    # --- Chart ---
    ax.plot(f_times, f_returns, color="#2196F3", linewidth=1.4, alpha=0.9)
    ax.fill_between(f_times, f_returns, 0, alpha=0.10, color="#2196F3")
    ax.axhline(y=0, color="gray", linewidth=0.5, linestyle="--", alpha=0.5)

    for b in buys:
        color = "#4CAF50" if b["type"] == "CALL" else "#FF9800"
        ax.scatter(b["time"], b["ret"], color=color, marker="^", s=100, zorder=5,
                   edgecolors="black", linewidths=0.5)

    for s in sells:
        color = "#4CAF50" if "profit" in s["rationale"].lower() else "#F44336"
        ax.scatter(s["time"], s["ret"], color=color, marker="v", s=100, zorder=5,
                   edgecolors="black", linewidths=0.5)

    # Title with context
    date_str = f_times[0].strftime("%Y-%m-%d") if f_times else "?"
    underlying_str = "/".join(sorted(underlyings)) if underlyings else "?"
    total_pnl = sum(r["pnl"] for r in trade_rows)
    ax.set_title(
        f"Intraday Option Trading \u2014 {date_str}\n"
        f"Underlying: {underlying_str} | "
        f"Strategy: 0DTE Puts, 15min interval | "
        f"Return: {returns_bps[-1]:+.1f} bps (${total_pnl:+,.0f})",
        fontsize=11, fontweight="bold", pad=12,
    )
    ax.set_xlabel("Time (ET)", fontsize=10)
    ax.set_ylabel("Return (bps)", fontsize=10)
    ax.set_xlim(t_min, t_max)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 30]))
    ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[0, 15, 30, 45]))
    plt.sca(ax)
    plt.xticks(rotation=30, ha="right")
    ax.grid(True, alpha=0.3, which="major")
    ax.grid(True, alpha=0.1, which="minor")

    legend_elements = [
        Line2D([0], [0], color="#2196F3", lw=2, label="Return"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#4CAF50", markersize=9, label="BUY Call"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor="#FF9800", markersize=9, label="BUY Put"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor="#4CAF50", markersize=9, label="SELL (Profit)"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor="#F44336", markersize=9, label="SELL (Stop Loss)"),
    ]
    ax.legend(handles=legend_elements, loc="best", fontsize=8, framealpha=0.9)

    # --- Trade table ---
    if has_trades:
        ax_table.axis("off")
        col_labels = ["Entry", "Exit", "Contract", "Qty", "Entry $", "Exit $", "P&L", "Reason In", "Reason Out"]
        cell_text = []
        cell_colors = []
        for r in trade_rows:
            pnl_str = f"${r['pnl']:+,.0f}"
            row = [
                r["entry_time"], r["exit_time"], r["contract"],
                str(r["qty"]), f"${r['entry_px']:.2f}", f"${r['exit_px']:.2f}",
                pnl_str, r["reason_in"], r["reason_out"],
            ]
            cell_text.append(row)
            row_color = ["#e8f5e9" if r["pnl"] >= 0 else "#ffebee"] * len(col_labels)
            cell_colors.append(row_color)

        table = ax_table.table(
            cellText=cell_text,
            colLabels=col_labels,
            cellColours=cell_colors,
            colColours=["#e3f2fd"] * len(col_labels),
            loc="center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.3)
        # Bold header
        for j in range(len(col_labels)):
            table[0, j].set_text_props(fontweight="bold")

    out = output_path or results_path.replace(".json", ".png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Plot saved to {out}")
    plt.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python trading/plot_intraday.py <results.json> [output.png]")
        sys.exit(1)
    results = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else None
    main(results, output)
