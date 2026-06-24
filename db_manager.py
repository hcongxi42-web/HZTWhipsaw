"""
SQLite 数据库管理模块
- 建表（stock_basic + stock_daily）
- 批量 upsert
- 增量更新
- 条件查询
"""

import sqlite3
import pandas as pd
from datetime import datetime
from contextlib import contextmanager
import config

# ==================== 建表 ====================

CREATE_STOCK_BASIC = """
CREATE TABLE IF NOT EXISTS stock_basic (
    code        TEXT PRIMARY KEY,
    code_name   TEXT,
    ipoDate     TEXT,
    outDate     TEXT,
    type        INTEGER,
    status      INTEGER
);
"""

CREATE_STOCK_DAILY = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    preclose    REAL,
    volume      REAL,
    amount      REAL,
    turn        REAL,
    tradestatus INTEGER,
    pctChg      REAL,
    peTTM       REAL,
    pbMRQ       REAL,
    psTTM       REAL,
    pcfNcfTTM   REAL,
    UNIQUE(code, date)
);
"""

# 索引
CREATE_IDX_DAILY_CODE = "CREATE INDEX IF NOT EXISTS idx_daily_code ON stock_daily(code);"
CREATE_IDX_DAILY_DATE = "CREATE INDEX IF NOT EXISTS idx_daily_date ON stock_daily(date);"
CREATE_IDX_DAILY_CODE_DATE = "CREATE INDEX IF NOT EXISTS idx_daily_code_date ON stock_daily(code, date);"

# ==================== 指数行情表 ====================
CREATE_INDEX_DAILY = """
CREATE TABLE IF NOT EXISTS index_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    preclose    REAL,
    volume      REAL,
    amount      REAL,
    pctChg      REAL,
    UNIQUE(code, date)
);
"""

CREATE_IDX_INDEX_CODE = "CREATE INDEX IF NOT EXISTS idx_index_code ON index_daily(code);"
CREATE_IDX_INDEX_DATE = "CREATE INDEX IF NOT EXISTS idx_index_date ON index_daily(date);"
CREATE_IDX_INDEX_CODE_DATE = "CREATE INDEX IF NOT EXISTS idx_index_code_date ON index_daily(code, date);"

INDEX_COLS = ['code', 'date', 'open', 'high', 'low', 'close', 'preclose',
              'volume', 'amount', 'pctChg']
INDEX_INSERT_SQL = (
    f"INSERT OR REPLACE INTO index_daily ({','.join(INDEX_COLS)}) "
    f"VALUES ({','.join('?' * len(INDEX_COLS))})"
)


def get_conn():
    """获取数据库连接"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")       # 写前日志，提升并发性能
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")       # 64MB 缓存
    return conn


@contextmanager
def db_ctx():
    """数据库连接上下文管理器，自动关闭"""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """初始化数据库：建表 + 建索引（股票 + 指数）"""
    conn = get_conn()
    try:
        conn.execute(CREATE_STOCK_BASIC)
        conn.execute(CREATE_STOCK_DAILY)
        conn.execute(CREATE_IDX_DAILY_CODE)
        conn.execute(CREATE_IDX_DAILY_DATE)
        conn.execute(CREATE_IDX_DAILY_CODE_DATE)
        conn.execute(CREATE_INDEX_DAILY)
        conn.execute(CREATE_IDX_INDEX_CODE)
        conn.execute(CREATE_IDX_INDEX_DATE)
        conn.execute(CREATE_IDX_INDEX_CODE_DATE)
        conn.commit()
        print("[db_manager] 数据库初始化完成（含指数表）")
    finally:
        conn.close()


# ==================== 写入 ====================

def _extract_basic_rows(df: pd.DataFrame):
    """向量化提取 stock_basic 写入数据（比 iterrows 快 50-100x）"""
    return list(zip(
        df['code'],
        df['code_name'],
        df['ipoDate'],
        df['outDate'],
        df['type'].astype(int),
        df['status'].astype(int),
    ))


