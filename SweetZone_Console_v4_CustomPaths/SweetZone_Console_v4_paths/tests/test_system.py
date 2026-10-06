import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import main
from src.client import Client, RateLimitStop, StopTask
from src.common import load_config
from src.screener import screen_one
from src.store import Store, FIELDS, scope_id
from src.update import download, eligible, prepare


STOCK = {'ts_code':'600000.SH','name':'示例A','market':'主板','list_status':'L','list_date':'19990101'}


def bars():
    dates = pd.bdate_range(end='2026-09-29',periods=300).strftime('%Y%m%d')
    return pd.DataFrame({'ts_code':'600000.SH','trade_date':dates,'open':10.0,'high':10.1,'low':10.0,
                         'close':10.0,'pre_close':10.0,'pct_chg':0.0,'vol':100.0,'amount':1000.0,
                         'adj_factor':1.0,'turnover_rate':1.0,'total_mv':500000.0,
                         'total_share':100.0,'float_share':99.0,'up_limit':11.0,'down_limit':9.0,'limit_record':1})


class Algorithms(unittest.TestCase):
    def setUp(self):
        self.cfg=load_config()
    def evaluate(self,k,missing=()):
        return screen_one(STOCK,k,self.cfg,'20260929',missing)
    def test_flat_is_not_pattern(self):
        r=self.evaluate(bars())
        self.assertEqual(r['status'],'未通过')
        self.assertFalse(r['passed'])
    def test_exact_stage_boundary_and_blown_limit(self):
        k=bars(); k.loc[299,['high','up_limit']]=14.0
        r=self.evaluate(k)
        self.assertAlmostEqual(r['max_stage_rise_pct'],40)
        self.assertTrue(r['passed']);self.assertTrue(r['blown_limit'])
        k.loc[299,['high','up_limit']]=14.001
        self.assertFalse(self.evaluate(k)['passed'])
    def test_distance_and_market_cap_boundaries(self):
        k=bars();k.loc[299,['close','high','up_limit']]=13.5
        k.loc[299,'total_mv']=300000
        self.assertTrue(self.evaluate(k)['passed'])
        k.loc[299,'total_mv']=5000000
        self.assertTrue(self.evaluate(k)['passed'])
        k.loc[299,'total_mv']=5000001
        self.assertFalse(self.evaluate(k)['passed'])
        k.loc[299,'total_mv']=500000
        k.loc[299,['close','high','up_limit']]=13.501
        self.assertFalse(self.evaluate(k)['passed'])
    def test_same_bar_high_not_stage(self):
        k=bars(); k.loc[50,'high']=20
        k.loc[299,'high']=11
        r=self.evaluate(k)
        self.assertAlmostEqual(r['max_stage_rise_pct'],10)
        self.assertTrue(r['passed'])
    def test_split_adjustment(self):
        k=bars()
        k.loc[:199,['open','high','low','close']]*=2
        k.loc[200:,'adj_factor']=2
        k.loc[299,'high']=11
        r=self.evaluate(k)
        self.assertTrue(r['passed'])
        self.assertAlmostEqual(r['max_stage_rise_pct'],10)
    def test_missing_factor_is_not_dropped(self):
        k=bars();k.loc[150,'adj_factor']=np.nan
        self.assertEqual(self.evaluate(k)['status'],'数据不足')
    def test_download_gap_not_treated_as_suspension(self):
        k=bars();k.loc[299,'high']=11
        self.assertEqual(self.evaluate(k,[k.trade_date.iloc[200]])['status'],'数据不足')
    def test_ant_tree(self):
        k=bars()
        values=np.linspace(10.003,10.8,20)
        for c in ['open','close','low']:k.loc[280:,c]=values
        k.loc[280:,'high']=values+.02
        self.assertTrue(self.evaluate(k)['ant_tree'])
        k.loc[299,'vol']=100000
        self.assertFalse(self.evaluate(k)['ant_tree'])
    def test_giant_prior_median(self):
        k=bars();k.loc[280,'vol']=200;k.loc[280,'turnover_rate']=3
        r=self.evaluate(k)
        self.assertTrue(r['bottom_giant']);self.assertTrue(r['passed'])
        k.loc[280,'vol']=199.9
        self.assertFalse(self.evaluate(k)['bottom_giant'])
    def test_downtrend(self):
        k=bars(); values=np.linspace(12,10,300)
        for c in ['open','low','close']:k[c]=values
        k['high']=values+.01;k.loc[299,['high','up_limit']]=11
        r=self.evaluate(k)
        self.assertTrue(r['downtrend']);self.assertFalse(r['passed'])
    def test_unknown_pattern_not_false(self):
        k=bars();k.loc[290,'limit_record']=0
        self.assertEqual(self.evaluate(k)['status'],'数据不足')
        k.loc[299,'high']=11
        self.assertTrue(self.evaluate(k)['passed'])
    def test_no_limit_price_not_hit(self):
        k=bars();k['up_limit']=0
        self.assertFalse(self.evaluate(k)['limit_close'])
        self.assertFalse(self.evaluate(k)['blown_limit'])
    def test_universe(self):
        rows=[STOCK,dict(STOCK,ts_code='688001.SH',market='科创板'),dict(STOCK,name='*ST示例'),
              dict(STOCK,ts_code='200001.SZ'),dict(STOCK,ts_code='000001.SZ'),
              dict(STOCK,ts_code='300001.SZ',market='创业板'),dict(STOCK,ts_code='600001.SH',list_status='D')]
        self.assertEqual(set(eligible(pd.DataFrame(rows)).ts_code),{'600000.SH','000001.SZ','688001.SH','300001.SZ'})


class APIAndRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.cfg=load_config()
    def tearDown(self):self.temp.cleanup()
    def test_rate_limit_not_retried_or_slept_after_error(self):
        class Pro:
            count=0
            def query(self,*args,**kwargs):
                self.count+=1
                raise RuntimeError('抱歉，daily 频率超限(50次/分钟)')
        pro=Pro();sleeps=[]
        client=Client(pro,self.cfg['api'],self.root/'clock.json',clock=lambda:1000,sleep=sleeps.append)
        with self.assertRaises(RateLimitStop):client.call('daily')
        self.assertEqual(pro.count,1);self.assertEqual(sleeps,[])
    def test_clock_persists_across_clients(self):
        class Pro:
            def query(self,*a,**kw):return pd.DataFrame()
        sleeps=[];clock=lambda:1000
        Client(Pro(),self.cfg['api'],self.root/'clock.json',clock=clock,sleep=sleeps.append).call('trade_cal')
        Client(Pro(),self.cfg['api'],self.root/'clock.json',clock=clock,sleep=sleeps.append).call('trade_cal')
        self.assertEqual(sleeps,[61])
    def test_repeated_pages_fail(self):
        class Pro:
            def query(self,*a,**kw):return pd.DataFrame({'ts_code':['600000.SH'],'trade_date':['20260929']})
        cfg=dict(self.cfg['api'],page_size=1)
        c=Client(Pro(),cfg,self.root/'clock.json',clock=lambda:1000,sleep=lambda n:None)
        with self.assertRaises(StopTask):c.fetch('daily',['ts_code','trade_date'])
    def test_pagination_collects_all(self):
        class Pro:
            def query(self,*a,**kw):
                return pd.DataFrame({'ts_code':['600000.SH','000001.SZ'],'trade_date':['20260929']*2}).iloc[kw['offset']:kw['offset']+1]
        cfg=dict(self.cfg['api'],page_size=1)
        c=Client(Pro(),cfg,self.root/'clock.json',clock=lambda:1000,sleep=lambda n:None)
        self.assertEqual(len(c.fetch('daily',['ts_code','trade_date'])),2)
    def test_stop_task_saves_daily_then_resume_skips_it(self):
        class Fake:
            token='';count=0
            def __init__(self,stop):self.calls=[];self.stop=stop
            def fetch(self,endpoint,fields,**params):
                self.calls.append(endpoint);self.count+=1
                if self.stop and endpoint=='adj_factor':raise RateLimitStop(endpoint,'频率超限')
                return bars().tail(1)[fields].reset_index(drop=True)
        stocks=pd.DataFrame([STOCK]);fake=Fake(True)
        args=argparse.Namespace(mode='update-and-screen',asof=None,legacy_dir=None)
        with patch('main.prepare',return_value=(stocks,['20260929'],'20240101','20260929')):
            result=main.run(args,self.cfg,self.root,lambda *a:fake)
        self.assertEqual(result,2)
        self.assertEqual(fake.calls,['daily','adj_factor'])
        summary=json.loads((self.root/'last_run.json').read_text())
        self.assertEqual(summary['checked'],0)
        self.assertEqual(summary['stop_kind'],'rate_limit')
        store=Store(self.root/'market.db');resume=Fake(False)
        download(resume,store,stocks,['20260929'],{})
        self.assertEqual(resume.calls,['adj_factor','daily_basic','stk_limit'])
        self.assertTrue(store.done('stk_limit','20260929',scope_id(['600000.SH'])))
        store.close()
    def test_transaction_rollback(self):
        store=Store(self.root/'market.db')
        bad=bars().tail(2)[FIELDS['daily']].astype(object).copy();bad.at[299,'close']={'bad':1}
        with self.assertRaises(Exception):store.save_batch('daily','20260929','scope',bad)
        self.assertFalse(store.done('daily','20260929','scope'))
        self.assertEqual(len(store.daily_codes('20260929')),0)
        store.close()
    def test_prune_preserves_cutoff_and_newer(self):
        store=Store(self.root/'market.db');df=bars()[FIELDS['daily']]
        store.save_batch('daily','20260929','scope',df)
        cutoff=str(df.trade_date.iloc[150]);store.prune(cutoff)
        self.assertEqual(len(store.load_stock('600000.SH','20000101','20991231')),150)
        store.close()
    def test_offline_never_constructs_client(self):
        store=Store(self.root/'market.db')
        store.set_meta('universe',[STOCK]);store.set_meta('universe_date','20260929')
        store.set_meta('last_plan',{'start':'20240101','end':'20260929','dates':['20260929']})
        store.set_meta('calendar',{'open':['20260929']});store.close()
        args=argparse.Namespace(mode='screen-only',asof=None,legacy_dir=None)
        def fail(*args):raise AssertionError('离线模式不应创建联网客户端')
        self.assertEqual(main.run(args,self.cfg,self.root,fail),0)
    def test_calendar_and_universe_cached_for_second_prepare(self):
        class Fake:
            calls=[]
            def fetch(self,endpoint,fields,**params):
                self.calls.append(endpoint)
                if endpoint=='stock_basic':return pd.DataFrame([STOCK])
                dates=pd.date_range(params['start_date'],params['end_date'])
                return pd.DataFrame({'cal_date':dates.strftime('%Y%m%d'),'is_open':(dates.dayofweek<5).astype(int)})
        fake=Fake();store=Store(self.root/'market.db')
        from datetime import datetime, timezone, timedelta
        fixed=datetime(2026,9,30,0,0,tzinfo=timezone(timedelta(hours=8)))
        with patch('src.update.now',return_value=fixed):
            a=prepare(fake,store,self.cfg)
            b=prepare(fake,store,self.cfg)
        self.assertEqual(fake.calls,['trade_cal','stock_basic'])
        self.assertEqual(a[3],'20260929');self.assertEqual(a[1],b[1])
        store.close()
    def test_ctrl_c_preserves_already_screened_rows(self):
        store=Store(self.root/'market.db')
        stocks=[STOCK,dict(STOCK,ts_code='000001.SZ')]
        store.set_meta('universe',stocks);store.set_meta('universe_date','20260929')
        store.set_meta('last_plan',{'start':'20240101','end':'20260929','dates':['20260929']})
        store.set_meta('calendar',{'open':['20260929']});store.close()
        args=argparse.Namespace(mode='screen-only',asof=None,legacy_dir=None)
        row={'ts_code':'600000.SH','name':'示例A','passed':True,'status':'通过','reason':'涨停'}
        with patch('main.screen_one',side_effect=[row,KeyboardInterrupt()]):
            self.assertEqual(main.run(args,self.cfg,self.root),130)
        summary=json.loads((self.root/'last_run.json').read_text())
        self.assertEqual(summary['checked'],1);self.assertEqual(summary['passed'],0)
        path=next((self.root/'output').glob('*/筛选通过.csv'))
        self.assertEqual(len(pd.read_csv(path)),0)


if __name__=='__main__':unittest.main()
