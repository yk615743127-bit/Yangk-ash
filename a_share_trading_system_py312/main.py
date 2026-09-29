
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

import pandas as pd

from src.data_manager import DailyDataManager
from src.exporter import export_results
from src.screener import TradingSystemScreener
from src.tushare_client import TushareClient


def get_latest_open_trade_date(client: TushareClient) -> str:
    end = datetime.now()
    start = end - timedelta(days=15)
    cal = client.trade_cal(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if cal.empty:
        return end.strftime("%Y%m%d")
    dates = sorted(cal["cal_date"].astype(str).tolist())
    return dates[-1]

print("a")

def main():
    parser = argparse.ArgumentParser(
        description="A股交易系统：本地日K增量维护 + 条件筛选（Python 3.12）"
    )
    parser.add_argument(
        "--mode",
        choices=["update-and-screen", "screen-only", "update-only"],
        default="update-and-screen",
        help="运行模式",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅处理前N只股票（调试用，0表示全部）",
    )
    args = parser.parse_args()

    client = TushareClient.create()
    manager = DailyDataManager(client)
    screener = TradingSystemScreener()

    latest_trade_date = get_latest_open_trade_date(client)
    print(f"[INFO] 最新交易日: {latest_trade_date}")

    stocks = client.stock_basic()
    if stocks.empty:
        raise RuntimeError("未获取到股票基础信息。请检查 Tushare Token、网络和接口权限。")

    # 提前缩小到主板 + 非ST，减少API调用
    from src.screener import is_main_board, is_non_st
    stocks = stocks[
        stocks["ts_code"].map(is_main_board)
        & stocks["name"].map(is_non_st)
    ].copy()

    if args.limit > 0:
        stocks = stocks.head(args.limit).copy()

    # 最新交易日 daily_basic 一次性取全市场，避免逐股票调用
    latest_basic = client.daily_basic_by_date(latest_trade_date)
    latest_basic_map = {}
    if not latest_basic.empty:
        latest_basic["ts_code"] = latest_basic["ts_code"].astype(str)
        latest_basic_map = {
            code: row for code, row in latest_basic.set_index("ts_code").iterrows()
        }

    all_rows = []
    passed_rows = []

    total = len(stocks)
    for i, (_, stock) in enumerate(stocks.iterrows(), start=1):
        ts_code = str(stock["ts_code"])
        name = str(stock["name"])
        print(f"[{i}/{total}] {ts_code} {name}")

        try:
            if args.mode in ("update-and-screen", "update-only"):
                daily = manager.update_one(ts_code, latest_trade_date)
            else:
                daily = manager.load_one(ts_code)

            if args.mode == "update-only":
                continue

            dbasic = latest_basic_map.get(ts_code)
            result = screener.screen_one(stock, daily, dbasic)
            all_rows.append(result.row)
            if result.passed:
                passed_rows.append(result.row)
                print(f"  >>> PASS: {result.row['pattern_reason']}")
        except KeyboardInterrupt:
            print("\n[STOP] 用户中断，已保存过的单股日K不会丢失。")
            break
        except Exception as e:
            print(f"  [ERROR] {ts_code}: {e}", file=sys.stderr)

    if args.mode != "update-only":
        passed_df = pd.DataFrame(passed_rows)
        all_df = pd.DataFrame(all_rows)

        if not passed_df.empty:
            passed_df = passed_df.sort_values(
                by=["current_vs_1y_low_pct", "market_cap_yi"],
                ascending=[True, True],
            )

        xlsx, csv = export_results(passed_df, all_df)
        print(f"\n[DONE] 筛选通过: {len(passed_df)} 只")
        print(f"[DONE] Excel: {xlsx}")
        print(f"[DONE] CSV:   {csv}")
    else:
        print("\n[DONE] 日K增量更新完成。")


if __name__ == "__main__":
    main()
