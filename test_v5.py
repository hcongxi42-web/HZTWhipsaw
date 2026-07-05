"""
V5 形态模式识别评分系统
========================
3形态分类器 + 几何平均匹配 + 模糊融合.
不依赖 Y/L 摆动点检测 — 每个形态直接从60日数据中识别自己的结构.

形态体系:
  双底形态    — 二次探底不破前低 + 缩量止跌
  上升通道    — HH/HL + 通道规整
  箱体蓄力    — 振幅收敛+均线粘合+量能萎缩
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sqlite3
import pandas as pd
import numpy as np
from scipy import stats
from tqdm import tqdm
import warnings
warnings.filterwarnings('ignore')

from batch_screen import (
    DB_PATH, LOOKBACK_DAYS, MIN_TURN, MIN_PRICE,
    get_lookback_dates, get_eligible_stocks, quick_filter, load_stock_data
)
from pattern_utils import _sigmoid, _bell, _geom_match, _annual_slope, _ma_slope
from pattern_box import PatternBoxAccumulation
from pattern_channel import PatternUptrendChannel
from pattern_double_bottom import PatternDoubleBottom


# ================================================================
# Part 1: FeatureEngineer — 统一预计算
# ================================================================
class FeatureEngineer:
    """一次性计算所有形态共用的基础特征"""
    def __init__(self, df):
        self.df = df.reset_index(drop=True)
        self._compute()

    def _compute(self):
        d = self.df
        n = len(d)
        d['ret'] = d['close'].pct_change()
        d['amplitude'] = (d['high'] - d['low']) / d['preclose']
        d['vol_ma5'] = d['volume'].rolling(5).mean()
        d['vol_ma10'] = d['volume'].rolling(10).mean()
        d['vol_ratio'] = d['volume'] / d['vol_ma5']
        d['upper_shadow_pct'] = (d['high'] - d[['open', 'close']].max(axis=1)) / d['open']
        d['lower_shadow_pct'] = (d[['open', 'close']].min(axis=1) - d['low']) / d['preclose']

        for p in [5, 10, 20, 60]:
            d[f'ma{p}'] = d['close'].rolling(p).mean()

        d['amp_20_range'] = (d['high'].rolling(20).max() - d['low'].rolling(20).min()) / d['close'].rolling(20).mean()

        d['bias_ma20'] = (d['close'] - d['ma20']) / d['ma20']
        d['bias_ma60'] = (d['close'] - d['ma60']) / d['ma60']

        d['ret_sign'] = np.sign(d['ret'])
        d['obv'] = (d['ret_sign'] * d['volume']).fillna(0).cumsum()
        d['vwap'] = (d['amount'] / d['volume']).fillna(d['close'])

        code = d['code'].iloc[0] if 'code' in d.columns else ''
        if code.startswith('sh.68') or code.startswith('sz.30'):
            limit_pct = 0.198
        elif code.startswith('bj.'):
            limit_pct = 0.298
        else:
            limit_pct = 0.098
        d['is_limit_up'] = (
            (d['ret'] >= limit_pct) |
            ((d['high'] / d['preclose'] - 1) >= limit_pct)
        ).astype(int)

        # ZigZag 简化版: 局部极值点 (M=5, 边界自适应)
        high_v = d['high'].values; low_v = d['low'].values
        peaks, valleys = [], []
        m = 5
        for i in range(m, n - 1):  # 允许检测末尾 (n-2 到 n-1 之间)
            left = max(0, i - m)
            right = min(n - 1, i + m)
            window_h = high_v[left:right + 1]
            window_l = low_v[left:right + 1]
            # 必须是窗口内唯一最高/最低 (去重)
            if high_v[i] == window_h.max() and list(window_h).count(high_v[i]) == 1:
                peaks.append(i)
            if low_v[i] == window_l.min() and list(window_l).count(low_v[i]) == 1:
                valleys.append(i)
        self.peaks = self._filter_extrema(peaks, high_v, 'max')
        self.valleys = self._filter_extrema(valleys, low_v, 'min')

    @staticmethod
    def _filter_extrema(indices, values, mode):
        if len(indices) < 2: return indices
        filtered = [indices[0]]
        for i in range(1, len(indices)):
            if indices[i] - filtered[-1] < 3:
                prev_val = values[filtered[-1]]; cur_val = values[indices[i]]
                if mode == 'max' and cur_val > prev_val: filtered[-1] = indices[i]
                elif mode == 'min' and cur_val < prev_val: filtered[-1] = indices[i]
            else:
                filtered.append(indices[i])
        return filtered


# ================================================================
# Part 2: 路由器 — 决策树 + 模糊融合
# ================================================================
PATTERN_PRIORITY = ['double_bottom', 'uptrend_channel', 'box_accumulation']

LABELS_MAP = {
    'double_bottom': '双底形态',
    'uptrend_channel': '上升通道',
    'box_accumulation': '箱体蓄力',
}


class PatternRouter:
    def __init__(self):
        self.patterns = {
            'double_bottom': PatternDoubleBottom(),
            'uptrend_channel': PatternUptrendChannel(),
            'box_accumulation': PatternBoxAccumulation(),
        }

    def evaluate(self, fe):
        """纯形态识别，不依赖 Y/L"""
        d = fe.df; last = d.iloc[-1]; closes = d['close'].values; n = len(d)

        # ── Stage 1: 决策树筛选候选 ──
        candidates = set()

        # 条件1: 双底形态 — 两个valley价格接近 + 中间有反弹
        if len(fe.valleys) >= 2:
            rv = fe.valleys[-4:] if len(fe.valleys) >= 4 else fe.valleys[-2:]
            for j in range(len(rv) - 1):
                p1 = d['low'].iloc[rv[j]]
                p2 = d['low'].iloc[rv[j + 1]]
                if p1 > 0 and abs(p2 - p1) / p1 < 0.10:
                    mid_pk = [p for p in fe.peaks if rv[j] < p < rv[j + 1]]
                    if mid_pk:
                        candidates.add('double_bottom')
                        break

        # 条件2: 上升通道 — 站上MA20或MA60 + 有正向趋势
        above_ma20 = pd.notna(last['ma20']) and last['close'] > last['ma20']
        above_ma60 = pd.notna(last['ma60']) and last['close'] > last['ma60']
        if above_ma20 or above_ma60:
            candidates.add('uptrend_channel')

        # 条件3: 箱体蓄力 — 振幅<25% 或 均线粘合 或 历史有箱体
        amp_range = last.get('amp_20_range', 0.3)
        ma_sticky = False
        ma_vals = {}
        for p in [5, 10, 20, 60]:
            v = last.get(f'ma{p}')
            if pd.notna(v) and v > 0:
                ma_vals[p] = v
        if len(ma_vals) >= 3:
            ma_range_v = max(ma_vals.values()) - min(ma_vals.values())
            ma_mean_v = np.mean(list(ma_vals.values()))
            conv = 1.0 - ma_range_v / ma_mean_v
            ma_sticky = conv > 0.82

        had_box = False
        amp20_series = d['amp_20_range'].dropna().tail(40).values
        if len(amp20_series) >= 10:
            in_box = (amp20_series > 0.04) & (amp20_series < 0.30)
            had_box = np.sum(in_box) >= 10

        if amp_range < 0.25 or ma_sticky or had_box:
            candidates.add('box_accumulation')

        # 兜底: 至少评估箱体
        if not candidates:
            candidates.add('box_accumulation')

        # ── Stage 2: 计算匹配度 ──
        results = {}
        for pname in candidates:
            p = self.patterns[pname]
            match = p.match(fe)
            if match > 20:
                quality = p.score(fe)
                results[pname] = {'match': round(match, 1), 'score': round(quality, 1)}

        if not results:
            # 兜底: 强制评估箱体
            p_box = self.patterns['box_accumulation']
            m = p_box.match(fe); s = p_box.score(fe)
            results['box_accumulation'] = {'match': round(m, 1), 'score': round(s, 1)}

        # ── Stage 3: 结构置信度 ──
        matches = [v['match'] for v in results.values()]
        structure_conf = round(1.0 - np.std(matches) / (np.mean(matches) + 1e-8), 2) if len(matches) >= 2 else 0.70

        # ── Stage 4: 融合总分 ──
        active = [(pn, v['match'], v['score']) for pn, v in results.items() if v['match'] > 30]
        if len(active) == 0:
            best = max(results.items(), key=lambda x: x[1]['match'])
            raw_score = best[1]['score']; dominant = best[0]
            # 单形态弱匹配: 融合 match 置信度, 防止虚高
            match_conf = min(1.0, best[1]['match'] / 60.0)
            total = raw_score * (0.4 + 0.6 * match_conf)
        elif len(active) == 1:
            total = active[0][2]; dominant = active[0][0]
        else:
            total = sum(m * s for _, m, s in active) / sum(m for _, m, _ in active)
            dominant = max(results.items(), key=lambda x: x[1]['match'])[0]

        return {
            'patterns': results,
            'dominant': dominant,
            'structure_conf': structure_conf,
            'total': round(total, 1),
        }


# ================================================================
# Part 3: 质量门控 + 单股评分
# ================================================================
FILTER_STATS = {'short_data': 0, 'weak_trend': 0, 'freefall': 0, 'passed': 0}


def quality_gate(fe):
    """前置质量门控: 快速排除垃圾股 (不依赖Y/L)"""
    d = fe.df; n = len(d)
    if n < 20:
        FILTER_STATS['short_data'] += 1
        return False

    last = d.iloc[-1]
    closes = d['close'].values

    # 门1: MA60 斜率 + 价格位置 — 明显的下跌趋势直接淘汰
    if pd.notna(last['ma60']) and n >= 60:
        ma60_slope = _ma_slope(closes, 60)
        if ma60_slope < -8 and last['close'] < last['ma60']:
            FILTER_STATS['weak_trend'] += 1
            return False

    # 门2: 价格低于60日最低点 30%+ (崩盘股)
    if n >= 60:
        low60 = d['low'].tail(60).min()
        if last['close'] < low60 * 0.70:
            FILTER_STATS['freefall'] += 1
            return False

    return True


def score_single_v5(df):
    """纯形态识别评分 — 不依赖 Y/L"""
    fe = FeatureEngineer(df)

    if not quality_gate(fe):
        return None

    router = PatternRouter()
    result = router.evaluate(fe)

    if 'dominant' not in result:
        result['dominant'] = 'unknown'

    FILTER_STATS['passed'] += 1
    return result


# ================================================================
# Part 4: 主流程
# ================================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description='V5 形态识别评分')
    parser.add_argument('--date', type=str, help='目标日期 (YYYY-MM-DD)')
    parser.add_argument('--latest', action='store_true', help='使用 DB 中最新的 stock_daily 日期')
    args = parser.parse_args()

    if args.latest:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT MAX(date) FROM stock_daily")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            target_date = row[0]
            print(f'[latest] DB 最新日期: {target_date}')
        else:
            print('ERROR: 无法获取最新日期')
            return
    elif args.date:
        target_date = args.date
    else:
        target_date = '2026-06-24'
    trading_dates = get_lookback_dates(target_date, LOOKBACK_DAYS)
    start_date = trading_dates[0]
    print(f'V5 形态识别 — {target_date}')
    print(f'回溯区间: {start_date} ~ {target_date} ({len(trading_dates)}天)')

    eligible = get_eligible_stocks(target_date, start_date)
    print(f'符合基本条件: {len(eligible)}只 (换手≥{MIN_TURN}%, 股价≥{MIN_PRICE})')

    eligible_codes = eligible['code'].tolist()
    passed_codes, reasons = quick_filter(eligible_codes, start_date, target_date)
    print(f'预筛选通过: {len(passed_codes)}/{len(eligible_codes)} '
          f'(淘汰: 无回调={reasons["no_retreat"]} 振幅小={reasons["too_stable"]} 出货={reasons["distribution"]})')

    results = []
    pattern_stats = {}
    skip_load = 0; skip_gate = 0; skip_exc = 0
    exc_samples = []

    for code in tqdm(passed_codes, desc='  形态评分', leave=False):
        df = load_stock_data(code, start_date, target_date)
        if df is None or len(df) < 20:
            skip_load += 1; continue
        try:
            r = score_single_v5(df)
            if r is None:
                skip_gate += 1; continue
            results.append({
                'code': code, 'total': r['total'],
                'dominant_pattern': r['dominant'],
                'structure_conf': r['structure_conf'],
                'patterns': r['patterns'],
            })
            dom = r['dominant']
            pattern_stats[dom] = pattern_stats.get(dom, 0) + 1
        except Exception as e:
            skip_exc += 1
            if len(exc_samples) < 5:
                exc_samples.append(f'{code}: {type(e).__name__}: {str(e)[:80]}')
            continue

    results.sort(key=lambda x: x['total'], reverse=True)

    # ── 输出 ──
    print(f'\n{"="*60}')
    print(f'V5 评分完成: {len(results)} 只股票')
    print(f'过滤: 数据不足={skip_load}, 门控淘汰={skip_gate}, 异常={skip_exc}, '
          f'弱趋势={FILTER_STATS["weak_trend"]}, 崩盘={FILTER_STATS["freefall"]}, 通过={FILTER_STATS["passed"]}')
    if exc_samples:
        print(f'异常样本:')
        for s in exc_samples: print(f'  {s}')
    print(f'{"="*60}')

    totals = [r['total'] for r in results]
    if totals:
        print(f'分数分布:')
        print(f'  均值: {np.mean(totals):.1f}  中位数: {np.median(totals):.1f}  最高: {np.max(totals):.1f}  最低: {np.min(totals):.1f}')
        for lo, hi, label in [(80, 100, '80-100'), (65, 80, '65-80'), (50, 65, '50-65'), (0, 50, ' 0-50')]:
            cnt = sum(1 for t in totals if lo <= t < hi)
            print(f'  {label}: {cnt:4d} ({cnt/len(totals)*100:.1f}%)')

    print(f'\n形态分布:')
    for pn in PATTERN_PRIORITY:
        cnt = pattern_stats.get(pn, 0)
        print(f'  {LABELS_MAP.get(pn, pn):8s}: {cnt:4d} ({cnt/max(len(results), 1)*100:.1f}%)')

    print(f'\nTop 20:')
    print(f'{"Rank":<5} {"Code":<12} {"Total":<7} {"主导形态":<10} {"Conf":<6} {"其他匹配形态"}')
    print(f'{"-"*85}')
    for i, r in enumerate(results[:20]):
        pat_str = ', '.join(f'{k}={v["match"]:.0f}' for k, v in
                          sorted(r['patterns'].items(), key=lambda x: x[1]['match'], reverse=True)[:3])
        print(f'{i+1:<5} {r["code"]:<12} {r["total"]:<7.1f} {LABELS_MAP.get(r["dominant_pattern"], r["dominant_pattern"]):<10} {r["structure_conf"]:<6.2f} {pat_str}')

    # 保存 CSV
    csv_rows = []
    for r in results:
        row = {'code': r['code'], 'total': r['total'],
               'dominant_pattern': r['dominant_pattern'], 'structure_conf': r['structure_conf']}
        for pn in PATTERN_PRIORITY:
            if pn in r['patterns']:
                row[f'{pn}_match'] = r['patterns'][pn]['match']
                row[f'{pn}_score'] = r['patterns'][pn]['score']
            else:
                row[f'{pn}_match'] = 0; row[f'{pn}_score'] = 0
        csv_rows.append(row)
    csv_path = f'v5_scores_{target_date}.csv'
    pd.DataFrame(csv_rows).to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'\nCSV: {csv_path}')


if __name__ == '__main__':
    main()
