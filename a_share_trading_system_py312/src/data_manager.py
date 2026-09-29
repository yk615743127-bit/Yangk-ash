\
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import DAILY_DIR, load_settings
from .tushare_client import TushareClient
from .utils import atomic_write_csv, normalize_trade_date, today_ymd, two_year_start_ymd


class DailyDataManager:
    def __init__(self, client: TushareClient):
        self.client = client
        self.settings = load_settings()
        self.history_years = int(self.settings.get("history_years", 2))

    def stock_path(self, ts_code: str) -> Path:
        return DAILY_DIR / f"{ts_code.replace('.', '_')}.csv"

    def _trim_to_window(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        cutoff = (datetime.now() - timedelta(days=int(365.25 * self.history_years) + 45)).strftime("%Y%m%d")
        return df[df["trade_date"].astype(str) >= cutoff].copy()

    def update_one(self, ts_code: str, end_date: str | None = None) -> pd.DataFrame:
        end_date = end_date or today_ymd()
        path = self.stock_path(ts_code)

        old = pd.DataFrame()
        if path.exists():
            old = pd.read_csv(path, dtype={"trade_date": str})
            old = normalize_trade_date(old)

        if old.empty:
            start_date = two_year_start_ymd(self.history_years)
        else:
            last_date = pd.to_datetime(old["trade_date"].max(), format="%Y%m%d")
            start_date = (last_date + timedelta(days=1)).strftime("%Y%m%d")

        if start_date <= end_date:
            new = self.client.daily(ts_code, start_date, end_date)
        else:
            new = pd.DataFrame()

        if not new.empty:
            new["trade_date"] = new["trade_date"].astype(str)

        merged = pd.concat([old, new], ignore_index=True) if not old.empty else new
        if merged.empty:
            return merged

        merged = normalize_trade_date(merged)
        merged = self._trim_to_window(merged)
        atomic_write_csv(merged, path)
        return merged

    def load_one(self, ts_code: str) -> pd.DataFrame:
        path = self.stock_path(ts_code)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path, dtype={"trade_date": str})
        return normalize_trade_date(df)
