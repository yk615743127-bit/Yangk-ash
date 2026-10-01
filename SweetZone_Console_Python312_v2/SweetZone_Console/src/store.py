from __future__ import annotations

import json
import sqlite3
import hashlib
import pandas as pd

FIELDS = {
    'daily': ['ts_code','trade_date','open','high','low','close','pre_close','pct_chg','vol','amount'],
    'adj_factor': ['ts_code','trade_date','adj_factor'],
    'daily_basic': ['ts_code','trade_date','turnover_rate','total_mv'],
    'stk_limit': ['ts_code','trade_date','up_limit','down_limit'],
}
STOCK_FIELDS = ['ts_code','name','market','list_status','list_date']


def scope_id(codes):
    return hashlib.sha256(','.join(sorted(codes)).encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=FULL')
        for table, fields in FIELDS.items():
            cols = ','.join(f'{c} {"TEXT" if c in ("ts_code", "trade_date") else "REAL"}' for c in fields)
            self.conn.execute(f'CREATE TABLE IF NOT EXISTS {table} ({cols}, PRIMARY KEY(ts_code,trade_date))')
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_date ON {table}(trade_date)')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS completed (endpoint TEXT, trade_date TEXT, scope TEXT,
          PRIMARY KEY(endpoint,trade_date,scope));
        ''')
        self.conn.commit()

    def close(self):
        self.conn.close()

    def meta(self, key, default=None):
        row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key,json.dumps(value,ensure_ascii=False)))

    def done(self, endpoint, date, scope):
        return self.conn.execute('SELECT 1 FROM completed WHERE endpoint=? AND trade_date=? AND scope=?',
                                 (endpoint,date,scope)).fetchone() is not None

    def save_batch(self, endpoint, date, scope, df, replace=True):
        fields = FIELDS[endpoint]
        values = df.reindex(columns=fields).astype(object).where(pd.notna(df.reindex(columns=fields)), None)
        action = 'REPLACE' if replace else 'IGNORE'
        sql = f'INSERT OR {action} INTO {endpoint} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})'
        # 数据与完成标记在同一事务；失败或强制中断时不会留下虚假完成状态。
        with self.conn:
            self.conn.executemany(sql, values.itertuples(index=False, name=None))
            if scope is not None:
                self.conn.execute('INSERT OR REPLACE INTO completed VALUES (?,?,?)', (endpoint,date,scope))

    def load_stock(self, code, start, end):
        return pd.read_sql_query('''SELECT d.*, a.adj_factor, b.turnover_rate, b.total_mv,
            l.up_limit, l.down_limit, (l.ts_code IS NOT NULL) AS limit_record
            FROM daily d
            LEFT JOIN adj_factor a USING(ts_code,trade_date)
            LEFT JOIN daily_basic b USING(ts_code,trade_date)
            LEFT JOIN stk_limit l USING(ts_code,trade_date)
            WHERE d.ts_code=? AND d.trade_date>=? AND d.trade_date<=? ORDER BY d.trade_date''',
            self.conn, params=(code,start,end))

    def daily_codes(self, date):
        return {r[0] for r in self.conn.execute('SELECT ts_code FROM daily WHERE trade_date=?', (date,))}

    def prune(self, cutoff):
        counts = {}
        with self.conn:
            for table in FIELDS:
                counts[table] = self.conn.execute(f'DELETE FROM {table} WHERE trade_date<?', (cutoff,)).rowcount
            self.conn.execute('DELETE FROM completed WHERE trade_date<?', (cutoff,))
        self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return counts
