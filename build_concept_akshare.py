"""
生成 concept.csv（白名单过滤）
策略：
  1. 如果有 concept_membership.json 缓存 → 从缓存过滤（无需 API）
  2. 如果缓存不存在且 AKShare 可用 → 爬取全量数据，保存缓存，再过滤
  3. 都不行 → 从现有 concept.csv 硬过滤（仅保留白名单概念，其余→'—'）

用法：
  python build_concept_akshare.py          # 默认：优先缓存
  python build_concept_akshare.py --refresh # 强制重新爬取
"""
import os, sys, json, time
from collections import Counter

DIR = os.path.dirname(os.path.abspath(__file__))
MEMBERSHIP_PATH = os.path.join(DIR, 'concept_membership.json')
WHITELIST_PATH = os.path.join(DIR, 'whitelist_mapping.json')
OUT_PATH = os.path.join(DIR, 'concept.csv')
DB_PATH = os.path.join(DIR, 'stock_data.db')

FORCE_REFRESH = '--refresh' in sys.argv

# ── 加载白名单 ──
with open(WHITELIST_PATH, 'r', encoding='utf-8') as f:
    wl_data = json.load(f)
WHITELIST = set(wl_data['whitelist'])
print(f"Whitelist: {len(WHITELIST)} concepts")


def fetch_from_akshare():
    """从 AKShare 爬取全量概念成员数据，保存到 JSON 缓存"""
    import akshare as ak

    print("  Fetching concept names...")
    concept_df = ak.stock_board_concept_name_em()
    concept_names = concept_df['板块名称'].tolist()
    print(f"  {len(concept_names)} total concepts")

    stock_map = {}
    total = len(concept_names)
    failed = 0

    for i, name in enumerate(concept_names):
        try:
            df = ak.stock_board_concept_cons_em(symbol=name)
            codes = [str(c).strip() for c in df['代码'].tolist() if len(str(c).strip()) == 6]
            cnt = len(codes)

            if cnt > 300:  # 太泛的概念跳过
                if (i + 1) % 100 == 0:
                    print(f"    [{i+1}/{total}] skip... ok:{len(stock_map)} fail:{failed}")
                time.sleep(0.15)
                continue

            for code in codes:
                if code not in stock_map:
                    stock_map[code] = {}
                stock_map[code][name] = cnt

            if (i + 1) % 60 == 0:
                print(f"    [{i+1}/{total}] {name}({cnt}) stocks:{len(stock_map)} fail:{failed}")

        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"    [{i+1}/{total}] {name} FAIL: {str(e)[:60]}")

        time.sleep(0.25)

    print(f"  Done. stocks:{len(stock_map)} failed:{failed}")

    # 保存缓存
    with open(MEMBERSHIP_PATH, 'w', encoding='utf-8') as f:
        json.dump({'stock_map': stock_map, 'concept_count': len(concept_names)},
                  f, ensure_ascii=False)
    print(f"  Saved concept_membership.json ({len(stock_map)} stocks)")

    return stock_map


def filter_from_cache(stock_map):
    """从全量成员数据中为每只股票选最聚焦的白名单概念"""
    lines = []
    for code, concepts in stock_map.items():
        # 只保留白名单概念
        whitelist_concepts = [(name, cnt) for name, cnt in concepts.items() if name in WHITELIST]
        if whitelist_concepts:
            # 选成分股数最小的（最聚焦）
            whitelist_concepts.sort(key=lambda x: x[1])
            lines.append((code, whitelist_concepts[0][0]))
        # 否则丢弃（不写入）

    lines.sort(key=lambda x: x[0])
    return lines


def hard_filter_existing():
    """从现有 concept.csv 硬过滤：只保留白名单概念，其余丢弃"""
    import pandas as pd

    try:
        df = pd.read_csv(OUT_PATH, encoding='gbk', dtype=str)
    except FileNotFoundError:
        print("  ERROR: concept.csv not found, cannot hard-filter")
        return []

    lines = []
    removed = 0
    for _, row in df.iterrows():
        code = row['permno'].strip()
        concept = row['concept_name'].strip()
        if concept in WHITELIST:
            lines.append((code, concept))
        else:
            removed += 1

    print(f"  Hard-filter: kept {len(lines)}, removed {removed} (non-whitelist)")
    return lines


def coverage_report(lines):
    """计算 screening_history 覆盖率"""
    import sqlite3
    if not os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    sh = set()
    for r in conn.execute('SELECT DISTINCT code FROM screening_history'):
        c = r[0]
        for p in ['sz.', 'sh.', 'bj.']:
            if c.startswith(p): c = c[3:]; break
        sh.add(c)
    conn.close()

    concept_codes = set(c for c, _ in lines)
    covered = sh & concept_codes
    pct = len(covered) / len(sh) * 100 if sh else 0
    print(f"  Coverage: {len(covered)}/{len(sh)} ({pct:.1f}%)")


# ── 主逻辑 ──
t0 = time.time()
lines = None

if FORCE_REFRESH or not os.path.exists(MEMBERSHIP_PATH):
    # 尝试 AKShare
    try:
        stock_map = fetch_from_akshare()
    except Exception as e:
        print(f"  AKShare failed: {e}")
        stock_map = None

    if stock_map:
        lines = filter_from_cache(stock_map)
    else:
        print("  Fallback: hard-filter existing concept.csv")
        lines = hard_filter_existing()
else:
    # 从缓存读取
    with open(MEMBERSHIP_PATH, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    print(f"  Loaded concept_membership.json ({len(cache['stock_map'])} stocks)")
    lines = filter_from_cache(cache['stock_map'])

if not lines:
    print("ERROR: No data generated!")
    sys.exit(1)

# ── 覆盖率 ──
coverage_report(lines)

# ── 写入 concept.csv ──
with open(OUT_PATH, 'w', encoding='gbk', newline='') as f:
    f.write('permno,concept_name\n')
    for code, name in lines:
        f.write(f'{code},{name}\n')
print(f"  Wrote concept.csv: {len(lines)} stocks, {len(set(n for _, n in lines))} concepts")

# ── Top 概念 ──
top = Counter(n for _, n in lines).most_common(15)
print(f"\n  Top 15 whitelisted concepts:")
for name, cnt in top:
    print(f"    {name}: {cnt}")

elapsed = time.time() - t0
print(f"\nDone in {elapsed:.0f}s")
