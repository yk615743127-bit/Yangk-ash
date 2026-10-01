from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-10


def screen_one(stock, frame, cfg, asof, missing_dates=()):
    s = cfg['screen']
    row = {'ts_code': stock['ts_code'], 'name':stock['name'], 'asof':asof,
           'latest_date':'', 'status':'数据不足', 'passed':False, 'reason':'',
           'limit_close':False, 'blown_limit':False, 'ant_tree':False, 'bottom_giant':False}
    def invalid(message):
        row['reason'] = message
        return row
    if frame.empty:
        return invalid('无本地日K')
    k = frame.sort_values('trade_date').drop_duplicates('trade_date').reset_index(drop=True).copy()
    row['latest_date'] = str(k.trade_date.iloc[-1])
    row['stale_days'] = (pd.Timestamp(asof)-pd.Timestamp(row['latest_date'])).days
    ydays, p, ant, giant = s['year_days'], s['pattern_days'], s['ant'], s['giant']
    if len(k) < ydays:
        return invalid(f'历史日K不足{ydays}根，实际{len(k)}根')
    # 巨量形态的历史低点需要向前再覆盖 p-1 根K；上市不足时该分支记为未知。
    needed = ydays+p-1
    start = str(k.trade_date.iloc[max(0,len(k)-needed)])
    if any(max(start,str(stock['list_date'])) <= d <= asof for d in missing_dates):
        return invalid('相关区间有未完成的日K下载批次，请先补齐')
    columns = ['open','high','low','close','vol','adj_factor','turnover_rate','total_mv','up_limit','down_limit','limit_record']
    for c in columns:
        if c not in k:
            k[c] = np.nan
        k[c] = pd.to_numeric(k[c],errors='coerce')
    tail = k.tail(needed)
    prices = tail[['open','high','low','close','adj_factor']]
    if not np.isfinite(prices.to_numpy()).all() or (prices<=0).any().any():
        return invalid('价格或复权因子缺失、非正数或无穷值')
    if (tail.high < tail[['open','close','low']].max(axis=1)-EPS).any() or (tail.low > tail[['open','close','high']].min(axis=1)+EPS).any():
        return invalid('日K高低价关系异常')
    factors = k.adj_factor/k.adj_factor.iloc[-1]
    for c in ('open','high','low','close'):
        k['a_'+c] = k[c]*factors
    y, h, recent = k.tail(ydays), k.tail(s['half_days']), k.tail(p)
    low, last = float(y.a_low.min()), float(k.a_close.iloc[-1])
    distance = last/low-1
    # 只允许低点严格早于高点。
    stage = float((y.a_high/y.a_low.shift(1).cummin()-1).max())
    ma = k.a_close.rolling(s['downtrend_ma_days']).mean()
    down = bool(last < ma.iloc[-1] and ma.iloc[-1] < ma.iloc[-1-s['downtrend_slope_days']]
                and k.a_low.tail(s['downtrend_low_days']).min() <= h.a_low.min()*(1+s['downtrend_low_tolerance'])+EPS)
    mv = k.total_mv.iloc[-1]/10000
    row.update(last_close=float(k.close.iloc[-1]),market_cap_yi=mv,
               distance_low_pct=distance*100,max_stage_rise_pct=stage*100,downtrend=down)
    if not np.isfinite(mv) or mv<=0:
        return invalid('最新有效日总市值缺失')
    reasons = []
    if not s['market_cap_min_yi']-EPS <= mv <= s['market_cap_max_yi']+EPS:
        reasons.append('总市值不在范围内')
    if distance > s['max_low_distance']+EPS:
        reasons.append('距离一年低点超过上限')
    if stage > s['max_stage_rise']+EPS:
        reasons.append('一年阶段涨幅超过上限')
    if down:
        reasons.append('近半年持续下跌')
    # 三个形态分别给出已知/未知。未知分支不当作false；其他分支明确命中仍可通过。
    unknown = []
    limit_known = recent.limit_record.eq(1).all()
    if not limit_known:
        unknown.append('部分实际涨停价记录缺失')
    valid_limit = recent.limit_record.eq(1) & np.isfinite(recent.up_limit) & recent.up_limit.gt(0)
    hit = valid_limit & recent.high.ge(recent.up_limit-s['limit_price_tolerance'])
    sealed = hit & recent.close.ge(recent.up_limit-s['limit_price_tolerance'])
    blown = hit & ~sealed
    row['limit_close'],row['blown_limit'] = bool(sealed.any()),bool(blown.any())
    row['limit_dates'] = ','.join(recent.loc[hit,'trade_date'].astype(str))
    n = ant['days']
    av = k.vol.tail(n)
    ant_known = len(k)>n+ant['ma_slope_days'] and np.isfinite(av.to_numpy()).all() and (av>=0).all() and av.mean()>0
    if ant_known:
        rets = k.a_close.pct_change(fill_method=None).tail(n)
        rise = last/k.a_close.iloc[-n-1]-1
        positive = float(rets.gt(0).mean())
        cv = float(av.std(ddof=0)/av.mean())
        ma20 = k.a_close.rolling(20).mean()
        ant_hit = bool(ant['min_rise']-EPS<=rise<=ant['max_rise']+EPS and positive+EPS>=ant['positive_ratio']
                       and k.a_close.tail(5).mean()>k.a_close.tail(10).mean()>ma20.iloc[-1]
                       and ma20.iloc[-1]>ma20.iloc[-1-ant['ma_slope_days']]
                       and rets.abs().max()<=ant['max_abs_daily_return']+EPS and cv<=ant['max_volume_cv']+EPS)
        row.update(ant_tree=ant_hit,ant_rise_pct=rise*100,ant_positive_ratio=positive,ant_volume_cv=cv)
    else:
        unknown.append('蚂蚁上树成交量或历史数据不足')
    historic_low = k.a_low.rolling(ydays,min_periods=ydays).min()
    vol_median = k.vol.shift(1).rolling(giant['volume_days'],min_periods=giant['volume_days']).median()
    indices = recent.index
    gknown = (historic_low.loc[indices].notna() & vol_median.loc[indices].gt(0)
              & k.vol.shift(1).rolling(giant['volume_days']).min().loc[indices].ge(0)
              & np.isfinite(recent.vol) & recent.vol.ge(0) & np.isfinite(recent.turnover_rate) & recent.turnover_rate.ge(0))
    ghit = (gknown & recent.a_close.le(historic_low.loc[indices]*(1+giant['max_low_distance'])+EPS)
            & recent.vol.ge(vol_median.loc[indices]*giant['volume_multiple']-EPS)
            & recent.turnover_rate.ge(giant['min_turnover_pct']-EPS))
    row['bottom_giant'] = bool(ghit.any())
    row['giant_dates'] = ','.join(recent.loc[ghit,'trade_date'].astype(str))
    if not gknown.all():
        unknown.append('部分巨量形态窗口的历史或换手率不足')
    row['data_notes'] = '；'.join(unknown)
    pattern = row['limit_close'] or row['blown_limit'] or row['ant_tree'] or row['bottom_giant']
    if reasons:
        row.update(status='未通过',reason='；'.join(reasons))
    elif pattern:
        row.update(status='通过',passed=True,reason='；'.join(label for key,label in (
            ('limit_close','涨停'),('blown_limit','炸板'),('ant_tree','蚂蚁上树'),('bottom_giant','底部巨量换手')) if row[key]))
    elif unknown:
        return invalid('尚无已确认形态，且有分支无法计算：'+'；'.join(unknown))
    else:
        row.update(status='未通过',reason='三种形态均未出现')
    return row
