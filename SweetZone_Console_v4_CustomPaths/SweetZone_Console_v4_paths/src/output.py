from __future__ import annotations

import json
import math
import os
from numbers import Integral, Real
import pandas as pd
from .common import atomic_json

LABELS = {
    'audit_all_standard':'近3年年报均标准无保留',
    'audit_year_1':'最近年报年度','audit_opinion_1':'最近年报审计意见','audit_ann_1':'最近年报审计公告日',
    'audit_year_2':'前1年报年度','audit_opinion_2':'前1年报审计意见','audit_ann_2':'前1年报审计公告日',
    'audit_year_3':'前2年报年度','audit_opinion_3':'前2年报审计意见','audit_ann_3':'前2年报审计公告日',
    'audit_fetched':'审计更新日期','audit_notes':'审计说明',
    'market':'板块','float_ratio_pct':'流通比例百分比','float_gt_98':'流通比例大于98%',
    'price_passed':'量价条件通过','unlock_risk':'解禁风险','reduction_risk':'减持计划风险',
    'risk_date':'风险核验日期','risk_notes':'风险说明',
    'ts_code':'股票代码','name':'股票名称','asof':'目标日期','latest_date':'最新日K日期',
    'status':'状态','passed':'是否通过','reason':'通过或排除原因','stale_days':'距目标日自然天数',
    'last_close':'未复权收盘价','market_cap_yi':'总市值亿元','distance_low_pct':'距离一年低点百分比',
    'max_stage_rise_pct':'一年最大阶段涨幅百分比','downtrend':'持续下跌',
    'limit_close':'出现涨停','blown_limit':'出现炸板','ant_tree':'蚂蚁上树','bottom_giant':'底部巨量换手',
    'limit_dates':'触及涨停日期','giant_dates':'巨量换手日期','ant_rise_pct':'蚂蚁累计涨幅百分比',
    'ant_positive_ratio':'蚂蚁上涨天数比例','ant_volume_cv':'蚂蚁成交量变异系数','data_notes':'数据说明'
}


def clean(value):
    if isinstance(value, dict):
        return {k:clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [clean(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value,str):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    return str(value)


class Results:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.rows = []
        self.journal = (directory/'screen_checkpoint.jsonl').open('a',encoding='utf-8')

    def add(self,row):
        row = clean(row)
        self.rows.append(row)
        self.journal.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def finish(self,summary):
        self.journal.close()
        summary = clean(summary)
        atomic_json(self.directory/'run_summary.json',summary)
        frame = pd.DataFrame(self.rows).reindex(columns=LABELS)
        passed = frame.loc[frame.passed.eq(True)].copy()
        if not passed.empty:
            passed = passed.sort_values(['distance_low_pct','market_cap_yi'])
        issues = frame.loc[frame.status.isin(['数据不足','计算失败'])].copy()
        candidates = frame.loc[frame.price_passed.eq(True)].copy()
        outputs = {'筛选通过':passed,'量价候选未通过附加条件':candidates.loc[~candidates.passed.eq(True)],'全部检查':frame,'数据问题':issues}
        failure_fields = ['status','stop_kind','endpoint','reason','progress']
        failure = [{k:str(summary.get(k,'')) for k in failure_fields}] if summary['status'] in ('stopped','interrupted') else []
        pd.DataFrame(failure,columns=failure_fields).to_csv(self.directory/'任务停止记录.csv',index=False,encoding='utf-8-sig')
        for name, df in outputs.items():
            path = self.directory/(name+'.csv')
            temp = path.with_suffix('.tmp.csv')
            df.rename(columns=LABELS).to_csv(temp,index=False,encoding='utf-8-sig')
            temp.replace(path)
        path = self.directory/'筛选结果.xlsx'
        temp = self.directory/'筛选结果.tmp.xlsx'
        with pd.ExcelWriter(temp,engine='openpyxl') as writer:
            for name,df in outputs.items():
                df.rename(columns=LABELS).to_excel(writer,sheet_name=name,index=False)
            pd.DataFrame([{'项目':k,'内容':str(v)} for k,v in summary.items() if k!='config']).to_excel(writer,sheet_name='运行说明',index=False)
            for ws in writer.book.worksheets:
                ws.freeze_panes = 'A2'
                ws.auto_filter.ref = ws.dimensions
                for column in ws.columns:
                    letter = column[0].column_letter
                    title = str(column[0].value or '')
                    ws.column_dimensions[letter].width = 48 if '原因' in title or '说明' in title or title=='内容' else 22
        temp.replace(path)
