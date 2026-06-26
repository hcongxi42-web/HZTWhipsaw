"""
baostock API 数据获取模块
- 断点续传：已获取的股票自动跳过
- 自动重试：连接断开时重试 3 次
- 限速控制：单只间隔 + 批量间歇，避免被远端踢掉
"""

import baostock as bs
import pandas as pd
from datetime import datetime, timedelta
from tqdm import tqdm
import time
import json
import os
from typing import Tuple
import config
import db_manager


# ==================== 断点续传文件 ====================
PROGRESS_FILE = os.path.join(config.BASE_DIR, '.fetch_progress.json')

# ==================== 指数配置 ====================
# 支持一次性抓取多个宽基指数，便于后续扩展
INDEX_CODES = {
    'sh.000300': 'CSI300',   # 沪深300
    # 'sh.000016': 'SZ50',   # 上证50（可选）
    # 'sh.000905': 'CSI500', # 中证500（可选）
}
INDEX_FIELDS = 'date,code,open,high,low,close,preclose,volume,amount,pctChg'


def _load_progress(start_date: str = '', end_date: str = '') -> dict:
    """
    加载已完成的股票代码集合。
    若日期范围变更，则重置进度（旧进度不适用于新范围）。
    """
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            progress = json.load(f)
        # 日期范围校验：范围变了就重置
        if progress.get('start_date') == start_date and progress.get('end_date') == end_date:
            return progress
        else:
            print(f"  [progress] 日期范围变更，重置断点续传进度")
    return {'done_codes': [], 'total_fetched': 0, 'last_code': None,
            'start_date': start_date, 'end_date': end_date, 'version': 1}


def _save_progress(progress: dict):
    """保存进度（含日期范围）"""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)


# ==================== 登录管理 ====================

def _login():
    """登录 baostock，带重试"""
    for attempt in range(3):
        lg = bs.login()
        if lg.error_code == '0':
            return True
        print(f"  [RETRY] 登录失败 (attempt {attempt+1}/3): {lg.error_msg}")
        time.sleep(2 ** attempt)
    raise ConnectionError("baostock 登录失败，已重试 3 次")


def _logout():
    """登出"""
    try:
        bs.logout()
    except:
        pass


# ==================== 指数行情获取 ====================

def fetch_index_daily_single(code: str, start_date: str, end_date: str,
                             max_retries: int = 3) -> pd.DataFrame:
    """
    获取单只指数日度 K 线（如 sh.000300 沪深300）
    """
    for attempt in range(max_retries):
        try:
            rs = bs.query_history_k_data_plus(
                code,
                INDEX_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency='d',
                adjustflag='3'  # 指数无需复权，但 baostock 要求传入；3 表示不复权
            )

            if rs.error_code != '0':
                if attempt < max_retries - 1:
                    time.sleep(1 + attempt)
                    continue
                return pd.DataFrame()

            df = rs.get_data()
            if df.empty:
                return df
            return df

        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  [RETRY] {code} attempt {attempt+1}/{max_retries}, wait {wait}s: {e}")
                time.sleep(wait)
            else:
                return pd.DataFrame()

    return pd.DataFrame()


