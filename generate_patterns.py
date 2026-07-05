"""
从 V5 CSV 生成形态数据 JSON (供前端"看形态"模式使用)
=====================================================
读取 v5_scores_{date}.csv → 写入 docs/data/patterns/{date}.json
"""
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sqlite3
import pandas as pd
import numpy as np

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs')
DATA_DIR = os.path.join(DOCS_DIR, 'data')
PATTERNS_DIR = os.path.join(DATA_DIR, 'patterns')
CONCEPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'concept.csv')

LABELS_MAP = {
    'double_bottom': '双底形态',
    'uptrend_channel': '上升通道',
    'box_accumulation': '箱体蓄力',
}


def safe_round(val, ndigits=1, default=0.0):
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(v):
        return default
    return round(v, ndigits)


def strip_code(code):
    for prefix in ['sz.', 'sh.', 'bj.']:
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def load_concept_map():
    if not os.path.exists(CONCEPT_PATH):
        return {}
    df = pd.read_csv(CONCEPT_PATH, encoding='gbk', dtype=str)
    cmp_map = {}
    for _, row in df.iterrows():
        cmp_map[row['permno'].strip()] = row['concept_name'].strip()
    return cmp_map


def get_concept(code, cmp_map):
    raw = strip_code(code)
    return cmp_map.get(raw, '—')


def main():
    os.makedirs(PATTERNS_DIR, exist_ok=True)

    # 加载名称 & 概念
    conn = sqlite3.connect(DB_PATH)
    names = pd.read_sql_query("SELECT code, code_name FROM stock_basic", conn)
    name_map = names.set_index('code')['code_name'].to_dict()
    conn.close()

    cmp_map = load_concept_map()

    # 扫描 CSV 文件
    import glob
    csv_files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              'v5_scores_*.csv')))
    if not csv_files:
        print('未找到 v5_scores_*.csv 文件')
        return

    print(f'找到 {len(csv_files)} 个 CSV 文件')

    pattern_dates = []

    for csv_path in csv_files:
        fname = os.path.basename(csv_path)
        date_str = fname.replace('v5_scores_', '').replace('.csv', '')
        print(f'处理 {date_str} ... ', end='')

        try:
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
        except Exception as e:
            print(f'读取失败: {e}')
            continue

        if df.empty:
            print('空文件, 跳过')
            continue

        stocks = []
        for _, row in df.iterrows():
            code = str(row.get('code', ''))
            if not code:
                continue

            dominant = str(row.get('dominant_pattern', 'box_accumulation'))

            stocks.append({
                'code': code,
                'name': (name_map.get(code, '?') or '?')[:8],
                'concept': get_concept(code, cmp_map),
                'total': safe_round(row.get('total', 0), 1),
                'dominant_pattern': dominant,
                'dominant_label': LABELS_MAP.get(dominant, dominant),
                'structure_conf': safe_round(row.get('structure_conf', 0), 2),
                'double_bottom_match': safe_round(row.get('double_bottom_match', 0), 1),
                'double_bottom_score': safe_round(row.get('double_bottom_score', 0), 1),
                'uptrend_channel_match': safe_round(row.get('uptrend_channel_match', 0), 1),
                'uptrend_channel_score': safe_round(row.get('uptrend_channel_score', 0), 1),
                'box_accumulation_match': safe_round(row.get('box_accumulation_match', 0), 1),
                'box_accumulation_score': safe_round(row.get('box_accumulation_score', 0), 1),
            })

        # 按 total 降序
        stocks.sort(key=lambda x: x['total'], reverse=True)

        out_path = os.path.join(PATTERNS_DIR, f'{date_str}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({
                'date': date_str,
                'total': len(stocks),
                'stocks': stocks,
            }, f, ensure_ascii=False)

        pattern_dates.append(date_str)
        print(f'{len(stocks)} 只')

    # 更新 dates.json — 添加 pattern_dates 字段
    dates_path = os.path.join(DATA_DIR, 'dates.json')
    if os.path.exists(dates_path):
        with open(dates_path, 'r', encoding='utf-8') as f:
            dates_data = json.load(f)
        dates_data['pattern_dates'] = pattern_dates
        with open(dates_path, 'w', encoding='utf-8') as f:
            json.dump(dates_data, f, ensure_ascii=False)
        print(f'\n已更新 dates.json (新增 pattern_dates: {len(pattern_dates)} 个日期)')

    print(f'\n形态 JSON 已生成到: {PATTERNS_DIR}')


if __name__ == '__main__':
    main()
