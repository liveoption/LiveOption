"""User-facing documentation for every supported runtime prompt placeholder."""
from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]+)?\}(?!\})")

# Description, source, and illustrative value. Examples are not live market data.
VARIABLES: dict[str, tuple[str, str, str]] = {
    "universe": ("本次回放的标的代码", "任务的标的设置", "SPY, QQQ"),
    "interval": ("相邻决策之间的分钟数", "策略决策频率", "15"),
    "flatten_clause": ("收盘前是否清仓及清仓时间的完整指令", "风险规则", "You MUST close all positions before 15:55 ET."),
    "dte_min": ("可交易期权最短剩余天数", "策略参数", "0"),
    "dte_max": ("可交易期权最长剩余天数", "策略参数", "7"),
    "max_positions": ("最多同时持有的仓位数", "策略参数", "5"),
    "current_time": ("当前决策时刻（美东时间）", "回放时钟", "2025-01-08 10:15"),
    "minutes_to_close": ("距当日收盘的分钟数", "回放时钟", "345"),
    "cash": ("账户可用现金，美元", "当前账户", "100,000.00"),
    "equity": ("账户总权益，美元", "当前账户", "101,250.00"),
    "daily_pnl": ("当日盈亏，美元", "当前账户", "1,250.00"),
    "daily_pnl_pct": ("当日盈亏百分比数值", "当前账户", "1.25"),
    "positions_table": ("当前期权仓位表；无仓位时显示提示", "账户持仓快照", "No open positions."),
    "price_windows_table": ("近期标的价格、涨跌幅和成交量窗口表", "截至决策时可见的行情", "Window | Time | SPY Close | SPY Chg% | SPY Vol"),
    "chain_sample_table": ("候选期权合约、DTE、Delta、IV、卖价和数据新鲜度", "截至决策时可见的期权链", "SPY250108C00590000 | DTE 0 | delta 0.45 | ask $2.10"),
    "CHAIN_CONTEXT": ("筛选后的候选期权链和行情摘要", "截至决策时可见的期权链", "SPY calls/puts: contract, bid, ask, delta, IV…"),
    "DAILY_PRICE_CONTEXT": ("标的历史日线价格摘要", "截至决策时可见的日线", "SPY: last 20 daily closes…"),
    "EARNINGS_CONTEXT": ("财报事件与预期信息", "财报事件数据", "Next earnings: 2025-02-05 after close"),
    "NEWS_CONTEXT": ("当时已发布的新闻摘要；关闭新闻时为空/提示", "News 开关与事件数据", "No recent news."),
    "PORTFOLIO_SUMMARY": ("现金、权益和持仓概况", "当前账户", "Cash $100,000; positions: none"),
    "PRICE_TREND": ("近期价格走势描述", "截至决策时可见的行情", "SPY +0.4% over 5 sessions"),
    "TIME_CONTEXT": ("本次决策的日期、时刻和交易阶段", "回放时钟", "2025-01-08 10:15 ET"),
    "MAX_CONTRACTS": ("单次可选的最大期权合约张数", "策略参数", "5"),
    "MAX_COST": ("单次交易允许的最大成本，美元", "策略参数", "2,000"),
    "ANALYSIS_JSON": ("分析阶段产生的结构化判断", "上一阶段模型输出", '{"direction":"bullish","confidence":0.6}'),
    "POSITIONS_SUMMARY": ("执行阶段可见的当前仓位摘要", "当前账户", "No open positions."),
    "TRADABLE_CONTRACTS": ("执行阶段允许下单的期权合约清单", "筛选后的期权链", "SPY250108C00590000 ask $2.10"),
    "MAX_POSITION_COST": ("财报交易的最大仓位成本，美元", "策略参数", "2,000"),
    "TICKER": ("当前财报事件对应的股票代码", "事件标的", "NVDA"),
    "CASH": ("账户可用现金，美元", "当前账户", "$100,000"),
    "DATE": ("当前回放日期", "回放时钟", "2025-02-25"),
    "DAYS_TO_EARNINGS": ("距离财报发布日期的天数", "财报日程", "1"),
    "EARNINGS_DATE": ("财报发布日期", "财报日程", "2025-02-26"),
    "EQUITY": ("账户总权益，美元", "当前账户", "$101,250"),
    "ESTIMATED_EPS": ("财报前可见的每股收益预期", "财报预期数据", "$0.82"),
    "ESTIMATED_REVENUE": ("财报前可见的收入预期", "财报预期数据", "$38.1B"),
    "HISTORICAL_REACTIONS": ("该标的历史财报后的价格反应", "财报历史数据", "Prior earnings: +3.2%, -1.8%"),
    "NEWS_SECTION": ("当时可见的相关新闻段落", "News 开关与事件数据", "Recent headlines: …"),
    "NEWS_WINDOW": ("新闻回看范围", "News 回看天数", "7 days"),
    "N_HISTORICAL": ("纳入摘要的历史财报次数", "财报历史数据", "4"),
    "PHASE_LABEL": ("财报前的决策阶段名称", "财报事件时钟", "T−1"),
    "POSITIONS_TABLE": ("当前持仓明细表", "当前账户", "No open positions."),
    "PRICE_CONTEXT": ("当前标的价格与近期走势", "截至决策时可见的行情", "NVDA $128.40; 5-day +2.1%"),
    "TIMING": ("本次决策相对财报的时间说明", "财报事件时钟", "One day before earnings"),
    "T_MINUS_1": ("财报前一交易日的日期", "财报日程", "2025-02-25"),
    "T_MINUS_2": ("财报前两交易日的日期", "财报日程", "2025-02-24"),
    "T_PLUS_1": ("财报后一交易日的日期", "财报日程", "2025-02-27"),
    "T_PLUS_2": ("财报后两交易日的日期", "财报日程", "2025-02-28"),
    "POST_DAY": ("财报后的第几个决策日", "财报事件时钟", "T+1"),
    "TIME_ET": ("当前美东时间", "回放时钟", "10:15 ET"),
    "ACTUAL_EPS": ("财报公布的实际每股收益", "已发布的财报", "$0.89"),
    "ACTUAL_REVENUE": ("财报公布的实际收入", "已发布的财报", "$39.3B"),
    "EPS_SURPRISE": ("实际 EPS 相对预期的差异", "已发布的财报", "+8.5%"),
    "REV_SURPRISE": ("实际收入相对预期的差异", "已发布的财报", "+3.1%"),
    "GAP_INFO": ("财报后开盘跳空信息", "截至决策时可见的行情", "+4.2% gap up"),
    "PREDICTION_RECAP": ("财报前模型判断的摘要", "此前已保存的模型输出", "Expected bullish move"),
}

