\
from __future__ import annotations

import numpy as np
import pandas as pd


def max_stage_rise_pct(df: pd.DataFrame) -> float:
    """
    在时间序列中，计算“先出现低点、后出现高点”的最大阶段涨幅。
    """
    if df.empty or "close" not in df.columns:
        return np.nan
    s = pd.to_numeric(df["close"], errors="coerce").dropna()
    if s.empty:
        return np.nan
    running_min = s.cummin()
    rises = (s / running_min - 1.0) * 100.0
    return float(rises.max())


def is_continuous_half_year_downtrend(
    df: pd.DataFrame,
    max_drop_pct: float = 25.0,
    ma_window: int = 60,
) -> bool:
    """
    将“近半年一直趋势向下”转成可计算规则：
    1) 半年首尾跌幅 >= max_drop_pct；
    2) 60日均线末值低于约20个交易日前；
    3) 当前收盘价低于60日均线。
    三者同时满足时，判为持续下行。
    """
    if len(df) < max(ma_window + 20, 80):
        return False
    x = df.tail(126).copy()
    close = pd.to_numeric(x["close"], errors="coerce").dropna()
    if len(close) < ma_window + 20:
        return False

    first = float(close.iloc[0])
    last = float(close.iloc[-1])
    drop_pct = (1 - last / first) * 100 if first > 0 else 0

    ma = close.rolling(ma_window).mean()
    if pd.isna(ma.iloc[-1]) or pd.isna(ma.iloc[-21]):
        return False

    ma_down = ma.iloc[-1] < ma.iloc[-21]
    below_ma = last < ma.iloc[-1]
    return bool(drop_pct >= max_drop_pct and ma_down and below_ma)


def has_recent_limit_or_fried_board(
    df: pd.DataFrame,
    lookback: int = 30,
    touch_threshold_pct: float = 9.5,
    fried_close_threshold_pct: float = 9.3,
) -> tuple[bool, str]:
    """
    仅针对当前非ST主板普通A股，按约10%涨停制度近似识别。
    - 涨停：收盘涨幅 >= touch_threshold_pct
    - 炸板：盘中最高价相对昨收 >= touch_threshold_pct，但收盘涨幅 < fried_close_threshold_pct
    """
    if df.empty:
        return False, ""
    x = df.tail(lookback).copy()

    for _, row in x.iterrows():
        pre = float(row.get("pre_close", 0) or 0)
        high = float(row.get("high", 0) or 0)
        close = float(row.get("close", 0) or 0)
        pct = float(row.get("pct_chg", 0) or 0)
        if pre <= 0:
            continue

        intraday_pct = (high / pre - 1) * 100
        close_pct = (close / pre - 1) * 100

        if pct >= touch_threshold_pct or close_pct >= touch_threshold_pct:
            return True, "近30日涨停"
        if intraday_pct >= touch_threshold_pct and close_pct < fried_close_threshold_pct:
            return True, "近30日炸板"
    return False, ""


def is_ant_climbing_tree(df: pd.DataFrame, cfg: dict) -> bool:
    """
    “蚂蚁上树”参数化近似：
    - 近N日整体缓慢抬升，不是暴涨；
    - MA5 > MA10 > MA20；
    - 多数交易日上涨；
    - 单日波动不过大；
    - 成交量离散度不过高，偏温和堆量。
    """
    n = int(cfg.get("lookback_days", 20))
    if len(df) < max(n, 20):
        return False

    x = df.tail(n).copy()
    close = pd.to_numeric(x["close"], errors="coerce")
    pct = pd.to_numeric(x["pct_chg"], errors="coerce")
    vol = pd.to_numeric(x["vol"], errors="coerce")

    if close.isna().any() or close.iloc[0] <= 0:
        return False

    rise_pct = (close.iloc[-1] / close.iloc[0] - 1) * 100
    if not (float(cfg.get("min_rise_pct", 4)) <= rise_pct <= float(cfg.get("max_rise_pct", 25))):
        return False

    full = pd.to_numeric(df["close"], errors="coerce")
    ma5 = full.rolling(5).mean().iloc[-1]
    ma10 = full.rolling(10).mean().iloc[-1]
    ma20 = full.rolling(20).mean().iloc[-1]
    if any(pd.isna(v) for v in [ma5, ma10, ma20]) or not (ma5 > ma10 > ma20):
        return False

    positive_ratio = float((pct > 0).mean())
    if positive_ratio < float(cfg.get("min_positive_day_ratio", 0.55)):
        return False

    if float(pct.abs().max()) > float(cfg.get("max_single_day_abs_pct", 6)):
        return False

    mean_vol = float(vol.mean()) if len(vol) else 0
    volume_cv = float(vol.std(ddof=0) / mean_vol) if mean_vol > 0 else 999
    if volume_cv > float(cfg.get("max_volume_cv", 0.80)):
        return False

    return True


def is_bottom_huge_turnover(
    df: pd.DataFrame,
    latest_daily_basic: pd.Series | None,
    cfg: dict,
) -> bool:
    """
    底部巨量换手：
    - 当前价格距离一年低点较近；
    - 最近1日成交量 >= 近20日中位数的指定倍数；
    - 最新换手率 >= 最低阈值。
    """
    if len(df) < 20 or latest_daily_basic is None:
        return False

    one_year = df.tail(250).copy()
    close = pd.to_numeric(one_year["close"], errors="coerce")
    if close.dropna().empty:
        return False

    current = float(close.iloc[-1])
    low_1y = float(close.min())
    if low_1y <= 0:
        return False

    dist_pct = (current / low_1y - 1) * 100
    if dist_pct > float(cfg.get("price_to_one_year_low_max_pct", 20)):
        return False

    vol20 = pd.to_numeric(df.tail(int(cfg.get("lookback_days", 20)))["vol"], errors="coerce")
    median_vol = float(vol20.median())
    latest_vol = float(vol20.iloc[-1])
    if median_vol <= 0:
        return False

    vol_multiple = latest_vol / median_vol
    if vol_multiple < float(cfg.get("turnover_multiple_vs_20d_median", 2.0)):
        return False

    turnover_rate = latest_daily_basic.get("turnover_rate", np.nan)
    try:
        turnover_rate = float(turnover_rate)
    except Exception:
        return False

    return turnover_rate >= float(cfg.get("turnover_rate_min_pct", 3.0))
