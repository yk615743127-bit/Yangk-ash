"""保留板块、流通比例和非ST处理；解禁、减持筛选已取消。"""
from __future__ import annotations
import math
import pandas as pd
def update_risks(client,store,cfg,progress):
    """解禁、减持条件已取消，不再请求公告风险数据。"""
    return

def load_reviews(directory,stocks,day):
    """不再创建、读取或要求人工公告核验表。"""
    return {}

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
    row.update(unlock_risk='未启用', reduction_risk='未启用',
               risk_notes='已取消解禁、减持筛选条件')
    bad=[]
    if 'ST' in stock.get('name','').upper() or '退' in stock.get('name',''):
        bad.append('当前ST或退市标识')
    if bad:
        row.update(passed=False,status='未通过',reason=row.get('reason','')+'；'+'；'.join(bad))
    return row