VARIABLES_EN: dict[str, str] = {
    "universe": "Underlyings for this replay", "interval": "Minutes between decisions",
    "flatten_clause": "Full instruction for flattening positions before close",
    "dte_min": "Minimum days to expiry", "dte_max": "Maximum days to expiry",
    "max_positions": "Maximum simultaneous positions", "current_time": "Decision time in US Eastern time",
    "minutes_to_close": "Minutes remaining until market close", "cash": "Available cash in USD",
    "equity": "Total account equity in USD", "daily_pnl": "Today’s P&L in USD",
    "daily_pnl_pct": "Today’s P&L percentage", "positions_table": "Current option positions table",
    "price_windows_table": "Recent underlying prices, changes and volumes",
    "chain_sample_table": "Candidate contracts with DTE, Delta, IV, ask and data age",
    "CHAIN_CONTEXT": "Filtered option-chain and market summary",
    "DAILY_PRICE_CONTEXT": "Historical daily underlying prices",
    "EARNINGS_CONTEXT": "Earnings event and expectations",
    "NEWS_CONTEXT": "News published by the decision time",
    "PORTFOLIO_SUMMARY": "Cash, equity and positions summary",
    "PRICE_TREND": "Recent underlying price trend", "TIME_CONTEXT": "Date, time and trading phase",
    "MAX_CONTRACTS": "Maximum contracts for one trade", "MAX_COST": "Maximum trade cost in USD",
    "ANALYSIS_JSON": "Structured output from the preceding analysis stage",
    "POSITIONS_SUMMARY": "Current positions visible to the execution stage",
    "TRADABLE_CONTRACTS": "Contracts eligible for execution",
    "MAX_POSITION_COST": "Maximum earnings-position cost in USD",
    "TICKER": "Ticker of the earnings event", "CASH": "Available cash in USD",
    "DATE": "Current replay date", "DAYS_TO_EARNINGS": "Days until earnings",
    "EARNINGS_DATE": "Earnings release date", "EQUITY": "Total account equity in USD",
    "ESTIMATED_EPS": "Pre-release earnings-per-share estimate",
    "ESTIMATED_REVENUE": "Pre-release revenue estimate",
    "HISTORICAL_REACTIONS": "Past post-earnings price reactions",
    "NEWS_SECTION": "News visible by the decision time",
    "NEWS_WINDOW": "News lookback period", "N_HISTORICAL": "Number of past earnings events summarized",
    "PHASE_LABEL": "Pre-earnings decision phase",
    "POSITIONS_TABLE": "Current positions table", "PRICE_CONTEXT": "Current price and recent movement",
    "TIMING": "Decision timing relative to earnings",
    "T_MINUS_1": "One trading day before earnings", "T_MINUS_2": "Two trading days before earnings",
    "T_PLUS_1": "One trading day after earnings", "T_PLUS_2": "Two trading days after earnings",
    "POST_DAY": "Post-earnings decision day", "TIME_ET": "Current US Eastern time",
    "ACTUAL_EPS": "Reported earnings per share", "ACTUAL_REVENUE": "Reported revenue",
    "EPS_SURPRISE": "EPS difference versus estimate", "REV_SURPRISE": "Revenue difference versus estimate",
    "GAP_INFO": "Post-release opening price gap",
    "PREDICTION_RECAP": "Summary of the model's saved pre-earnings prediction",
}

