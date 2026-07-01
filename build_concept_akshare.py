"""
使用 AKShare 东方财富概念数据生成 concept.csv（仅白名单概念）
"""
import akshare as ak
import time, os, sqlite3, json
from collections import Counter

DIR = os.path.dirname(os.path.abspath(__file__))

# ── 加载白名单 ──
whitelist_path = os.path.join(DIR, 'whitelist_mapping.json')
with open(whitelist_path, 'r', encoding='utf-8') as f:
    wl_data = json.load(f)
WHITELIST = set(wl_data['whitelist'])
print(f"Whitelist: {len(WHITELIST)} concepts")

print("1. Getting concept list...")
t0 = time.time()
concept_df = ak.stock_board_concept_name_em()
concept_names = concept_df['板块名称'].tolist()
print(f"   {len(concept_names)} total concepts ({time.time()-t0:.1f}s)")

# ── 过滤：只保留白名单中的概念 ──
whitelist_names = [n for n in concept_names if n in WHITELIST]
print(f"   {len(whitelist_names)} in whitelist")

# ── 逐个概念拉成分股 ──
stock_map = {}
total = len(whitelist_names)
failed = 0

for i, name in enumerate(whitelist_names):
    try:
        df = ak.stock_board_concept_cons_em(symbol=name)
        codes = [str(c).strip() for c in df['代码'].tolist() if len(str(c).strip()) == 6]
        cnt = len(codes)

        # 跳过大概念（>300只=太泛）
        if cnt > 300:
            print(f"   [{i+1}/{total}] {name}({cnt}) SKIP (too broad)")
            time.sleep(0.15)
            continue

        for code in codes:
            if code not in stock_map:
                stock_map[code] = []
            stock_map[code].append((name, cnt))

        if (i + 1) % 30 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (total - i - 1)
            print(f"   [{i+1}/{total}] {name}({cnt}) stocks:{len(stock_map)} fail:{failed} eta:{eta:.0f}s")

    except Exception as e:
        failed += 1
        if failed <= 5:
            print(f"   [{i+1}/{total}] {name} FAIL: {str(e)[:60]}")

    time.sleep(0.25)

elapsed = time.time() - t0
print(f"\n2. Done! {elapsed:.0f}s  stocks:{len(stock_map)} failed:{failed}")

# ── 选最聚焦的概念（成分股数最小）──
lines = []
for code, clist in stock_map.items():
    clist.sort(key=lambda x: x[1])
    lines.append((code, clist[0][0]))

lines.sort(key=lambda x: x[0])

# ── 覆盖率 ──
db = os.path.join(DIR, 'stock_data.db')
conn = sqlite3.connect(db)
sh = set()
for r in conn.execute('SELECT DISTINCT code FROM screening_history'):
    c = r[0]
    for p in ['sz.', 'sh.', 'bj.']:
        if c.startswith(p): c = c[3:]; break
    sh.add(c)
conn.close()

concept_codes = set(c for c, _ in lines)
covered = sh & concept_codes
print(f"   Coverage: {len(covered)}/{len(sh)} ({len(covered)/len(sh)*100:.1f}%)")

# ── 写入 ──
out = os.path.join(DIR, 'concept.csv')
with open(out, 'w', encoding='gbk', newline='') as f:
    f.write('permno,concept_name\n')
    for code, name in lines:
        f.write(f'{code},{name}\n')
print(f"   concept.csv: {len(lines)} stocks")

top = Counter(n for _, n in lines).most_common(20)
print(f"\n   Top 20 concepts:")
for name, cnt in top:
    print(f"     {name}: {cnt}")

# ── 验证关键股票 ──
key_stocks = {'002326': '永太科技', '300502': '新易盛', '301128': '强瑞技术',
              '002497': '雅化集团', '600522': '中天科技'}
print(f"\n   Key stocks:")
for code, name in key_stocks.items():
    concept = '—'
    for c, cn in lines:
        if c == code:
            concept = cn
            break
    print(f"     {name} ({code}): {concept}")
