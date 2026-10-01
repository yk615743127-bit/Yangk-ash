from __future__ import annotations

import time
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from .client import StopTask
from .common import now, cutoff_for
from .store import FIELDS, STOCK_FIELDS, scope_id


def eligible(stocks):
    codes = stocks.ts_code.astype(str)
    # 明确排除B股；市场类别仍以服务端主板字段为准。
    a_share = codes.str.match(r'^(60\d{4}\.SH|00\d{4}\.SZ)$')
    return stocks[a_share & stocks.market.eq('主板') & stocks.list_status.eq('L')
                  & ~stocks.name.fillna('').str.contains('ST|退', case=False, regex=True)].copy()


def prepare(client, store, cfg, requested_end=None):
    today = now()
    safe_day = today if today.hour >= cfg['ready_hour_shanghai'] else today - timedelta(days=1)
    end = requested_end or safe_day.strftime('%Y%m%d')
    datetime.strptime(end, '%Y%m%d')
    if end > safe_day.strftime('%Y%m%d'):
        raise StopTask('目标日期尚未到数据就绪时间；默认使用北京时间18点后的完整日线')
    start = cutoff_for(end, cfg)
    cal = store.meta('calendar', {})
    fresh = cal and (today.date() - datetime.strptime(cal['fetched'], '%Y%m%d').date()).days < 7
    if not fresh or cal['start'] > start or cal['end'] < end:
        cal_end = (today + timedelta(days=365)).strftime('%Y%m%d')
        print('[准备] 获取交易日历（一次请求后缓存）', flush=True)
        cal_start = (datetime.strptime(start,'%Y%m%d')-timedelta(days=31)).strftime('%Y%m%d')
        frame = client.fetch('trade_cal', ['cal_date','is_open'], exchange='SSE', start_date=cal_start, end_date=cal_end)
        if frame.empty:
            raise StopTask('交易日历为空，停止任务', 'trade_cal')
        frame['cal_date'] = frame.cal_date.astype(str)
        actual = set(frame.cal_date)
        required = set(pd.date_range(start, end).strftime('%Y%m%d'))
        if not required.issubset(actual):
            raise StopTask('交易日历区间不完整，停止任务', 'trade_cal')
        cal = {'start': frame.cal_date.min(), 'end':frame.cal_date.max(),
               'fetched':today.strftime('%Y%m%d'),
               'open': sorted(frame.loc[pd.to_numeric(frame.is_open).eq(1), 'cal_date'].tolist())}
        store.set_meta('calendar', cal)
    dates = [d for d in cal['open'] if start <= d <= end]
    if not dates:
        raise StopTask('所选日期范围内没有交易日')
    end = dates[-1]
    start = cutoff_for(end, cfg)
    dates = [d for d in cal['open'] if start <= d <= end]
    if store.meta('universe_date') != today.strftime('%Y%m%d'):
        print('[准备] 获取当前主板非ST股票名单', flush=True)
        stocks = client.fetch('stock_basic', STOCK_FIELDS, market='主板', list_status='L')
        if stocks.empty or stocks[STOCK_FIELDS].isna().any().any():
            raise StopTask('股票名单为空或关键字段缺失', 'stock_basic')
        stocks = eligible(stocks).sort_values('ts_code').reset_index(drop=True)
        if stocks.empty:
            raise StopTask('未找到主板非ST股票', 'stock_basic')
        for d in stocks.list_date:
            datetime.strptime(str(d), '%Y%m%d')
        store.set_meta('universe', stocks.to_dict('records'))
        store.set_meta('universe_date', today.strftime('%Y%m%d'))
    stocks = pd.DataFrame(store.meta('universe'))
    return stocks, dates, start, end