SOURCE_EN = {
    "任务的标的设置": "Run underlyings", "策略决策频率": "Strategy frequency",
    "风险规则": "Risk rules", "策略参数": "Strategy settings",
    "回放时钟": "Replay clock", "当前账户": "Current account",
    "账户持仓快照": "Portfolio snapshot", "截至决策时可见的行情": "Point-in-time market data",
    "截至决策时可见的期权链": "Point-in-time option chain",
    "截至决策时可见的日线": "Point-in-time daily bars",
    "财报事件数据": "Earnings event data", "News 开关与事件数据": "News setting and event data",
    "上一阶段模型输出": "Previous model stage", "筛选后的期权链": "Filtered option chain",
    "事件标的": "Event underlying", "财报日程": "Earnings calendar",
    "财报预期数据": "Earnings estimates", "财报历史数据": "Earnings history",
    "财报事件时钟": "Earnings event clock", "已发布的财报": "Released earnings report",
    "此前已保存的模型输出": "Previously saved model output",
    "News 回看天数": "News lookback setting",
}


def variables_by_stage(templates: dict[str, str]) -> dict[str, list[dict[str, str]]]:
    """Return only supported fields for each stage, in first-use order."""
    result = {}
    for stage, template in templates.items():
        names = list(dict.fromkeys(PLACEHOLDER.findall(template)))
        missing = set(names) - VARIABLES.keys()
        if missing:
            raise ValueError(f"undocumented prompt variables: {sorted(missing)}")
        result[stage] = [
            {"name": name, "description": VARIABLES[name][0], "description_en": VARIABLES_EN[name],
             "source": VARIABLES[name][1], "source_en": SOURCE_EN[VARIABLES[name][1]], "example": VARIABLES[name][2]}
            for name in names
        ]
    return result
