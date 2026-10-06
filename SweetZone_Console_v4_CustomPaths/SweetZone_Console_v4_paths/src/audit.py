"""最近连续三年度年报审计；保留原文，严格判断标准无保留。"""
import re
import pandas as pd
from .client import StopTask
from .common import now

FIELDS=['ts_code','ann_date','end_date','audit_result','audit_agency','audit_sign']
STANDARD={'标准无保留意见','标准的无保留意见'}

def normalize(value):
    return re.sub(r'\s+','',str(value or ''))

def update_audits(client,store,stocks,progress):
    day=now().strftime('%Y%m%d')
    start=f'{int(day[:4])-5}0101'
    for i,code in enumerate(stocks.ts_code,1):
        key='audit:'+code
        old=store.meta(key,{})
        if old.get('fetched')==day:continue
        progress.update(stage='audit',endpoint='fina_audit',current=code,completed=i-1,total=len(stocks))
        print(f'[审计 {i}/{len(stocks)}] {code}',flush=True)
        df=client.fetch('fina_audit',FIELDS,ts_code=code,start_date=start,end_date=day)
        if not df.empty:
            if not df.ts_code.eq(code).all():raise StopTask('审计返回股票代码不匹配','fina_audit')
            for col in ('ann_date','end_date'):
                parsed=pd.to_datetime(df[col],format='%Y%m%d',errors='coerce')
                if parsed.isna().any():raise StopTask('审计日期缺失或格式异常','fina_audit')
                df[col]=parsed.dt.strftime('%Y%m%d')
            if (df.ann_date>day).any():raise StopTask('审计返回未来公告','fina_audit')
        rows=df.astype(object).where(pd.notna(df),None).to_dict('records')
        store.set_meta(key,{'fetched':day,'records':rows})
        progress['completed']=i

def audit_summary(snapshot,day):
    result={'audit_all_standard':'未知','audit_fetched':snapshot.get('fetched',''),'audit_notes':''}
    records=[r for r in snapshot.get('records',[]) if str(r.get('ann_date',''))<=day
             and str(r.get('end_date','')).endswith('1231') and str(r['end_date'])<day]
    year=int(day[:4])
    # 1—4月：若上一年年报已披露，使用上一年；否则上一完整披露年度。
    previous=year-1
    latest=previous if day[4:]>'0430' or any(r['end_date']==f'{previous}1231' for r in records) else year-2
    states=[]
    for i,y in enumerate(range(latest,latest-3,-1),1):
        result[f'audit_year_{i}']=y
        matched=[r for r in records if r['end_date']==f'{y}1231']
        if not matched:
            opinion='缺失';state='未知';ann=''
        else:
            ann=max(r['ann_date'] for r in matched)
            opinions={normalize(r.get('audit_result')) for r in matched if r['ann_date']==ann}
            opinion=' / '.join(sorted(opinions)) or '缺失'
            if len(opinions)!=1 or not next(iter(opinions),''):
                state='未知'
            else:
                value=next(iter(opinions))
                if value in STANDARD:state='是'
                elif value in ('无保留意见','无保留'):state='未知'
                elif any(x in value for x in ('保留意见','否定意见','无法表示意见','拒绝表示意见','强调事项','持续经营','非标准')):
                    state='否'
                else:state='未知'
        result[f'audit_opinion_{i}']=opinion
        result[f'audit_ann_{i}']=ann
        states.append(state)
    result['audit_all_standard']='否' if '否' in states else ('是' if states==['是']*3 else '未知')
    if snapshot.get('fetched')!=day:
        result['audit_notes']='审计快照非当日或缺失；需更新后确认最新更正公告'
        if result['audit_all_standard']=='是':result['audit_all_standard']='未知'
    if result['audit_all_standard']=='未知':
        result['audit_notes']+='；年度缺失、同日意见冲突、标签不明确或快照过期，不判作标准意见'
    return result

def apply_audit(row,store,cfg,day):
    row=dict(row)
    row.update(audit_summary(store.meta('audit:'+row['ts_code'],{}),day))
    if cfg.get('audit',{}).get('require_standard',True):
        state=row['audit_all_standard']
        if state=='否':
            row.update(passed=False,status='未通过',reason=row.get('reason','')+'；近三年年报存在非标准审计意见')
        elif state=='未知' and row.get('passed'):
            row.update(passed=False,status='数据不足',reason='审计数据未完整确认，不能通过')
    return row