def fetch_and_store_index(start_date: str = None, end_date: str = None):
    """
    批量获取并存储指数行情数据。
    自动判断数据库中已有数据，只做增量更新。
    """
    if start_date is None:
        start_date = config.START_DATE
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    _login()
    db_conn = db_manager.get_conn()
    try:
        for code, name in INDEX_CODES.items():
            db_latest = db_manager.get_index_latest_date(code)
            if db_latest is not None and db_latest >= end_date:
                print(f"[data_fetcher] {name}({code}) 数据已最新，跳过")
                continue

            fetch_start = start_date if db_latest is None else (
                datetime.strptime(db_latest, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            print(f"[data_fetcher] 获取指数 {name}({code}): {fetch_start} ~ {end_date}")
            df = fetch_index_daily_single(code, fetch_start, end_date)
            if not df.empty:
                db_manager.upsert_index_batch(df, conn=db_conn)
                db_conn.commit()
                print(f"[data_fetcher] {name}({code}) 写入 {len(df)} 条")
            else:
                print(f"[data_fetcher] {name}({code}) 无数据")
            time.sleep(0.5)
    finally:
        db_conn.close()
        _logout()


# ==================== 股票列表 ====================

def fetch_stock_basic():
    """获取全部 A 股列表（排除指数/B股/基金等）"""
    rs = bs.query_stock_basic(code_name='')
    if rs.error_code != '0':
        raise RuntimeError(f"query_stock_basic 失败: {rs.error_msg}")

    data = rs.get_data()

    # 筛选 A 股（type=1, status=1）
    a_stocks = data[(data['type'] == '1') & (data['status'] == '1')].copy()

    def _is_valid_a_stock(code):
        if not code:
            return False
        parts = code.split('.')
        if len(parts) != 2:
            return False
        market, symbol = parts
        if market not in ('sh', 'sz'):
            return False
        if not symbol.isdigit() or len(symbol) != 6:
            return False
        if market == 'sh' and symbol.startswith(('60', '68')):
            return True
        if market == 'sz' and symbol.startswith(('00', '30')):
            return True
        return False

    a_stocks = a_stocks[a_stocks['code'].apply(_is_valid_a_stock)].copy()
    print(f"[data_fetcher] {len(a_stocks)} 只 A 股")
    return a_stocks


# ==================== 日度行情（单只 + 重试） ====================

def fetch_daily_single(code: str, start_date: str, end_date: str,
                       max_retries: int = 3) -> Tuple[pd.DataFrame, bool]:
    """
    获取单只股票日度 K 线，带自动重试

    返回: (df, is_api_error)
      - df: 数据 DataFrame（可能为空）
      - is_api_error: True 表示 API 层面报错（非 '0'），False 表示成功或仅无数据
    """
    for attempt in range(max_retries):
        try:
            rs = bs.query_history_k_data_plus(
                code,
                config.DAILY_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency='d',
                adjustflag=config.ADJUST_FLAG
            )

            if rs.error_code != '0':
                if attempt < max_retries - 1:
                    time.sleep(1 + attempt)
                    continue
                # API 持续报错 → 真失败
                return pd.DataFrame(), True

            # API 调用成功（error_code == '0'）——无论有无数据都不是错误
            df = rs.get_data()

            if 'tradestatus' in df.columns:
                df = df[df['tradestatus'] == '1'].copy()
            return df if not df.empty else pd.DataFrame(), False

        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                if attempt >= 1:
                    print(f"  [RETRY] {code} attempt {attempt+1}/{max_retries}, wait {wait}s: {e}")
                time.sleep(wait)
            else:
                return pd.DataFrame(), True

    return pd.DataFrame(), True


# ==================== 断点续传逻辑 ====================

def _stock_is_complete(code: str, start_date: str, end_date: str) -> bool:
    """
    检查某只股票在数据库中是否已覆盖完整的日期范围。
    用该股票在 DB 中的实际最早日期估算预期交易日数，避免新股/次新股误判。
    """
    conn = db_manager.get_conn()
    try:
        row = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM stock_daily WHERE code = ?",
            (code,)
        ).fetchone()
        if row[0] is None:
            return False
        db_start, db_end, count = row

        # 关键：如果 DB 最新日期早于请求的起始日期，说明有增量日期需要拉取
        if db_end < start_date:
            return False

        # 以该股票实际最早日期为起点估算预期交易日
        expected_days = _count_trading_days(db_start, end_date)
        from datetime import timedelta
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        tolerance_dt = end_dt - timedelta(days=2)
        end_ok = db_end >= tolerance_dt.strftime('%Y-%m-%d')
        return end_ok and count >= max(5, expected_days * 0.7)
    finally:
        conn.close()


def _count_trading_days(start: str, end: str) -> int:
    """估算交易日数（全年约 250 天）"""
    d1 = datetime.strptime(start, '%Y-%m-%d')
    d2 = datetime.strptime(end, '%Y-%m-%d')
    calendar_days = (d2 - d1).days
    return int(calendar_days * 250 / 365)


# ==================== 快速增量获取（短日期范围专用） ====================

def fetch_incremental_dates(start_date: str, end_date: str):
    """
    轻量级增量获取：仅用于拉取少量新日期（1-5天）的日度行情。
    - 不做 _stock_is_complete 逐只检查（日期增量时所有股票都需要新数据）
    - 不写断点续传文件（短任务中断重跑即可）
    - 更快间隔 + 单会话 + 无批次重登
    - 1天数据 ≈ 5-8分钟完成（vs 全量流程 25-30分钟）
    """
    SLEEP_SINGLE = 0.05       # 增量拉取间隔更短（baostock 对小查询更宽松）
    DB_COMMIT_EVERY = 100     # 更大的提交批次
    THROTTLE_COOLDOWN = 60    # 限流冷却

    conn = db_manager.get_conn()
    try:
        codes = db_manager.get_all_stock_codes()
    finally:
        conn.close()

    if not codes:
        print("[fetch_incr] 无股票代码，跳过")
        return 0, 0

    print(f"[fetch_incr] 快速增量: {start_date} ~ {end_date}, {len(codes)} 只股票")

    _login()
    db_conn = db_manager.get_conn()

    success = 0
    fail = 0
    empty = 0
    consecutive_fails = 0
    since_commit = 0

    for i, code in enumerate(codes):
        try:
            df, is_api_error = fetch_daily_single(code, start_date, end_date)
            if not df.empty:
                db_manager.upsert_daily_batch(df, conn=db_conn)
                since_commit += 1
                success += 1
                consecutive_fails = 0
            elif is_api_error:
                fail += 1
                consecutive_fails += 1
            else:
                empty += 1
        except Exception:
            fail += 1
            consecutive_fails += 1

        time.sleep(SLEEP_SINGLE)

        # 定期提交
        if since_commit >= DB_COMMIT_EVERY:
            db_conn.commit()
            since_commit = 0

        # 限流检测
        if consecutive_fails >= 5:
            print(f"  [THROTTLE] 连续 {consecutive_fails} 次错误，冷却 {THROTTLE_COOLDOWN}s...")
            db_conn.commit()
            since_commit = 0
            _logout()
            time.sleep(THROTTLE_COOLDOWN)
            try:
                bs.login()
            except Exception:
                pass
            consecutive_fails = 0

        # 进度日志 (每500只)
        if (i + 1) % 500 == 0:
            pct = (i + 1) / len(codes) * 100
            print(f"  ... {i+1}/{len(codes)} ({pct:.0f}%) 成功={success} 空={empty} 失败={fail}")

    db_conn.commit()
    db_conn.close()
    _logout()

    print(f"[fetch_incr] 完成: 成功={success}, 无数据={empty}, 失败={fail}, "
          f"覆盖率={success}/{len(codes)} ({success/len(codes)*100:.1f}%)")
    return success, fail


# ==================== 批量获取（断点续传版） ====================

def fetch_and_store_all(start_date: str = None, end_date: str = None):
    """
    批量获取所有 A 股日度数据，支持断点续传。
    - 已完成的股票自动跳过
    - 每 100 只股票批次间重新登录一次
    - 连接断开自动重试
    """
    if start_date is None:
        start_date = config.START_DATE
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    print(f"[data_fetcher] 日期范围: {start_date} ~ {end_date}")

    # Step 1: 获取股票列表并入库
    _login()
    stocks = fetch_stock_basic()
    _logout()
    db_manager.upsert_stock_basic(stocks)
    codes = stocks['code'].tolist()

    # Step 2: 加载断点续传进度（日期范围变更时自动重置）
    progress = _load_progress(start_date=start_date, end_date=end_date)
    done_set = set(progress.get('done_codes', []))

    # Step 3: 过滤已完成的股票
    todo_codes = []
    skipped_db = 0
    skipped_progress = 0
    for code in codes:
        if code in done_set:
            skipped_progress += 1
            continue
        if _stock_is_complete(code, start_date, end_date):
            done_set.add(code)
            skipped_db += 1
            continue
        todo_codes.append(code)

    print(f"[data_fetcher] 总股票: {len(codes)}, 已完成(DB): {skipped_db}, "
          f"已完成(progress): {skipped_progress}, 待获取: {len(todo_codes)}")

    if not todo_codes:
        print("[data_fetcher] 所有股票数据已完整，无需获取")
        _save_progress({'done_codes': list(done_set), 'total_fetched': len(done_set),
                        'start_date': start_date, 'end_date': end_date})
        return len(done_set), 0

    # Step 4: 分批获取（抗限流版）
    BATCH_SIZE = 200           # 增大批次，减少重登次数
    SLEEP_SINGLE = 0.15        # 单只请求间隔（秒）
    SLEEP_BATCH = 10           # 批次间休息（秒），不必过长
    THROTTLE_COOLDOWN = 120    # 被限流后冷却时间（秒）
    HARD_COOLDOWN = 300        # 重登失败后长冷却（秒）
    DB_COMMIT_EVERY = 50       # 每 N 只股票提交一次数据库（防丢进度）

    success_count = 0
    fail_count = 0
    empty_count = 0            # 新增：API 成功但无数据（退市/停牌），不计入失败
    consecutive_fails = 0      # 仅 API 真错误才累加，触发限流冷却
    since_last_commit = 0      # 自上次提交以来的写入计数

    _login()  # 首次登录

    # 打开一个长连接贯穿整批（大幅减少连接开关开销）
    db_conn = db_manager.get_conn()

    for batch_start in range(0, len(todo_codes), BATCH_SIZE):
        batch = todo_codes[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(todo_codes) + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n[data_fetcher] 批次 {batch_num}/{total_batches} "
              f"({batch_start+1}-{batch_start+len(batch)}), "
              f"已完成 {success_count}, 失败 {fail_count}, 总计 {len(done_set)}")

        for code in tqdm(batch, desc=f"Batch {batch_num}", unit="stock", leave=False):
            try:
                df, is_api_error = fetch_daily_single(code, start_date, end_date)
                if not df.empty:
                    # 有数据 → 写入 DB
                    db_manager.upsert_daily_batch(df, conn=db_conn)
                    since_last_commit += 1
                    success_count += 1
                    done_set.add(code)
                    consecutive_fails = 0  # 重置连续失败计数
                elif is_api_error:
                    # 真 API 错误（error_code != '0' 且重试耗尽）
                    fail_count += 1
                    consecutive_fails += 1
                else:
                    # API 成功但该股票在此日期范围无数据（退市/停牌/周末）
                    # 不计入失败，不触发限流——这是正常现象
                    empty_count += 1
                    # 如果连续大量空返回，可能是该股已退市，加入 done 避免反复查询
                    # 但保留在 fail_count 之外，不影响限流判断

                time.sleep(SLEEP_SINGLE)

            except Exception as e:
                # 网络/程序异常 → 真失败
                fail_count += 1
                consecutive_fails += 1
                if consecutive_fails <= 3:
                    tqdm.write(f"  [ERR] {code}: {e}")

            # 每 N 只提交一次 DB + 存进度（防止意外中断丢进度）
            if since_last_commit >= DB_COMMIT_EVERY:
                db_conn.commit()
                _save_progress({'done_codes': list(done_set), 'total_fetched': len(done_set),
                                'last_code': code, 'start_date': start_date, 'end_date': end_date})
                since_last_commit = 0

            # ===== 限流检测：仅 API 真错误（非空数据）触发 =====
            if consecutive_fails >= 5:
                tqdm.write(f"  [THROTTLE] 连续 {consecutive_fails} 次 API 错误，疑似限流，冷却 {THROTTLE_COOLDOWN}s...")
                db_conn.commit()  # 限流前先提交已有数据
                _save_progress({'done_codes': list(done_set), 'total_fetched': len(done_set),
                                'last_code': code, 'start_date': start_date, 'end_date': end_date})
                since_last_commit = 0
                _logout()
                time.sleep(THROTTLE_COOLDOWN)

                # 尝试重新登录，失败则等更久
                login_ok = False
                for login_attempt in range(3):
                    try:
                        lg = bs.login()
                        if lg.error_code == '0':
                            login_ok = True
                            break
                    except Exception:
                        pass
                    tqdm.write(f"  [LOGIN RETRY] 登录重试 {login_attempt+1}/3，等待 {HARD_COOLDOWN}s...")
                    time.sleep(HARD_COOLDOWN)

                if not login_ok:
                    tqdm.write(f"  [FATAL] 无法重新登录，继续尝试剩余股票...")
                    consecutive_fails = 0
                    continue

                consecutive_fails = 0

        # 每批结束：提交 DB + 保存进度
        db_conn.commit()
        since_last_commit = 0
        _save_progress({'done_codes': list(done_set), 'total_fetched': len(done_set),
                         'last_code': batch[-1] if batch else None,
                         'start_date': start_date, 'end_date': end_date})

        # 批次间休息（最后一批不休息）
        if batch_start + BATCH_SIZE < len(todo_codes):
            print(f"  [REST] 批次休息 {SLEEP_BATCH}s...")
            _logout()
            time.sleep(SLEEP_BATCH)
            _login()

    db_conn.close()
    _logout()

    print(f"\n[data_fetcher] 本轮完成: 成功 {success_count}, 无数据 {empty_count}, "
          f"失败 {fail_count}, 总计已完成 {len(done_set)}/{len(codes)}")
    return success_count, fail_count


def _check_stock_coverage() -> tuple:
    """
    检查股票覆盖度
    返回 (total_in_basic, total_with_data, missing_codes)
    """
    conn = db_manager.get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM stock_basic WHERE type=1 AND status=1").fetchone()[0]
        codes_with_data = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM stock_daily"
        ).fetchone()[0]
        return total, codes_with_data, total - codes_with_data
    finally:
        conn.close()


