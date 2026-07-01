"""
从 stockapi.com.cn 拉取概念数据，生成 concept.csv
策略：先跑全部概念，记录失败列表，最后单独重试失败项
"""
import urllib.request, json, time, sys, os, pickle

CACHE_FILE = 'concept_cache.pkl'
FAILED_FILE = 'concept_failed.txt'

def fetch_json(url, max_retries=5):
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < max_retries - 1:
                wait = min(10, (attempt + 1) * 2)
                time.sleep(wait)
    return None

# --- 1. 获取概念列表 ---
print("1. Fetching concept list...")
data = fetch_json("https://www.stockapi.com.cn/v1/base/gn")
concepts = {}
for item in data.get('data', []):
    concepts[item['plateCode']] = item['name']
print(f"   Total concepts: {len(concepts)}")

# --- 2. 加载缓存（支持断点续传）---
stock_map = {}  # stock_code -> [(concept_name, stock_count), ...]
done_concepts = set()
failed_concepts = set()

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'rb') as f:
        stock_map, done_concepts = pickle.load(f)
    print(f"   Loaded cache: {len(stock_map)} stocks, {len(done_concepts)} concepts done")

todo = [(c, n) for c, n in concepts.items() if c not in done_concepts]
print(f"   Remaining: {len(todo)}")

# --- 3. 逐个拉取 ---
total = len(todo)
for i, (bk_code, bk_name) in enumerate(todo):
    all_diff = []
    # 取多页（每页200只）
    for page in range(1, 6):
        url = f"https://www.stockapi.com.cn/v1/base/bkList?bkCode={bk_code}&pageNo={page}&pageSize=200"
        data = fetch_json(url, max_retries=3)
        if data is None:
            all_diff = []  # 标记失败
            break
        diff = data.get('data', {}).get('diff', [])
        if not diff:
            all_diff = []
            break
        all_diff.extend(diff)
        if len(diff) < 200:
            break
        time.sleep(0.3)

    if not all_diff:
        failed_concepts.add(bk_code)
        done_concepts.add(bk_code)
        if (i + 1) % 50 == 0:
            print(f"   [{i+1}/{total}] fail:{len(failed_concepts)} ...")
        continue

    stock_count = len(all_diff)
    done_concepts.add(bk_code)

    # 只保留 ≤200 只成分股的中小概念（太大=太泛）
    if stock_count <= 200:
        for stock in all_diff:
            code = stock.get('f12', '')
            if code:
                if code not in stock_map:
                    stock_map[code] = []
                stock_map[code].append((bk_name, stock_count))

    if (i + 1) % 30 == 0:
        # 定期保存缓存
        with open(CACHE_FILE, 'wb') as f:
            pickle.dump((stock_map, done_concepts), f)
        print(f"   [{i+1}/{total}] {bk_name}:{stock_count}  fail:{len(failed_concepts)}  stocks:{len(stock_map)}  [saved]")

    time.sleep(0.5)  # 温和限速

# --- 4. 重试失败概念 ---
if failed_concepts:
    print(f"\n4. Retrying {len(failed_concepts)} failed concepts...")
    retry_ok = 0
    for bk_code in list(failed_concepts):
        bk_name = concepts.get(bk_code, bk_code)
        time.sleep(2)  # 重试间隔更长
        all_diff = []
        for page in range(1, 6):
            url = f"https://www.stockapi.com.cn/v1/base/bkList?bkCode={bk_code}&pageNo={page}&pageSize=200"
            data = fetch_json(url, max_retries=2)
            if data is None:
                all_diff = []
                break
            diff = data.get('data', {}).get('diff', [])
            if not diff:
                break
            all_diff.extend(diff)
            if len(diff) < 200:
                break
            time.sleep(1)

        if all_diff:
            stock_count = len(all_diff)
            if stock_count <= 200:
                for stock in all_diff:
                    code = stock.get('f12', '')
                    if code:
                        if code not in stock_map:
                            stock_map[code] = []
                        stock_map[code].append((bk_name, stock_count))
            failed_concepts.discard(bk_code)
            retry_ok += 1
            if retry_ok % 20 == 0:
                print(f"   retry OK: {retry_ok}, still failed: {len(failed_concepts)}")

    print(f"   Retry recovered: {retry_ok}, still failed: {len(failed_concepts)}")

# --- 5. 最终保存 ---
with open(CACHE_FILE, 'wb') as f:
    pickle.dump((stock_map, done_concepts), f)

print(f"\n5. Generating concept.csv...")
print(f"   Stocks with concepts: {len(stock_map)}")

# 选最聚焦的概念（成分股数最小）
lines = []
for code, clist in stock_map.items():
    clist.sort(key=lambda x: x[1])
    best_name = clist[0][0]
    lines.append((code, best_name))

# 排序
lines.sort(key=lambda x: x[0])

output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'concept.csv')
with open(output_path, 'w', encoding='gbk', newline='') as f:
    f.write('permno,concept_name\n')
    for code, name in lines:
        f.write(f'{code},{name}\n')

print(f"   Written: {len(lines)} stocks to concept.csv")

# 统计
from collections import Counter
top = Counter(n for _, n in lines).most_common(15)
print(f"\n   Top concepts:")
for name, cnt in top:
    print(f"     {name}: {cnt}")
