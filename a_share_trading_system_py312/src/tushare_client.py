\
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

import pandas as pd
import tushare as ts

from .config import get_tushare_token, load_settings


@dataclass
class TushareClient:
    pro: object
    sleep_seconds: float = 0.15

    @classmethod
    def create(cls) -> "TushareClient":
        settings = load_settings()
        token = get_tushare_token()
        ts.set_token(token)
        pro = ts.pro_api(token)
        return cls(
            pro=pro,
            sleep_seconds=float(
                settings.get("tushare", {}).get("sleep_seconds_between_calls", 0.15)
            ),
        )

    def _sleep(self):
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)

    def stock_basic(self) -> pd.DataFrame:
        self._sleep()
        return self.pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date"
        )

    def trade_cal(self, start_date: str, end_date: str) -> pd.DataFrame:
        self._sleep()
        return self.pro.trade_cal(
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            is_open="1",
            fields="cal_date,is_open"
        )

    def daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self._sleep()
        return self.pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields=(
                "ts_code,trade_date,open,high,low,close,pre_close,"
                "change,pct_chg,vol,amount"
            ),
        )

    def daily_basic_by_date(self, trade_date: str) -> pd.DataFrame:
        self._sleep()
        return self.pro.daily_basic(
            trade_date=trade_date,
            fields=(
                "ts_code,trade_date,turnover_rate,turnover_rate_f,"
                "volume_ratio,pe,pb,total_share,float_share,"
                "free_share,total_mv,circ_mv"
            ),
        )