def update_data(update_index: bool = True):
    """
    增量更新：先补全缺失股票，再增量获取新日期，最后更新指数。
    - 有股票未获取 → 断点续传补全（使用 DB 实际最新日期）
    - 日期不是最新 → 增量获取新日期数据
    - 数据库为空 → 全量获取
    - update_index: 是否同时更新指数数据（默认 True）
    """
    end = datetime.now().strftime('%Y-%m-%d')

    # 确保表结构存在（首次运行/空库）
    db_manager.init_db()

    db_latest = db_manager.get_latest_date()

    # 1. 数据库为空 → 全量获取
    if db_latest is None:
        print(f"[data_fetcher] 数据库为空，全量获取: {config.START_DATE} ~ {end}")
        stock_res = fetch_and_store_all(start_date=config.START_DATE, end_date=end)
        if update_index:
            fetch_and_store_index(start_date=config.START_DATE, end_date=end)
        return stock_res

    # 2. 检查股票覆盖度（用 DB 实际最新日期，避免今天没数据误判）
    total_stocks, stocks_with_data, missing = _check_stock_coverage()
    print(f"[data_fetcher] 股票覆盖度: {stocks_with_data}/{total_stocks} (缺 {missing} 只)")

    if missing > 0:
        print(f"[data_fetcher] 断点续传补全 (目标覆盖至 {db_latest})...")
        stock_res = fetch_and_store_all(start_date=config.START_DATE, end_date=db_latest)
        if update_index:
            fetch_and_store_index(start_date=config.START_DATE, end_date=db_latest)
        return stock_res

    # 3. 股票都齐了，检查日期增量（走快速通道：短日期范围不逐只检查）
    if db_latest < end:
        start_dt = datetime.strptime(db_latest, '%Y-%m-%d') + timedelta(days=1)
        start = start_dt.strftime('%Y-%m-%d')
        delta_days = (datetime.strptime(end, '%Y-%m-%d') - datetime.strptime(start, '%Y-%m-%d')).days
        print(f"[data_fetcher] 日期增量更新: {start} ~ {end} ({delta_days+1}天)")
        # 短日期范围(≤5天)走快速增量通道，长范围走完整断点续传
        if delta_days <= 5:
            stock_res = fetch_incremental_dates(start_date=start, end_date=end)
        else:
            stock_res = fetch_and_store_all(start_date=start, end_date=end)
        if update_index:
            fetch_and_store_index(start_date=start, end_date=end)
        return stock_res

    print(f"[data_fetcher] 股票数据完整，无需更新 (latest={db_latest})")

    # 4. 即使股票数据完整，也检查指数是否需要更新
    if update_index:
        fetch_and_store_index(start_date=config.START_DATE, end_date=end)

    # 5. 更新 akshare 补充数据（概念分类 + 筹码分布 + 流通市值）
    if (getattr(config, 'AKSHARE_CONCEPT_FETCH', False) or
        getattr(config, 'AKSHARE_CYQ_FETCH', False) or
        getattr(config, 'AKSHARE_FLOAT_MV_FETCH', False)):
        print("\n[data_fetcher] --- 更新 akshare 补充数据 ---")
        try:
            import data_fetcher_akshare
            data_fetcher_akshare.update_akshare_data(start_date=config.START_DATE, end_date=end)
        except ImportError:
            print("[data_fetcher] akshare 未安装，跳过补充数据更新")
        except Exception as e:
            print(f"[data_fetcher] akshare 数据更新出错: {e}")

    return _get_latest_date_count()


