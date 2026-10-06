from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
import pandas as pd
from src.lock import FileLock, Timeout

from src.client import Client, StopTask
from src.common import ROOT, now, choose_data_dir, cutoff_for, load_config, atomic_json, redact
from src.store import Store
from src.update import prepare, download, import_legacy, missing_daily_dates
from src.screener import screen_one
from src.output import Results
from src.audit import update_audits, apply_audit
from src.risks import update_risks, load_reviews, apply_risks


def menu():
    print('\n甜蜜区筛选器 4.0\n1 更新并筛选\n2 仅更新\n3 仅本地筛选\n4 清理旧数据\n0 退出')
    modes = {'1':'update-and-screen','2':'update-only','3':'screen-only','4':'prune','0':'exit'}
    while True:
        value = input('请输入编号：').strip()
        if value in modes:
            return modes[value]
        print('请输入0至4')


def run(args, cfg, directory, client_factory=Client.create):
    store = Store(directory/'market.db')
    run_id = now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    results = Results(Path(r"D:\Stock\AI\Data\output") / run_id)
    progress = {'stage':'prepare','current':'','completed':0,'total':0,'endpoint':''}
    summary = {'run_id':run_id,'started':now().isoformat(),'mode':args.mode,
               'status':'running','data_dir':str(directory),'config':cfg}
    client = None
    exit_code = 0
    try:
        if args.mode == 'prune':
            # 清理截止基于今天，主动选择此模式才执行。
            cutoff = cutoff_for(now().strftime('%Y%m%d'),cfg)
            progress.update(stage='prune',current=cutoff)
            summary['deleted_rows'] = store.prune(cutoff)
            summary['cutoff'] = cutoff
            print(f'[清理] 删除 {cutoff} 之前的记录：{summary["deleted_rows"]}',flush=True)
        else:
            if args.mode != 'screen-only':
                client = client_factory(cfg['api'],directory)
                stocks,dates,start,end = prepare(client,store,cfg,args.asof)
                summary.update(asof=end,universe_date=store.meta('universe_date'),stocks_total=len(stocks))
                if args.legacy_dir:
                    import_legacy(args.legacy_dir,stocks,store,progress)
                # 保存计划在下载前，供中断后的离线筛选与审计读取。
                store.set_meta('last_plan',{'start':start,'end':end,'dates':dates})
                download(client,store,stocks,dates,progress)
                update_audits(client,store,stocks,progress)
                update_risks(client,store,cfg,progress)
            else:
                plan = store.meta('last_plan')
                records = store.meta('universe')
                if not plan or not records:
                    raise StopTask('本地没有股票名单或更新计划，请先运行一次更新')
                stocks = pd.DataFrame(records)
                end = args.asof or plan['end']
                if end > plan['end']:
                    raise StopTask('本地数据尚未更新到指定目标日')
                cal = store.meta('calendar',{})
                dates = [d for d in cal.get('open',[]) if cutoff_for(end,cfg)<=d<=end]
                if not dates:
                    raise StopTask('本地日历未覆盖目标日期')
                end, start = dates[-1],cutoff_for(dates[-1],cfg)
                summary.update(asof=end,universe_date=store.meta('universe_date'),stocks_total=len(stocks))
                print(f'[离线] 使用名单日期 {summary["universe_date"]}；目标日 {end}；不会联网',flush=True)
            if args.mode in ('update-and-screen','screen-only'):
                review_date = now().strftime('%Y%m%d')
                reviews = load_reviews(directory, stocks, review_date)
                summary['risk_date'] = review_date
                summary['historical_backtest'] = False
                gaps = missing_daily_dates(store,stocks,dates)
                summary['unfinished_daily_dates'] = len(gaps)
                for i,stock in enumerate(stocks.to_dict('records'),1):
                    progress.update(stage='screen',endpoint='',current=stock['ts_code'],completed=i-1,total=len(stocks))
                    k = store.load_stock(stock['ts_code'],start,end)
                    try:
                        row = screen_one(stock,k,cfg,end,gaps)
                    except Exception as exc:
                        row = {'ts_code':stock['ts_code'],'name':stock['name'],'asof':end,'passed':False,
                               'status':'计算失败','reason':redact(exc,client.token if client else '')}
                    row = apply_risks(row,stock,k,store,cfg,review_date,reviews)
                    row = apply_audit(row,store,cfg,review_date)
                    results.add(row)
                    progress['completed'] = i
                    print(f'[筛选 {i}/{len(stocks)} {i/len(stocks):.1%}] {stock["ts_code"]} {stock["name"]}：{row["status"]} {row["reason"]}',flush=True)
        summary['status'] = 'completed'
    except KeyboardInterrupt:
        exit_code = 130
        summary.update(status='interrupted',reason='用户中断；已提交数据和筛选记录已保存')
        print('\n[停止] 用户中断',flush=True)
    except StopTask as exc:
        exit_code = 2 if exc.kind=='rate_limit' else 1
        summary.update(status='stopped',stop_kind=exc.kind,reason=str(exc),endpoint=exc.endpoint)
        print(f'\n[停止] {exc}\n已保存已完成的批次。本次不再发起请求或继续筛选。',flush=True)
    except Exception as exc:
        exit_code = 1
        summary.update(status='stopped',stop_kind='error',reason=redact(exc,client.token if client else ''))
        print(f'\n[停止] {summary["reason"]}',flush=True)
    finally:
        summary.update(finished=now().isoformat(),progress=progress,checked=len(results.rows),
                       passed=sum(bool(r.get('passed')) for r in results.rows),
                       data_issues=sum(r.get('status') in ('数据不足','计算失败') for r in results.rows),
                       api_requests=client.count if client else 0)
        if summary['status']=='completed' and summary['data_issues']:
            summary['status']='completed_with_data_issues'
        # 先保存停止信息，即使Excel被占用仍可查看状态与JSONL原始结果。
        atomic_json(directory/'last_run.json',summary)
        try:
            results.finish(summary)
        except Exception as exc:
            exit_code = 1
            print(f'[导出失败] {redact(exc)}；原始筛选记录保存在 screen_checkpoint.jsonl',flush=True)
        store.close()
        print(f'[结果] 状态 {summary["status"]}；已检查 {summary["checked"]} 只；通过 {summary["passed"]} 只；数据问题 {summary["data_issues"]} 只',flush=True)
        print(f'[目录] {results.directory}',flush=True)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description='三板块非ST甜蜜区筛选器 Python 3.12')
    parser.add_argument('--mode',choices=['update-and-screen','update-only','screen-only','prune'])
    parser.add_argument('--data-dir',help='数据保存目录；第一次也可以在控制台输入')
    parser.add_argument('--change-dir',action='store_true',help='重新选择数据目录')
    parser.add_argument('--asof',help='目标日期YYYYMMDD；默认最近已到数据就绪时间的交易日')
    parser.add_argument('--legacy-dir',help='可选：旧项目 data/daily 文件夹，导入已有未复权CSV')
    args = parser.parse_args()
    if not args.mode:
        args.mode = menu()
    if args.mode == 'exit':
        return 0
    cfg = load_config()
    directory = choose_data_dir(args.data_dir,args.change_dir)
    print(f'[目录] 数据保存在 {directory}',flush=True)
    try:
        with FileLock(str(directory/'run.lock'),timeout=0):
            return run(args,cfg,directory)
    except Timeout:
        print('[停止] 此数据目录已有程序在运行，请先关闭另一个任务')
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已退出')
        sys.exit(130)
    except Exception as exc:
        print(f'[启动失败] {redact(exc)}',file=sys.stderr)
        sys.exit(1)
