\
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .config import load_settings
from .patterns import (
    has_recent_limit_or_fried_board,
    is_ant_climbing_tree,
    is_bottom_huge_turnover,
    is_continuous_half_year_downtrend,
    max_stage_rise_pct,
)


MAIN_BOARD_PREFIXES = (
    "600", "601", "603", "605",  # 沪市主板
    "000", "001", "002",        # 深市主板（含原中小板）
)


def is_main_board(ts_code: str) -> bool:
    symbol = ts_code.split(".")[0]
    return symbol.startswith(MAIN_BOARD_PREFIXES)


def is_non_st(name: str) -> bool:
    s = str(name).upper()
    return "ST" not in s and "退" not in s


@dataclass
class ScreenResult:
    passed: bool
    row: dict


class TradingSystemScreener:
    def __init__(self):
        self.settings = load_settings()
        self.scfg = self.settings["screen"]
        self.pcfg = self.settings["patterns"]

    def screen_one(
        self,
        basic_row: pd.Series,
        daily_df: pd.DataFrame,
        latest_daily_basic: pd.Series | None,
    ) -> ScreenResult:
        ts_code = str(basic_row["ts_code"])
        name = str(basic_row["name"])

        result = {
            "ts_code": ts_code,
            "name": name,
            "industry": basic_row.get("industry", ""),
            "market": basic_row.get("market", ""),
            "pass_main_board": False,
            "pass_non_st": False,
            "market_cap_yi": np.nan,
            "pass_market_cap": False,
            "max_stage_rise_1y_pct": np.nan,
            "pass_no_40pct_stage_rise": False,
            "current_vs_1y_low_pct": np.nan,
            "pass_near_1y_low": False,
            "half_year_continuous_downtrend": False,
            "pass_not_continuous_downtrend": False,
            "pattern_limit_or_fried": False,
            "pattern_ant_tree": False,
            "pattern_bottom_huge_turnover": False,
            "pattern_reason": "",
            "passed": False,
        }

        result["pass_main_board"] = is_main_board(ts_code)
        result["pass_non_st"] = is_non_st(name)
        if not result["pass_main_board"] or not result["pass_non_st"]:
            return ScreenResult(False, result)

        if daily_df.empty or len(daily_df) < 120 or latest_daily_basic is None:
            return ScreenResult(False, result)

        total_mv = latest_daily_basic.get("total_mv", np.nan)
        try:
            # Tushare total_mv 单位：万元；1亿元 = 10000万元
            market_cap_yi = float(total_mv) / 10000.0
        except Exception:
            market_cap_yi = np.nan

        result["market_cap_yi"] = round(market_cap_yi, 2) if not np.isnan(market_cap_yi) else np.nan
        result["pass_market_cap"] = (
            float(self.scfg["market_cap_min_yi"]) <= market_cap_yi <= float(self.scfg["market_cap_max_yi"])
            if not np.isnan(market_cap_yi)
            else False
        )
        if not result["pass_market_cap"]:
            return ScreenResult(False, result)

        one_year = daily_df.tail(250).copy()
        rise = max_stage_rise_pct(one_year)
        result["max_stage_rise_1y_pct"] = round(rise, 2) if not np.isnan(rise) else np.nan
        result["pass_no_40pct_stage_rise"] = (
            rise <= float(self.scfg["one_year_stage_rise_max_pct"])
            if not np.isnan(rise)
            else False
        )
        if not result["pass_no_40pct_stage_rise"]:
            return ScreenResult(False, result)

        closes = pd.to_numeric(one_year["close"], errors="coerce").dropna()
        if closes.empty:
            return ScreenResult(False, result)

        current = float(closes.iloc[-1])
        low_1y = float(closes.min())
        dist = (current / low_1y - 1.0) * 100.0 if low_1y > 0 else np.inf
        result["current_vs_1y_low_pct"] = round(dist, 2)
        result["pass_near_1y_low"] = dist <= float(self.scfg["current_to_one_year_low_max_pct"])
        if not result["pass_near_1y_low"]:
            return ScreenResult(False, result)

        downtrend = is_continuous_half_year_downtrend(
            daily_df,
            max_drop_pct=float(self.scfg["half_year_downtrend_max_drop_pct"]),
            ma_window=int(self.scfg["half_year_downtrend_ma_window"]),
        )
        result["half_year_continuous_downtrend"] = downtrend
        result["pass_not_continuous_downtrend"] = not downtrend
        if downtrend:
            return ScreenResult(False, result)

        p1, p1_reason = has_recent_limit_or_fried_board(
            daily_df,
            lookback=int(self.scfg["recent_pattern_days"]),
            touch_threshold_pct=float(self.pcfg["limit_touch_threshold_pct"]),
            fried_close_threshold_pct=float(self.pcfg["fried_board_close_threshold_pct"]),
        )
        p2 = is_ant_climbing_tree(daily_df, self.pcfg["ant_tree"])
        p3 = is_bottom_huge_turnover(daily_df, latest_daily_basic, self.pcfg["bottom_huge_turnover"])

        result["pattern_limit_or_fried"] = p1
        result["pattern_ant_tree"] = p2
        result["pattern_bottom_huge_turnover"] = p3

        reasons = []
        if p1:
            reasons.append(p1_reason)
        if p2:
            reasons.append("蚂蚁上树")
        if p3:
            reasons.append("底部巨量换手")
        result["pattern_reason"] = " / ".join(reasons)

        result["passed"] = bool(p1 or p2 or p3)
        return ScreenResult(result["passed"], result)