def upsert_stock_basic(df: pd.DataFrame, conn=None):
    """
    批量 upsert 股票基本信息 (executemany 一次提交)
    df 需包含列: code, code_name, ipoDate, outDate, type, status
    conn 可复用：传入外部连接时不自动关闭，调用者负责 commit/close
    """
    if df.empty:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        data = _extract_basic_rows(df)
        conn.executemany("""
            INSERT OR REPLACE INTO stock_basic (code, code_name, ipoDate, outDate, type, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, data)
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


# 日度行情写入列（预编译，避免每次拼接）
DAILY_COLS = ['code', 'date', 'open', 'high', 'low', 'close', 'preclose',
              'volume', 'amount', 'turn', 'tradestatus', 'pctChg',
              'peTTM', 'pbMRQ', 'psTTM', 'pcfNcfTTM']

DAILY_INSERT_SQL = (
    f"INSERT OR REPLACE INTO stock_daily ({','.join(DAILY_COLS)}) "
    f"VALUES ({','.join('?' * len(DAILY_COLS))})"
)


def upsert_daily_batch(df: pd.DataFrame, conn=None):
    """
    批量 upsert 日度行情数据 (executemany 向量化写入)
    df 需包含列: code, date, open, high, low, close, preclose,
                 volume, amount, turn, tradestatus, pctChg,
                 peTTM, pbMRQ, psTTM, pcfNcfTTM
    conn 可复用：传入外部连接时不自动关闭/提交，调用者负责
    """
    if df.empty:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        work = df[DAILY_COLS].copy()
        # 向量化数值转换
        for c in DAILY_COLS[2:]:
            work[c] = pd.to_numeric(work[c], errors='coerce')

        conn.executemany(DAILY_INSERT_SQL,
                         work.itertuples(index=False, name=None))
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def upsert_index_batch(df: pd.DataFrame, conn=None):
    """
    批量 upsert 指数日度行情数据
    df 需包含列: code, date, open, high, low, close, preclose,
                 volume, amount, pctChg
    """
    if df.empty:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        work = df[INDEX_COLS].copy()
        for c in INDEX_COLS[2:]:
            work[c] = pd.to_numeric(work[c], errors='coerce')

        conn.executemany(INDEX_INSERT_SQL,
                         work.itertuples(index=False, name=None))
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


# ==================== 查询 ====================

def get_latest_date():
    """获取股票日度数据中最新交易日，如无数据/表不存在返回 None"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()
        return row[0] if row and row[0] else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def get_index_latest_date(code: str = None):
    """获取指数日度数据中最新交易日，如无数据返回 None"""
    conn = get_conn()
    try:
        if code:
            row = conn.execute(
                "SELECT MAX(date) FROM index_daily WHERE code = ?", (code,)
            ).fetchone()
        else:
            row = conn.execute("SELECT MAX(date) FROM index_daily").fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_stock_list(types=(1,), status=(1,)):
    """
    获取股票列表
    默认: type=1 (A股), status=1 (上市)
    """
    conn = get_conn()
    try:
        types = tuple(types)
        status = tuple(status)
        placeholders_t = ','.join('?' * len(types))
        placeholders_s = ','.join('?' * len(status))
        df = pd.read_sql_query(
            f"SELECT code, code_name, ipoDate, outDate, type, status "
            f"FROM stock_basic "
            f"WHERE type IN ({placeholders_t}) AND status IN ({placeholders_s})",
            conn, params=list(types) + list(status)
        )
        return df
    finally:
        conn.close()


def get_all_stock_codes(types=(1,), status=(1,)):
    """获取所有符合条件的股票代码列表"""
    df = get_stock_list(types, status)
    return df['code'].tolist() if not df.empty else []


def query_daily(codes=None, start=None, end=None):
    """
    查询日度行情数据
    - codes: 股票代码列表，None=全部
    - start: 起始日期 (str, 'YYYY-MM-DD')
    - end:   终止日期 (str, 'YYYY-MM-DD')
    返回 DataFrame
    """
    conn = get_conn()
    try:
        sql = "SELECT * FROM stock_daily WHERE 1=1"
        params = []

        if codes:
            placeholders = ','.join('?' for _ in codes)
            sql += f" AND code IN ({placeholders})"
            params.extend(codes)

        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)

        sql += " ORDER BY date, code"

        df = pd.read_sql_query(sql, conn, params=params)
        return df
    finally:
        conn.close()


def query_index(codes=None, start=None, end=None):
    """
    查询指数日度行情数据
    - codes: 指数代码列表，None=全部
    - start: 起始日期 (str, 'YYYY-MM-DD')
    - end:   终止日期 (str, 'YYYY-MM-DD')
    返回 DataFrame
    """
    conn = get_conn()
    try:
        sql = "SELECT * FROM index_daily WHERE 1=1"
        params = []

        if codes:
            placeholders = ','.join('?' for _ in codes)
            sql += f" AND code IN ({placeholders})"
            params.extend(codes)

        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)

        sql += " ORDER BY date, code"

        df = pd.read_sql_query(sql, conn, params=params)
        return df
    finally:
        conn.close()


def get_available_dates():
    """获取数据库中所有交易日"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM stock_daily ORDER BY date"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def count_records():
    """统计数据库记录数"""
    conn = get_conn()
    try:
        basic = conn.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
        daily = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        codes = conn.execute("SELECT COUNT(DISTINCT code) FROM stock_daily").fetchone()[0]
        dates = conn.execute("SELECT COUNT(DISTINCT date) FROM stock_daily").fetchone()[0]
        index = conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0]
        index_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM index_daily").fetchone()[0]
        return {'stock_basic': basic, 'stock_daily': daily,
                'unique_codes': codes, 'unique_dates': dates,
                'index_daily': index, 'index_codes': index_codes}
    finally:
        conn.close()


# ==================== 增量更新 ====================

def need_update():
    """
    判断是否需要更新数据
    返回 (need_update: bool, latest_date: str or None)
    """
    latest = get_latest_date()
    if latest is None:
        return True, None

    today = datetime.now().strftime('%Y-%m-%d')
    return latest < today, latest


# ==================== 数据质量校验 ====================

def validate_daily_quality() -> dict:
    """
    检查 stock_daily 表的数据质量。
    返回异常统计，供调用者决定是否处理。
    """
    conn = get_conn()
    try:
        issues = {}

        # close <= 0
        row = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE close <= 0 OR close IS NULL"
        ).fetchone()
        issues['close_invalid'] = row[0]

        # high < low
        row = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE high < low"
        ).fetchone()
        issues['high_lt_low'] = row[0]

        # volume < 0
        row = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE volume < 0"
        ).fetchone()
        issues['volume_negative'] = row[0]

        # 重复键（同 code + date）
        row = conn.execute(
            "SELECT COUNT(*) FROM (SELECT code, date, COUNT(*) as cnt "
            "FROM stock_daily GROUP BY code, date HAVING cnt > 1)"
        ).fetchone()
        issues['duplicate_keys'] = row[0]

        total_issues = sum(issues.values())
        if total_issues > 0:
            print(f"[db_manager] 数据质量检查: {total_issues} 条异常 (详情: {issues})")
        return issues
    finally:
        conn.close()
