import tempfile
import unittest
import sqlite3
from pathlib import Path
import pandas as pd
from src.store import Store
from src.common import load_config
from src.risks import apply_risks,window,load_reviews,update_risks
from src.client import RateLimitStop,Client
from unittest.mock import patch
from datetime import datetime

class Risks(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.store=Store(self.path/'market.db')
  self.cfg=load_config();self.day='20261005';self.stock={'ts_code':'600000.SH','name':'示例','market':'主板'}
  self.row={'passed':True,'status':'通过','reason':'涨停'}
  self.k=pd.DataFrame([{'trade_date':self.day,'total_share':100,'float_share':98}])
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def apply(self,reviews={}):return apply_risks(self.row,self.stock,self.k,self.store,self.cfg,self.day,reviews)
 def good(self):return {'600000.SH':{'unlock_status':'无风险','reduction_status':'无风险','source':'公告审核记录'}}
 def test_unknown_blocks(self):
  r=self.apply();self.assertFalse(r['passed']);self.assertTrue(r['price_passed'])
 def test_ratio_boundary_not_filter(self):
  r=self.apply(self.good());self.assertTrue(r['passed']);self.assertEqual(r['float_gt_98'],'否')
  self.k['float_share']=98.0001;self.assertEqual(self.apply(self.good())['float_gt_98'],'是')
 def test_missing_ratio_unknown(self):
  self.k['total_share']=0;self.assertEqual(self.apply(self.good())['float_gt_98'],'未知')
 def test_manual_risk_blocks(self):
  rev=self.good();rev['600000.SH']['reduction_status']='有风险';self.assertFalse(self.apply(rev)['passed'])
 def test_st_blocks(self):
  self.stock['name']='*ST示例';self.assertFalse(self.apply(self.good())['passed'])
 def test_source_required(self):
  rev=self.good();rev['600000.SH']['source']='';self.assertFalse(self.apply(rev)['passed'])
 def test_unlock_overrides_clear_review(self):
  self.store.set_meta('unlock_active','test');self.store.set_meta('test',{'date':self.day,'start':'20260405','end':'20270405','records':[{'ts_code':'600000.SH','ann_date':'20261001','float_date':'20261020'}]})
  self.assertFalse(self.apply(self.good())['passed'])
 def test_future_announcement_not_used(self):
  self.store.set_meta('unlock_active','test');self.store.set_meta('test',{'date':self.day,'start':'20260405','end':'20270405','records':[{'ts_code':'600000.SH','ann_date':'20261006','float_date':'20261020'}]})
  self.assertTrue(self.apply(self.good())['passed'])
 def test_calendar_month(self):self.assertEqual(window('20260831',-6),'20260228')
 def test_stale_reviews_not_loaded(self):
  pd.DataFrame([{'ts_code':'600000.SH','review_date':'20261004','unlock_status':'无风险','reduction_status':'无风险','source':'公告','notes':''}]).to_csv(self.path/'risk_reviews.csv',index=False)
  self.assertEqual(load_reviews(self.path,pd.DataFrame([self.stock]),self.day),{})
 def test_risk_resume_and_stop(self):
  class Fake:
   count=0
   def fetch(self,*a,**kw):
    self.count+=1
    if self.count==2:raise RateLimitStop('share_float','频次超限')
    return pd.DataFrame(columns=['ts_code','ann_date','float_date','float_share','float_ratio','holder_name','share_type'])
  f=Fake()
  with patch('src.risks.now',return_value=datetime(2026,10,5)):
   with self.assertRaises(RateLimitStop):update_risks(f,self.store,self.cfg,{})
   self.assertEqual(f.count,2);self.assertIsNone(self.store.meta('unlock_active'))
   update_risks(f,self.store,self.cfg,{})
   self.assertEqual(f.count,14)
 def test_schema_migration(self):
  path=self.path/'old.db';c=sqlite3.connect(path)
  c.executescript('CREATE TABLE daily_basic(ts_code TEXT,trade_date TEXT,turnover_rate REAL,total_mv REAL,PRIMARY KEY(ts_code,trade_date)); CREATE TABLE completed(endpoint TEXT,trade_date TEXT,scope TEXT,PRIMARY KEY(endpoint,trade_date,scope)); INSERT INTO completed VALUES("daily_basic","20260930","old");');c.close()
  s=Store(path);self.assertFalse(s.done('daily_basic','20260930','old'))
  self.assertIn('float_share',{r[1] for r in s.conn.execute('PRAGMA table_info(daily_basic)')});s.close()
 def test_multiple_holders_allowed(self):
  class Pro:
   def query(self,*a,**kw):return pd.DataFrame({'ts_code':['600000.SH']*2,'holder_name':['甲','乙']})
  c=Client(Pro(),self.cfg['api'],self.path/'clock.json')
  self.assertEqual(len(c.fetch('share_float',['ts_code','holder_name'])),2)

if __name__=='__main__':unittest.main()
