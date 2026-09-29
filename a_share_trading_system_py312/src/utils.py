\
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd


def ymd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def today_ymd() -> str:
    return datetime.now().strftime("%Y%m%d")


def two_year_start_ymd(years: int = 2) -> str:
    # 留少量缓冲，方便计算均线/阶段涨幅
    dt = datetime.now() - timedelta(days=int(365.25 * years) + 45)
    return ymd(dt)


def safe_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def normalize_trade_date(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    return df.sort_values("trade_date").drop_duplicates("trade_date", keep="last")


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.csv")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(path)
