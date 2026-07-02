"""
生成静态站点文件 (GitHub Pages)
=================================
从 screening_history 表导出 JSON 数据文件到 docs/ 目录
"""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sqlite3
import pandas as pd
import numpy as np

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs')
DATA_DIR = os.path.join(DOCS_DIR, 'data')
HISTORY_DIR = os.path.join(DATA_DIR, 'history')
CONCEPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'concept.csv')


def load_concept_map():
    """加载概念映射"""
    if not os.path.exists(CONCEPT_PATH):
        return {}
    df = pd.read_csv(CONCEPT_PATH, encoding='gbk', dtype=str)
    cmp_map = {}
    for _, row in df.iterrows():
        cmp_map[row['permno'].strip()] = row['concept_name'].strip()
    return cmp_map


def strip_code(code):
    for prefix in ['sz.', 'sh.', 'bj.']:
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def safe_round(val, ndigits=1, default=0.0):
    """安全 round: 将 NaN/Inf/None 替换为 default，避免 JSON 中出现 NaN（浏览器 JSON.parse 拒绝）"""
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(v):
        return default
    return round(v, ndigits)


def get_concept(code, cmp_map):
    raw = strip_code(code)
    return cmp_map.get(raw, '—')


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    # 加载概念映射
    print('加载概念映射...')
    cmp_map = load_concept_map()
    print(f'  概念数: {len(cmp_map)}')

    # 加载股票名称
    print('加载股票名称...')
    names = pd.read_sql_query("SELECT code, code_name FROM stock_basic", conn)
    name_map = names.set_index('code')['code_name'].to_dict()
    print(f'  股票数: {len(name_map)}')

    # 获取所有可用日期 (≥20条即可展示，标注质量)
    print('获取日期列表...')
    dates_df = pd.read_sql_query("""
        SELECT target_date, COUNT(*) as cnt, ROUND(AVG(total),1) as avg_score,
               ROUND(MAX(total),1) as max_score, SUM(is_limit_up_today) as limit_count,
               SUM(CASE WHEN trend_class='trend' THEN 1 ELSE 0 END) as trend_cnt,
               SUM(CASE WHEN trend_class='choppy' OR trend_class IS NULL THEN 1 ELSE 0 END) as choppy_cnt
        FROM screening_history
        GROUP BY target_date HAVING cnt >= 20
        ORDER BY target_date DESC
    """, conn)
    dates = dates_df['target_date'].tolist()
    print(f'  可用日期: {len(dates)} ({dates[-1]} ~ {dates[0]})')

    # 获取每个日期的 stock_daily 原始股票数，用于数据质量标识
    daily_counts = {}
    try:
        count_rows = pd.read_sql_query("""
            SELECT date, COUNT(DISTINCT code) as stock_count
            FROM stock_daily
            WHERE date IN (SELECT DISTINCT target_date FROM screening_history)
            GROUP BY date
        """, conn)
        daily_counts = dict(zip(count_rows['date'], count_rows['stock_count']))
    except Exception:
        pass

    # 写入日期列表 (含数据质量)
    stats_dict = dates_df.set_index('target_date').to_dict(orient='index')
    for d in stats_dict:
        sc = daily_counts.get(d, 0)
        if sc >= 4000:
            quality = 'full'
        elif sc >= 1000:
            quality = 'partial'
        elif sc > 0:
            quality = 'sparse'
        else:
            quality = 'unknown'
        stats_dict[d]['stock_count'] = sc
        stats_dict[d]['quality'] = quality

    with open(os.path.join(DATA_DIR, 'dates.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'dates': dates,
            'latest': dates[0],
            'stats': stats_dict
        }, f, ensure_ascii=False)
    print(f'  ✓ dates.json')

    # 逐日期导出
    print('导出各日期数据...')
    all_concepts = set()
    for date in dates:
        df = pd.read_sql_query("""
            SELECT code, rank, total, washout_quality, probe_test, ma_convergence,
                   stock_strength, launch_readiness, volume_price_health,
                   latest_close, latest_pctChg, is_limit_up_today,
                   recent_limit_days, probe_count, days_since_probe,
                   trend_class, trend_class_score
            FROM screening_history
            WHERE target_date = ?
            ORDER BY rank
        """, conn, params=(date,))

        stocks = []
        for _, row in df.iterrows():
            code = row['code']
            concept = get_concept(code, cmp_map)
            all_concepts.add(concept)
            stocks.append({
                'code': code,
                'name': name_map.get(code, '?')[:8],
                'rank': int(row['rank']),
                'total': safe_round(row['total'], 1),
                'washout_quality': safe_round(row['washout_quality'], 1),
                'probe_test': safe_round(row['probe_test'], 1),
                'ma_convergence': safe_round(row['ma_convergence'], 1),
                'stock_strength': safe_round(row.get('stock_strength', 0) or 0, 1),
                'launch_readiness': safe_round(row['launch_readiness'], 1),
                'volume_price_health': safe_round(row.get('volume_price_health', 0) or 0, 1),
                'latest_close': safe_round(row['latest_close'], 2),
                'latest_pctChg': safe_round(row['latest_pctChg'], 2),
                'is_limit_up_today': bool(int(row['is_limit_up_today'])),
                'recent_limit_days': int(row['recent_limit_days']),
                'probe_count': int(row['probe_count']),
                'days_since_probe': int(row['days_since_probe']),
                'concept': concept,
                'trend_class': row.get('trend_class') or None,
                'trend_class_score': safe_round(row.get('trend_class_score', 0) or 0, 0),
            })

        with open(os.path.join(DATA_DIR, f'{date}.json'), 'w', encoding='utf-8') as f:
            json.dump({'date': date, 'total': len(stocks), 'stocks': stocks}, f, ensure_ascii=False)
        print(f'  ✓ {date}: {len(stocks)} stocks')

    # 写入概念列表
    concepts = sorted([i for i in all_concepts if i != '—'])
    with open(os.path.join(DATA_DIR, 'concepts.json'), 'w', encoding='utf-8') as f:
        json.dump({'concepts': concepts}, f, ensure_ascii=False)
    print(f'  ✓ concepts.json ({len(concepts)} concepts)')

    # 导出各股票历史数据 + K线
    print('导出股票历史数据 + K线...')
    all_codes = pd.read_sql_query(
        "SELECT DISTINCT code FROM screening_history", conn
    )['code'].tolist()
    print(f'  共 {len(all_codes)} 只有历史记录的股票')

    # 批量加载 K 线数据 (最近 60 天, 一次性查询)
    kline_df = pd.read_sql_query("""
        SELECT code, date, open, high, low, close, volume, pctChg, turn
        FROM stock_daily
        WHERE date >= (SELECT DATE(MAX(date), '-60 days') FROM stock_daily)
        ORDER BY code, date
    """, conn)
    kline_by_code = {}
    for code, grp in kline_df.groupby('code'):
        kline_by_code[code] = grp.tail(30).to_dict('records')  # 只保留最近30天

    for i, code in enumerate(all_codes):
        df = pd.read_sql_query("""
            SELECT target_date, rank, total, washout_quality, probe_test, ma_convergence,
                   stock_strength, launch_readiness, volume_price_health,
                   trend_class, trend_class_score
            FROM screening_history WHERE code = ? ORDER BY target_date
        """, conn, params=(code,))

        history = [
            {
                'date': row['target_date'],
                'rank': int(row['rank']),
                'total': safe_round(row['total'], 1),
                'washout_quality': safe_round(row['washout_quality'], 1),
                'probe_test': safe_round(row['probe_test'], 1),
                'ma_convergence': safe_round(row['ma_convergence'], 1),
                'stock_strength': safe_round(row.get('stock_strength', 0) or 0, 1),
                'launch_readiness': safe_round(row['launch_readiness'], 1),
                'volume_price_health': safe_round(row.get('volume_price_health', 0) or 0, 1),
                'trend_class': row.get('trend_class') or None,
                'trend_class_score': safe_round(row.get('trend_class_score', 0) or 0, 0),
            }
            for _, row in df.iterrows()
        ]

        if history:
            fname = strip_code(code)
            # K 线数据 (最近 30 天)
            kline_raw = kline_by_code.get(code, [])
            kline = [
                {
                    'date': r['date'],
                    'open': safe_round(r['open'], 2),
                    'high': safe_round(r['high'], 2),
                    'low': safe_round(r['low'], 2),
                    'close': safe_round(r['close'], 2),
                    'volume': int(r['volume']),
                    'pctChg': safe_round(r.get('pctChg'), 2) if r.get('pctChg') is not None else 0,
                    'turn': safe_round(r.get('turn'), 2) if r.get('turn') is not None else 0,
                }
                for r in kline_raw
            ]

            with open(os.path.join(HISTORY_DIR, f'{fname}.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'code': code,
                    'name': name_map.get(code, '?'),
                    'concept': get_concept(code, cmp_map),
                    'history': history,
                    'kline': kline,
                }, f, ensure_ascii=False)

        if (i + 1) % 500 == 0:
            print(f'  ... {i+1}/{len(all_codes)}')

    print(f'  ✓ {len(all_codes)} stock history files (with K-line)')

    # ── 清理僵尸文件（DB中已删除但JSON残留的日期/股票）──
    dead_dates = 0
    for fn in os.listdir(DATA_DIR):
        if fn.endswith('.json') and fn not in ('dates.json', 'concepts.json', 'storage.json'):
            date_name = fn.replace('.json', '')
            if date_name not in dates:
                os.remove(os.path.join(DATA_DIR, fn))
                dead_dates += 1
    if dead_dates:
        print(f'  ✓ 清理了 {dead_dates} 个僵尸日期文件')

    dead_stocks = 0
    valid_codes = set(strip_code(c) for c in all_codes)
    for fn in os.listdir(HISTORY_DIR):
        if fn.endswith('.json'):
            code_name = fn.replace('.json', '')
            if code_name not in valid_codes:
                os.remove(os.path.join(HISTORY_DIR, fn))
                dead_stocks += 1
    if dead_stocks:
        print(f'  ✓ 清理了 {dead_stocks} 个僵尸股票文件')

    conn.close()

    # ── 生成存储监控数据 ──
    import time as _time
    db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
    docs_size_mb = _dir_size(DOCS_DIR)
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data_seed.db')
    seed_size_mb = os.path.getsize(seed_path) / (1024 * 1024) if os.path.exists(seed_path) else 0
    code_size_mb = _dir_size(os.path.dirname(os.path.abspath(__file__))) - db_size_mb - docs_size_mb - seed_size_mb
    if code_size_mb < 0:
        code_size_mb = 0.5  # fallback

    storage = {
        'repo_mb': round(seed_size_mb + docs_size_mb + code_size_mb, 1),   # GitHub 仓库大小（不含DB）
        'cache_mb': round(db_size_mb, 1),                                    # Actions 缓存（= DB文件）
        'cache_limit_mb': 10240,                                             # GitHub Actions cache 总额度
        'pages_mb': round(docs_size_mb, 1),                                  # GitHub Pages 站点
        'db_mb': round(db_size_mb, 1),
        'updated': _time.strftime('%Y-%m-%d %H:%M UTC', _time.gmtime()),
    }
    storage_path = os.path.join(DATA_DIR, 'storage.json')
    with open(storage_path, 'w', encoding='utf-8') as f:
        json.dump(storage, f, ensure_ascii=False)
    print(f'  ✓ storage.json ({db_size_mb:.0f} MB DB, {docs_size_mb:.0f} MB Pages)')

    print(f'\n静态站点文件已生成到: {DOCS_DIR}')
    print(f'总大小: {docs_size_mb:.1f} MB')


def _dir_size(path):
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total / (1024 * 1024)


if __name__ == '__main__':
    main()
