"""
生成 concept.csv（白名单过滤）
数据源：A股全市场股票概念数据.xlsx（本地 Excel，无需网络）
策略：
  1. 读取 Excel → 解析每只股票的概念列表
  2. 过滤出白名单概念
  3. 每个股票选「最聚焦」的白名单概念（全市场出现次数最少）
  4. 无白名单概念的股票 → 不写入（generate_static 会给 '—'）

用法：
  python build_concept_akshare.py          # 默认：从 Excel 读取
  python build_concept_akshare.py --akshare # 强制从 AKShare 爬取（需网络）
"""
import os, sys, json, time
from collections import Counter

DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(DIR, 'A股全市场股票概念数据.xlsx')
WHITELIST_PATH = os.path.join(DIR, 'whitelist_mapping.json')
MEMBERSHIP_PATH = os.path.join(DIR, 'concept_membership.json')
OUT_PATH = os.path.join(DIR, 'concept.csv')
DB_PATH = os.path.join(DIR, 'stock_data.db')

USE_AKSHARE = '--akshare' in sys.argv

# ── 加载白名单 ──
with open(WHITELIST_PATH, 'r', encoding='utf-8') as f:
    wl_data = json.load(f)
WHITELIST = set(wl_data['whitelist'])
print(f"Whitelist: {len(WHITELIST)} concepts")


def parse_excel():
    """从 Excel 解析股票→概念映射"""
    import pandas as pd

    df = pd.read_excel(EXCEL_PATH, header=None)

    stock_concepts = {}  # code -> list of concepts
    concept_freq = Counter()  # how many stocks each concept covers

    for i in range(2, len(df)):
        code_raw = str(df.iloc[i, 1])
        if code_raw == 'nan':
            continue
        code = code_raw.strip().zfill(6)  # "1" -> "000001"

        concepts_str = df.iloc[i, 3]
        if pd.isna(concepts_str):
            stock_concepts[code] = []
            continue

        concepts = [c.strip() for c in str(concepts_str).split('、') if c.strip()]
        stock_concepts[code] = concepts
        for c in set(concepts):
            concept_freq[c] += 1

    print(f"  Excel: {len(stock_concepts)} stocks, {len(concept_freq)} unique concepts")
    return stock_concepts, concept_freq


def filter_and_pick(stock_concepts, concept_freq):
    """为每只股票选最聚焦的白名单概念"""
    lines = []
    whitelisted_stocks = 0
    no_wl_stocks = 0
    empty_stocks = 0

    for code, concepts in stock_concepts.items():
        if not concepts:
            empty_stocks += 1
            continue

        # 过滤白名单 + 按出现次数排序（取最小=最聚焦）
        wl_candidates = [(c, concept_freq.get(c, 0)) for c in concepts if c in WHITELIST]
        if wl_candidates:
            wl_candidates.sort(key=lambda x: x[1])
            lines.append((code, wl_candidates[0][0]))
            whitelisted_stocks += 1
        else:
            no_wl_stocks += 1

    lines.sort(key=lambda x: x[0])
    print(f"  Whitelisted: {whitelisted_stocks}, No whitelist concept: {no_wl_stocks}, "
          f"Empty concept: {empty_stocks}")
    return lines


def fetch_from_akshare():
    """从 AKShare 爬取全量概念成员数据（备选方案）"""
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

            if cnt > 300:
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

    with open(MEMBERSHIP_PATH, 'w', encoding='utf-8') as f:
        json.dump({'stock_map': stock_map, 'concept_count': len(concept_names)},
                  f, ensure_ascii=False)
    print(f"  Saved concept_membership.json ({len(stock_map)} stocks)")

    return stock_map


def filter_from_akshare_cache(stock_map):
    """从 AKShare 全量成员数据中为每只股票选最聚焦的白名单概念"""
    lines = []
    for code, concepts in stock_map.items():
        whitelist_concepts = [(name, cnt) for name, cnt in concepts.items() if name in WHITELIST]
        if whitelist_concepts:
            whitelist_concepts.sort(key=lambda x: x[1])
            lines.append((code, whitelist_concepts[0][0]))

    lines.sort(key=lambda x: x[0])
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
            if c.startswith(p):
                c = c[3:]
                break
        sh.add(c)
    conn.close()

    concept_codes = set(c for c, _ in lines)
    covered = sh & concept_codes
    pct = len(covered) / len(sh) * 100 if sh else 0
    print(f"  Coverage: {len(covered)}/{len(sh)} ({pct:.1f}%)")


# ── 主逻辑 ──
t0 = time.time()
lines = None

if USE_AKSHARE:
    # AKShare 模式（备选）
    print("Mode: AKShare (network required)")
    try:
        stock_map = fetch_from_akshare()
        lines = filter_from_akshare_cache(stock_map)
    except Exception as e:
        print(f"  AKShare failed: {e}")
        sys.exit(1)
elif os.path.exists(EXCEL_PATH):
    # Excel 模式（默认）
    print("Mode: Excel (local file)")
    stock_concepts, concept_freq = parse_excel()
    lines = filter_and_pick(stock_concepts, concept_freq)
else:
    # Excel 不在 → 尝试 concept_membership.json 缓存
    print("Mode: concept_membership.json cache")
    if os.path.exists(MEMBERSHIP_PATH):
        with open(MEMBERSHIP_PATH, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        print(f"  Loaded cache ({len(cache['stock_map'])} stocks)")
        lines = filter_from_akshare_cache(cache['stock_map'])
    else:
        print("ERROR: No data source available!")
        print("  Place A股全市场股票概念数据.xlsx in the project root, or use --akshare")
        sys.exit(1)

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

unique_concepts = set(n for _, n in lines)
print(f"  Wrote concept.csv: {len(lines)} stocks, {len(unique_concepts)} concepts")

# ── 白名单验证 ──
violations = unique_concepts - WHITELIST
if violations:
    print(f"  !! WHITELIST VIOLATIONS: {violations}")
else:
    print(f"  [OK] All {len(unique_concepts)} concepts in whitelist")

# ── Top 概念 ──
top = Counter(n for _, n in lines).most_common(20)
print(f"\n  Top 20 whitelisted concepts:")
for name, cnt in top:
    print(f"    {name}: {cnt}")

elapsed = time.time() - t0
print(f"\nDone in {elapsed:.0f}s")