# ==================== 数据完整性检查 & 回填 ====================

def _get_latest_date_count():
    """返回 (最新日期, 股票数)"""
    conn = db_manager.get_conn()
    try:
        row = conn.execute(
            "SELECT date, COUNT(DISTINCT code) FROM stock_daily GROUP BY date ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row:
            return row[0], row[1]
        return None, 0
    finally:
        conn.close()


def check_date_completeness(date_str: str, min_stocks: int = 4000) -> tuple:
    """
    检查指定日期的数据是否完整
    返回 (stock_count, is_complete)
    """
    conn = db_manager.get_conn()
    try:
        cnt = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date = ?", (date_str,)
        ).fetchone()[0]
        return cnt, cnt >= min_stocks
    finally:
        conn.close()


def backfill_recent_dates(days: int = 3, min_stocks: int = 1000):
    """
    检查最近 N 天的 stock_daily 数据完整性，补拉缺口日期。
    返回补拉成功的日期列表。
    """
    conn = db_manager.get_conn()
    try:
        # 获取最近 N 天（排除周末）
        from datetime import datetime as dt
        end = dt.now()
        check_dates = []
        d = end
        while len(check_dates) < days * 2:  # 最多回看 days*2 个日历日
            d_str = d.strftime('%Y-%m-%d')
            if d.weekday() < 5:  # 周一到周五
                check_dates.append(d_str)
            d = d - timedelta(days=1)
            if len(check_dates) >= days:
                break

        # 检查每个日期
        gap_dates = []
        for d_str in check_dates:
            cnt = conn.execute(
                "SELECT COUNT(DISTINCT code) FROM stock_daily WHERE date = ?", (d_str,)
            ).fetchone()[0]
            if cnt < min_stocks:
                gap_dates.append((d_str, cnt))
    finally:
        conn.close()

    if not gap_dates:
        print("[backfill] 最近日期数据完整，无需回填")
        return []

    filled = []
    _login()
    try:
        for d_str, cnt in gap_dates:
            print(f"[backfill] 日期 {d_str} 仅有 {cnt} 只股票，快速补拉...")
            try:
                fetch_incremental_dates(start_date=d_str, end_date=d_str)
                # 同时补拉指数
                fetch_and_store_index(start_date=d_str, end_date=d_str)
                filled.append(d_str)
                print(f"[backfill] OK {d_str} 补拉完成")
            except Exception as e:
                print(f"[backfill] FAIL {d_str} 补拉失败: {e}")
            time.sleep(1)
    finally:
        _logout()

    return filled