def import_legacy(folder, stocks, store, progress):
    """导入旧项目未复权日K；不冒充完整的交易日批次，不删除来源文件。"""
    folder = Path(folder)
    if not folder.is_dir():
        raise StopTask(f'旧CSV目录不存在：{folder}')
    count = 0
    for i, stock in enumerate(stocks.itertuples(index=False), 1):
        path = folder / (stock.ts_code.replace('.', '_') + '.csv')
        if not path.exists():
            continue
        mark = [str(path.resolve()),path.stat().st_mtime_ns,path.stat().st_size]
        if store.meta('import:'+stock.ts_code) == mark:
            continue
        progress.update(stage='import',current=stock.ts_code,completed=i,total=len(stocks))
        frame = pd.read_csv(path, dtype={'ts_code':str,'trade_date':str})
        if not set(FIELDS['daily']).issubset(frame.columns):
            raise StopTask(f'{path.name} 缺少旧版日K字段，请选择旧项目的 data/daily 目录')
        frame = frame.loc[frame.ts_code.eq(stock.ts_code), FIELDS['daily']].copy()
        pd.to_datetime(frame.trade_date, format='%Y%m%d', errors='raise')
        frame = frame.drop_duplicates(['ts_code','trade_date'],keep='last')
        store.save_batch('daily','',None,frame,replace=False)
        store.set_meta('import:'+stock.ts_code,mark)
        count += 1
        print(f'[导入 {i}/{len(stocks)}] {stock.ts_code}',flush=True)
    print(f'[导入] 本次导入 {count} 个旧CSV；辅助数据将联网补齐',flush=True)


def download(client, store, stocks, dates, progress):
    jobs = []
    # 最新日先检验所需接口权限，其余日期从旧到新补齐。
    ordered_dates = [dates[-1]] + dates[:-1]
    for day in ordered_dates:
        codes = set(stocks.loc[stocks.list_date.astype(str).le(day), 'ts_code'])
        scope = scope_id(codes)
        for endpoint in FIELDS:
            if not store.done(endpoint,day,scope):
                jobs.append((endpoint,day,codes,scope))
    total, began = len(jobs), time.monotonic()
    print(f'[下载] 股票 {len(stocks)} 只，交易日 {len(dates)} 天，待补接口日期批次 {total} 个',flush=True)
    for i, (endpoint,day,codes,scope) in enumerate(jobs,1):
        progress.update(stage='download',endpoint=endpoint,current=day,completed=i-1,total=total)
        print(f'[下载 {i}/{total} {i/total:.1%}] {day} {endpoint}',flush=True)
        if codes:
            frame = client.fetch(endpoint, FIELDS[endpoint], trade_date=day)
            if frame.empty:
                raise StopTask(f'{day} {endpoint} 返回空表，可能尚未入库；本批不标记完成',endpoint)
            if not frame.trade_date.astype(str).eq(day).all():
                raise StopTask(f'{endpoint} 返回日期不匹配',endpoint)
            frame = frame.loc[frame.ts_code.isin(codes)].copy()
            frame['trade_date'] = frame.trade_date.astype(str)
            for column in FIELDS[endpoint][2:]:
                frame[column] = pd.to_numeric(frame[column],errors='coerce')
            required_fields = {'daily':['open','high','low','close','vol'],
                               'adj_factor':['adj_factor'],
                               'daily_basic':['turnover_rate','total_mv']}.get(endpoint,[])
            if required_fields:
                values = frame[required_fields].to_numpy()
                if not np.isfinite(values).all() or (values<0).any():
                    raise StopTask(f'{day} {endpoint} 必需字段无效，本批不标记完成',endpoint)
                positive = [x for x in required_fields if x not in ('vol','turnover_rate')]
                if (frame[positive]<=0).any().any():
                    raise StopTask(f'{day} {endpoint} 价格、复权因子或市值非正数',endpoint)
            if endpoint in ('adj_factor','daily_basic'):
                required = store.daily_codes(day) & codes
                if required - set(frame.ts_code):
                    raise StopTask(f'{day} {endpoint} 缺少 {len(required-set(frame.ts_code))} 只有日K的股票；待补齐',endpoint)
        else:
            frame = pd.DataFrame(columns=FIELDS[endpoint])
        store.save_batch(endpoint,day,scope,frame)
        progress.update(completed=i)
        elapsed = time.monotonic()-began
        print(f'  已保存 {len(frame)} 条；耗时 {elapsed/60:.1f} 分钟；预计剩余 {elapsed/i*(total-i)/60:.1f} 分钟',flush=True)
    store.set_meta('last_download_end', dates[-1])


def missing_daily_dates(store, stocks, dates):
    # 没有批次凭证的缺口不得被误当作停牌。已完成的空日K则允许停牌无记录。
    missing = []
    for day in dates:
        codes = set(stocks.loc[stocks.list_date.astype(str).le(day),'ts_code'])
        if not store.done('daily',day,scope_id(codes)):
            missing.append(day)
    return missing
