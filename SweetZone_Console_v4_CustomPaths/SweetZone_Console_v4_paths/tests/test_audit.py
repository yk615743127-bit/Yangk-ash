import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
import pandas as pd
from src.audit import audit_summary,apply_audit,update_audits,FIELDS
from src.store import Store
from src.client import RateLimitStop

DAY='20261005'
def snapshot():
 return {'fetched':DAY,'records':[{'ts_code':'600000.SH','end_date':f'{y}1231','ann_date':f'{y+1}0401','audit_result':'标准无保留意见','audit_agency':'测试所','audit_sign':'甲'} for y in (2025,2024,2023)]}
class AuditTests(unittest.TestCase):
 def test_three_years(self):
  r=audit_summary(snapshot(),DAY);self.assertEqual(r['audit_all_standard'],'是');self.assertEqual(r['audit_year_3'],2023)
 def test_emphasis_not_standard(self):
  s=snapshot();s['records'][0]['audit_result']='带强调事项段的无保留意见';self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'否')
 def test_generic_unqualified_unknown(self):
  s=snapshot();s['records'][0]['audit_result']='无保留意见';self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'未知')
 def test_missing_not_replaced(self):
  s=snapshot();s['records'][1]['end_date']='20221231';self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'未知')
 def test_latest_correction(self):
  s=snapshot();s['records'].append(dict(s['records'][0],ann_date='20260930',audit_result='保留意见'));self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'否')
 def test_future_correction_ignored(self):
  s=snapshot();s['records'].append(dict(s['records'][0],ann_date='20261006',audit_result='否定意见'));self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'是')
 def test_midyear_not_counted(self):
  s=snapshot();s['records'][0]['end_date']='20250630';self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'未知')
 def test_conflict_unknown(self):
  s=snapshot();s['records'].append(dict(s['records'][0],audit_result='保留意见'));self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'未知')
 def test_stale_not_pass(self):
  s=snapshot();s['fetched']='20261004';self.assertEqual(audit_summary(s,DAY)['audit_all_standard'],'未知')
 def test_early_year(self):
  s=snapshot();s['fetched']='20260301';r=audit_summary(s,'20260301');self.assertEqual(r['audit_year_1'],2024)
 def test_filter_toggle(self):
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db');row={'ts_code':'600000.SH','passed':True,'status':'通过'}
   self.assertFalse(apply_audit(row,store,{},DAY)['passed'])
   self.assertTrue(apply_audit(row,store,{'audit':{'require_standard':False}},DAY)['passed']);store.close()
 def test_stop_and_resume(self):
  class Fake:
   def __init__(self):self.calls=[];self.fail=True
   def fetch(self,ep,fields,**kw):
    self.calls.append(kw['ts_code'])
    if self.fail and len(self.calls)==2:raise RateLimitStop(ep,'超限')
    return pd.DataFrame([dict(r,ts_code=kw['ts_code']) for r in snapshot()['records']])
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d)/'db');f=Fake();stocks=pd.DataFrame({'ts_code':['600000.SH','000001.SZ','688001.SH']})
   with patch('src.audit.now',return_value=datetime(2026,10,5)):
    with self.assertRaises(RateLimitStop):update_audits(f,store,stocks,{})
    self.assertEqual(f.calls,['600000.SH','000001.SZ']);f.fail=False
    update_audits(f,store,stocks,{})
    self.assertEqual(f.calls,['600000.SH','000001.SZ','000001.SZ','688001.SH'])
   store.close()
if __name__=='__main__':unittest.main()
