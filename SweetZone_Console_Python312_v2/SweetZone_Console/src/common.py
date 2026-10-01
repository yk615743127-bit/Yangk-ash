from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = timezone(timedelta(hours=8))


def now():
    return datetime.now(SHANGHAI)


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load_config():
    cfg = read_json(ROOT / 'config.json')
    if cfg['history_years'] < 2 or cfg['buffer_days'] < 0:
        raise ValueError('历史范围至少两年，缓冲天数不能小于零')
    api, s = cfg['api'], cfg['screen']
    if api['interval_seconds'] < 0.3 or api['trade_cal_interval_seconds'] < 61:
        raise ValueError('正常接口间隔至少1.3秒，trade_cal间隔至少61秒')
    if not 1 <= api['page_size'] <= 3000:
        raise ValueError('page_size须在1至3000之间')
    if not 0 <= cfg['ready_hour_shanghai'] <= 23:
        raise ValueError('数据就绪小时须在0至23之间')
    if s['year_days'] < max(s['half_days'], s['pattern_days'], 80):
        raise ValueError('一年窗口不能小于半年、形态窗口或80日')
    if not 0 < s['market_cap_min_yi'] <= s['market_cap_max_yi']:
        raise ValueError('市值范围不合法')
    return cfg


def cutoff_for(end_date, cfg):
    # 两个自然年，闰日退到2月28日，再保留计算缓冲。
    end = datetime.strptime(end_date, '%Y%m%d')
    try:
        start = end.replace(year=end.year - cfg['history_years'])
    except ValueError:
        start = end.replace(year=end.year - cfg['history_years'], day=28)
    return (start - timedelta(days=cfg['buffer_days'])).strftime('%Y%m%d')


def choose_data_dir(explicit=None, change=False):
    settings_path = ROOT / 'local_settings.json'
    settings = read_json(settings_path, {})
    if explicit:
        path = Path(explicit).expanduser().resolve()
    elif settings.get('data_dir') and not change:
        path = Path(settings['data_dir'])
        if not path.exists():
            raise FileNotFoundError(f'上次目录不存在：{path}；使用 --change-dir 重新选择')
    else:
        default = ROOT / 'workspace'
        print('首次使用或更换目录：输入数据保存文件夹的完整路径。')
        value = input(f'直接回车使用 {default}\n路径：').strip().strip('"')
        path = Path(value).expanduser().resolve() if value else default
    path.mkdir(parents=True, exist_ok=True)
    atomic_json(settings_path, {'data_dir': str(path)})
    return path


def redact(message, token=''):
    import re
    text = str(message)
    if token:
        text = text.replace(token, '[密钥已隐藏]')
    return re.sub(r'(?i)(token[\s\"\x27:=]+)[a-z0-9_-]{12,}', r'\1[已隐藏]', text)
