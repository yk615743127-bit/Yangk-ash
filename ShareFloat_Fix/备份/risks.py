"""公告风险：自动证据 + 按核验日导入的公告审核结果；未知不通过。"""
from __future__ import annotations
import math
import pandas as pd
from .common import now
from .client import StopTask

FLOAT_FIELDS=['ts_code','ann_date','float_date','float_share','float_ratio','holder_name','share_type']
REVIEW_FIELDS=['ts_code','review_date','unlock_status','reduction_status','source','notes']
VALID={'无风险','有风险','待核实'}

def window(day, months):
    return (pd.Timestamp(day)+pd.DateOffset(months=months)).strftime('%Y%m%d')

def update_risks(client,store,cfg,progress):
    day=now().strftime('%Y%m%d')
    start=window(day,-cfg['risk']['lookback_months'])
    end=window(day,cfg['risk']['unlock_forward_months'])
    key=f'unlock:{day}:{start}:{end}'
    if store.meta(key) is not None:
        store.set_meta('unlock_active',key)
        return
    # 按月缓存，每完成一批即提交。边界连续、不重叠。
    cursor=pd.Timestamp(start); finish=pd.Timestamp(end); records=[]
    while cursor<=finish:
        stop=min(cursor+pd.offsets.MonthEnd(0),finish)
        a,b=cursor.strftime('%Y%m%d'),stop.strftime('%Y%m%d')
        batch=f'{key}:{a}:{b}'
        rows=store.meta(batch)
        if rows is None:
            progress.update(stage='risk',endpoint='share_float',current=f'{a}-{b}')
            print(f'[风险] 解禁记录 {a}—{b}',flush=True)
            # 子区间单独提交；右侧失败时，重启可复用已完成的左侧。
            prefix=f'{key}:split_v2'
            frame=client.fetch_float_range(
                FLOAT_FIELDS,a,b,
                load=lambda x,y: store.meta(f'{prefix}:{x}:{y}'),
                save=lambda x,y,rows: store.set_meta(f'{prefix}:{x}:{y}',rows))
            for col in ('ann_date','float_date'):
                if not frame.empty:
                    parsed=pd.to_datetime(frame[col],format='%Y%m%d',errors='coerce')
                    if parsed.isna().any():raise StopTask('解禁日期缺失或格式错误','share_float')
                    frame[col]=parsed.dt.strftime('%Y%m%d')
            if not frame.empty and not frame.float_date.between(a,b).all():
                raise StopTask('解禁接口返回区间不匹配','share_float')
            rows=frame.astype(object).where(pd.notna(frame),None).to_dict('records')
            store.set_meta(batch,rows)
        records.extend(rows)
        cursor=stop+pd.Timedelta(days=1)
    store.set_meta(key,{'date':day,'start':start,'end':end,'records':records})
    store.set_meta('unlock_active',key)

def load_reviews(directory,stocks,day):
    path=directory/'risk_reviews.csv'
    if not path.exists():
        frame=stocks[['ts_code']].copy()
        frame['review_date']=''
        frame['unlock_status']='待核实';frame['reduction_status']='待核实'
        frame['source']='';frame['notes']=''
        frame.to_csv(path,index=False,encoding='utf-8-sig')
        print(f'[风险] 已生成公告核验模板 {path}；未核验股票不会列入全部通过名单',flush=True)
    frame=pd.read_csv(path,dtype=str,keep_default_na=False)
    if not set(REVIEW_FIELDS).issubset(frame.columns):raise StopTask('risk_reviews.csv 缺少必需列')
    if not frame.unlock_status.isin(VALID).all() or not frame.reduction_status.isin(VALID).all():
        raise StopTask('风险状态只能为 无风险/有风险/待核实')
    current=frame.loc[frame.review_date.eq(day)]
    if current.ts_code.duplicated().any():raise StopTask('同一股票同一核验日存在重复审核记录')
    return {r['ts_code']:r for r in current.to_dict('records')}

def apply_risks(row,stock,k,store,cfg,day,reviews):
    row=dict(row);row['price_passed']=bool(row.get('passed'))
    row.update(market=stock.get('market','未知'),float_gt_98='未知',risk_date=day)
    if not k.empty and {'total_share','float_share'}.issubset(k.columns):
        last=k.sort_values('trade_date').iloc[-1]
        try:
            total,flt=float(last.total_share),float(last.float_share)
            if math.isfinite(total) and math.isfinite(flt) and total>0 and 0<=flt<=total:
                row['float_ratio_pct']=100*flt/total
                row['float_gt_98']='是' if flt/total>0.98 else '否'
        except (TypeError,ValueError):pass
    notes=[];review=reviews.get(stock['ts_code'],{})
    verified=bool(review.get('source','').strip())
    unlock=review.get('unlock_status','待核实') if verified else '待核实'
    reduction=review.get('reduction_status','待核实') if verified else '待核实'
    key=store.meta('unlock_active');snap=store.meta(key,{}) if key else {}
    start=window(day,-cfg['risk']['lookback_months']);end=window(day,cfg['risk']['unlock_forward_months'])
    fresh=snap.get('date')==day and snap.get('start')==start and snap.get('end')==end
    if fresh:
        hit=[r for r in snap['records'] if r['ts_code']==stock['ts_code'] and r['ann_date']<=day and start<=r['float_date']<=end]
        if hit:
            unlock='有风险';notes.append('半年窗口有解禁记录：'+','.join(sorted({r['float_date'] for r in hit})))
    else:
        notes.append('解禁自动快照缺失或非当日；需当日完整公告核验')
    if unlock=='待核实':notes.append('需核验半年解禁计划及更早尚未完成事项')
    if reduction=='待核实':notes.append('需核验过去半年减持计划及更早尚未完成计划')
    if review.get('source'):notes.append('核验来源：'+review['source'])
    if review.get('notes'):notes.append(review['notes'])
    row.update(unlock_risk=unlock,reduction_risk=reduction,risk_notes='；'.join(notes))
    bad=[]
    if 'ST' in stock.get('name','').upper() or '退' in stock.get('name',''):bad.append('当前ST或退市标识')
    if unlock=='有风险':bad.append('解禁风险')
    if reduction=='有风险':bad.append('减持计划风险')
    if bad:
        row.update(passed=False,status='未通过',reason=row.get('reason','')+'；'+'；'.join(bad))
    elif row['price_passed'] and (unlock!='无风险' or reduction!='无风险'):
        row.update(passed=False,status='数据不足',reason='量价通过；公告风险未核实，不作为最终通过')
    return row
