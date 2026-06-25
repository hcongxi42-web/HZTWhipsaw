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
INDUSTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'industry.csv')


def load_industry_map():
    """加载行业映射"""
    if not os.path.exists(INDUSTRY_PATH):
        return {}
    df = pd.read_csv(INDUSTRY_PATH, encoding='gbk', dtype=str)
    ind_map = {}
    for _, row in df.iterrows():
        ind_map[row['permno'].strip()] = row['industry_name'].strip()
    return ind_map


def strip_code(code):
    for prefix in ['sz.', 'sh.', 'bj.']:
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def get_industry(code, ind_map):
    raw = strip_code(code)
    return ind_map.get(raw, '—')


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    # 加载行业映射
    print('加载行业映射...')
    ind_map = load_industry_map()
    print(f'  行业数: {len(ind_map)}')

    # 加载股票名称
    print('加载股票名称...')
    names = pd.read_sql_query("SELECT code, code_name FROM stock_basic", conn)
    name_map = names.set_index('code')['code_name'].to_dict()
    print(f'  股票数: {len(name_map)}')

    # 获取所有可用日期 (≥100条)
    print('获取日期列表...')
    dates_df = pd.read_sql_query("""
        SELECT target_date, COUNT(*) as cnt, ROUND(AVG(total),1) as avg_score,
               ROUND(MAX(total),1) as max_score, SUM(is_limit_up_today) as limit_count
        FROM screening_history
        GROUP BY target_date HAVING cnt >= 100
        ORDER BY target_date DESC
    """, conn)
    dates = dates_df['target_date'].tolist()
    print(f'  可用日期: {len(dates)} ({dates[-1]} ~ {dates[0]})')

    # 写入日期列表
    with open(os.path.join(DATA_DIR, 'dates.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'dates': dates,
            'latest': dates[0],
            'stats': dates_df.set_index('target_date').to_dict(orient='index')
        }, f, ensure_ascii=False)
    print(f'  ✓ dates.json')

    # 逐日期导出
    print('导出各日期数据...')
    all_industries = set()
    for date in dates:
        df = pd.read_sql_query("""
            SELECT code, rank, total, washout_quality, probe_test, ma_convergence,
                   stock_strength, launch_readiness, fund_flow, volume_health,
                   latest_close, latest_pctChg, is_limit_up_today,
                   recent_limit_days, probe_count, days_since_probe
            FROM screening_history
            WHERE target_date = ?
            ORDER BY rank
        """, conn, params=(date,))

        stocks = []
        for _, row in df.iterrows():
            code = row['code']
            industry = get_industry(code, ind_map)
            all_industries.add(industry)
            stocks.append({
                'code': code,
                'name': name_map.get(code, '?')[:8],
                'rank': int(row['rank']),
                'total': round(float(row['total']), 1),
                'washout_quality': round(float(row['washout_quality']), 1),
                'probe_test': round(float(row['probe_test']), 1),
                'ma_convergence': round(float(row['ma_convergence']), 1),
                'stock_strength': round(float(row.get('stock_strength', 0) or 0), 1),
                'launch_readiness': round(float(row['launch_readiness']), 1),
                'fund_flow': round(float(row['fund_flow']), 1),
                'volume_health': round(float(row['volume_health']), 1),
                'latest_close': round(float(row['latest_close']), 2),
                'latest_pctChg': round(float(row['latest_pctChg']), 2),
                'is_limit_up_today': bool(int(row['is_limit_up_today'])),
                'recent_limit_days': int(row['recent_limit_days']),
                'probe_count': int(row['probe_count']),
                'days_since_probe': int(row['days_since_probe']),
                'industry': industry,
            })

        with open(os.path.join(DATA_DIR, f'{date}.json'), 'w', encoding='utf-8') as f:
            json.dump({'date': date, 'total': len(stocks), 'stocks': stocks}, f, ensure_ascii=False)
        print(f'  ✓ {date}: {len(stocks)} stocks')

    # 写入行业列表
    industries = sorted([i for i in all_industries if i != '—'])
    with open(os.path.join(DATA_DIR, 'industries.json'), 'w', encoding='utf-8') as f:
        json.dump({'industries': industries}, f, ensure_ascii=False)
    print(f'  ✓ industries.json ({len(industries)} industries)')

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
                   stock_strength, launch_readiness, fund_flow, volume_health
            FROM screening_history WHERE code = ? ORDER BY target_date
        """, conn, params=(code,))

        history = [
            {
                'date': row['target_date'],
                'rank': int(row['rank']),
                'total': round(float(row['total']), 1),
                'washout_quality': round(float(row['washout_quality']), 1),
                'probe_test': round(float(row['probe_test']), 1),
                'ma_convergence': round(float(row['ma_convergence']), 1),
                'stock_strength': round(float(row.get('stock_strength', 0) or 0), 1),
                'launch_readiness': round(float(row['launch_readiness']), 1),
                'fund_flow': round(float(row['fund_flow']), 1),
                'volume_health': round(float(row['volume_health']), 1),
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
                    'open': round(float(r['open']), 2),
                    'high': round(float(r['high']), 2),
                    'low': round(float(r['low']), 2),
                    'close': round(float(r['close']), 2),
                    'volume': int(r['volume']),
                    'pctChg': round(float(r['pctChg']), 2) if r.get('pctChg') is not None else 0,
                    'turn': round(float(r['turn']), 2) if r.get('turn') is not None else 0,
                }
                for r in kline_raw
            ]

            with open(os.path.join(HISTORY_DIR, f'{fname}.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'code': code,
                    'name': name_map.get(code, '?'),
                    'industry': get_industry(code, ind_map),
                    'history': history,
                    'kline': kline,
                }, f, ensure_ascii=False)

        if (i + 1) % 500 == 0:
            print(f'  ... {i+1}/{len(all_codes)}')

    print(f'  ✓ {len(all_codes)} stock history files (with K-line)')

    conn.close()
    print(f'\n静态站点文件已生成到: {DOCS_DIR}')
    print(f'总大小: {_dir_size(DOCS_DIR):.1f} MB')


def _dir_size(path):
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total += os.path.getsize(fp)
    return total / (1024 * 1024)


if __name__ == '__main__':
    main()