def fetch_with_retry(max_attempts: int = 2, wait_minutes: int = 10):
    """
    一站式数据更新：先回填缺口 → 增量拉新 → 完整性检查 → 自适应重试。
    不强制要求「今天」有数据，检查 DB 实际最新日期是否完整。
    """
    import time as time_mod

    # 先回填最近 3 天的数据缺口（仅补已有数据中的空洞，不等未发布日期）
    backfill_recent_dates(days=3, min_stocks=1000)

    for attempt in range(1, max_attempts + 1):
        print(f"\n[fetch_with_retry] 第 {attempt}/{max_attempts} 次拉取...")
        update_data(update_index=True)

        # ── 智能检查：用 DB 实际最新日期 ──
        latest_date, cnt = _get_latest_date_count()
        print(f"[fetch_with_retry] DB 最新日期: {latest_date}, {cnt} 只股票")

        if latest_date is None:
            print("[fetch_with_retry] DB 为空")
        elif cnt >= 4000:
            print(f"[fetch_with_retry] {latest_date} 数据完整 ({cnt} 只)，继续")
            return True
        elif cnt >= 1000:
            print(f"[fetch_with_retry] {latest_date} 部分数据 ({cnt} 只)")
        else:
            print(f"[fetch_with_retry] {latest_date} 仅 {cnt} 只（可能节假日）")

        if attempt < max_attempts:
            wait = 5 if cnt >= 2000 else (10 if cnt >= 500 else wait_minutes)
            print(f"[fetch_with_retry] 等待 {wait} 分钟后重试...")
            time_mod.sleep(wait * 60)

    print(f"[fetch_with_retry] {max_attempts} 次后接受当前数据状态，继续执行")
    return False
