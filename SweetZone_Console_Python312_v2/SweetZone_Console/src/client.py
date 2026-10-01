from __future__ import annotations

import time
import pandas as pd
from .common import ROOT, atomic_json, read_json, redact


class StopTask(RuntimeError):
    def __init__(self, message, endpoint='', kind='error'):
        super().__init__(message)
        self.endpoint, self.kind = endpoint, kind


class RateLimitStop(StopTask):
    def __init__(self, endpoint, message):
        super().__init__(f'{endpoint} 频次超限；任务停止：{message}', endpoint, 'rate_limit')


def is_rate_limit(message):
    text = str(message).lower()
    return any(x in text for x in ('频率', '频次', 'rate limit', 'too many requests', '429')) or (
        any(x in text for x in ('每分钟', '每小时', '每天'))
        and any(x in text for x in ('最多', '上限', '只能', '超限'))
    )


class Client:
    """串行请求，主动限速；所有服务端错误只调用一次，绝不自动重试。"""
    def __init__(self, pro, cfg, clock_path, token='', clock=time.time, sleep=time.sleep):
        self.pro, self.cfg, self.path, self.token = pro, cfg, clock_path, token
        self.clock, self.sleep = clock, sleep
        self.times = read_json(clock_path, {})
        self.count = 0

    @classmethod
    def create(cls, cfg, data_dir):
        import os
        import tushare as ts
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env', override=True)
        token = os.getenv('TUSHARE_TOKEN', '').strip()
        if not token or token == 'your_tushare_token_here':
            raise StopTask('请复制项目中的 .env.example 为 .env，并填写 TUSHARE_TOKEN')
        pro = ts.pro_api(token=token, timeout=cfg['timeout_seconds'])
        return cls(pro, cfg, data_dir / 'request_clock.json', token)

    def call(self, endpoint, **params):
        interval = self.cfg['interval_seconds']
        last = self.times.get('all', 0)
        due = last + interval
        if endpoint == 'trade_cal':
            due = max(due, self.times.get(endpoint, 0) + self.cfg['trade_cal_interval_seconds'])
        delay = max(0.0, due - self.clock())
        if delay > 0:
            if delay >= 3:
                print(f'[限速] {endpoint} 距下次允许请求还有 {delay:.1f} 秒', flush=True)
            self.sleep(delay)
        self.times['all'] = self.times[endpoint] = self.clock()
        atomic_json(self.path, self.times)
        self.count += 1
        try:
            return self.pro.query(endpoint, **params)
        except Exception as exc:
            message = redact(exc, self.token)
            if is_rate_limit(message):
                raise RateLimitStop(endpoint, message) from None
            raise StopTask(f'{endpoint} 请求失败：{message}', endpoint) from None

    def fetch(self, endpoint, fields, **params):
        """完整读取分页；重复页或异常结构时停止，避免把截断数据标为完成。"""
        size, offset, seen, pages = self.cfg['page_size'], 0, set(), []
        while True:
            frame = self.call(endpoint, fields=','.join(fields), limit=size, offset=offset, **params)
            if frame is None or not isinstance(frame, pd.DataFrame):
                raise StopTask(f'{endpoint} 未返回数据表', endpoint)
            if frame.empty:
                break
            if not set(fields).issubset(frame.columns):
                raise StopTask(f'{endpoint} 缺少字段：{sorted(set(fields)-set(frame.columns))}', endpoint)
            keys = ['ts_code', 'trade_date'] if 'trade_date' in fields else ['ts_code']
            if endpoint == 'trade_cal':
                keys = ['cal_date']
            ids = set(map(tuple, frame[keys].astype(str).to_numpy()))
            if ids & seen or len(ids) != len(frame):
                raise StopTask(f'{endpoint} 分页重复，请检查接口分页支持情况，未标记完成', endpoint)
            seen.update(ids)
            pages.append(frame[fields])
            if len(frame) < size:
                break
            offset += len(frame)
            if offset > 100000:
                raise StopTask(f'{endpoint} 单批返回量异常', endpoint)
        return pd.concat(pages, ignore_index=True) if pages else pd.DataFrame(columns=fields)
