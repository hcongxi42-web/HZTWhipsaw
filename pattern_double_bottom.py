"""
双底形态 (Double Bottom)
========================
从 ZigZag valleys 找两个相近低点 (二次探底不破前低),
中间必须有反弹, 第二底缩量止跌.
"""
import numpy as np
import pandas as pd
from scipy import stats
from pattern_utils import _sigmoid, _bell, _geom_match, _annual_slope


class PatternDoubleBottom:
    """P4: 双底形态 — 二次探底不破前低 + 缩量止跌"""
    name = 'double_bottom'
    label = '双底形态'

    def __init__(self):
        self._db_info = None  # match() 存入, score() 复用

    # ── 双底发现 ─────────────────────────────────────
    def _find_double_bottom(self, fe):
        """从ZigZag valleys找双底: 两个相近的低点, 中间有反弹, 第二底缩量."""
        d = fe.df; n = len(d)
        valleys = fe.valleys
        peaks = fe.peaks

        if len(valleys) < 2:
            return None

        best = None
        best_quality = 0

        # 只看近50天内的valleys
        recent_valleys = [v for v in valleys if v >= n - 50]
        if len(recent_valleys) < 2:
            return None

        for i in range(len(recent_valleys) - 1):
            v1 = recent_valleys[i]      # 第一个底 (较早)
            v2 = recent_valleys[i + 1]  # 第二个底 (较近, 可能是最近一个)

            span = v2 - v1
            if span < 6 or span > 40:   # 间隔合理
                continue

            price1 = d['low'].iloc[v1]
            price2 = d['low'].iloc[v2]
            if price1 <= 0:
                continue

            # 两个底价格接近 (±8%内)
            price_diff = abs(price2 - price1) / price1
            if price_diff > 0.08:
                continue

            # 中间必须有反弹 (v1和v2之间有peak)
            mid_peaks = [p for p in peaks if v1 < p < v2]
            if not mid_peaks:
                continue
            mid_high = max(d['high'].iloc[p] for p in mid_peaks)
            mid_low = min(price1, price2)
            rebound = (mid_high - mid_low) / mid_low
            if rebound < 0.08:          # 反弹幅度至少8%
                continue

            # 第二底缩量: v2附近量 < v1附近量
            vol1 = d['volume'].iloc[max(0, v1 - 1):v1 + 3].mean()
            vol2 = d['volume'].iloc[max(0, v2 - 1):min(n, v2 + 3)].mean()
            vol_shrink = 1.0 - vol2 / max(vol1, 0.01)

            # 第二底之后: 止跌确认
            recovery = 0.0
            no_new_low = 0.5
            if v2 < n - 1:
                post = d.iloc[v2:]
                recovery = (post['close'].iloc[-1] / price2 - 1) if price2 > 0 else 0
                post_low = post['low'].min()
                no_new_low = 1.0 if post_low >= price2 * 0.97 else 0.5

            # 质量评分
            prox_q = _bell(price_diff * 100, 1.5, 3.0)
            vol_q = _sigmoid(vol_shrink, 0.10, 15.0)
            rebound_q = _sigmoid(rebound * 100, 15, 0.15)
            recovery_q = _sigmoid(recovery * 100, 3, 0.5)

            quality = prox_q * 0.30 + vol_q * 0.25 + rebound_q * 0.25 + recovery_q * 0.20

            if quality > best_quality:
                best_quality = quality
                best = {
                    'v1_idx': v1, 'v2_idx': v2,
                    'price1': price1, 'price2': price2,
                    'price_diff': price_diff,
                    'mid_high': mid_high, 'rebound': rebound,
                    'vol_shrink': vol_shrink,
                    'recovery': recovery, 'no_new_low': no_new_low,
                    'quality': quality,
                }

        return best

    # ── 形态匹配度 ───────────────────────────────────
    def match(self, fe):
        db = self._find_double_bottom(fe)
        self._db_info = db

        if db is None:
            return 0

        d = fe.df; last = d.iloc[-1]; n = len(d)

        # f1: 双底价格接近度 — 越接近越像双底
        f1 = _bell(db['price_diff'] * 100, 1.5, 3.5)

        # f2: 缩量程度 — 第二底量越小越好
        f2 = _sigmoid(db['vol_shrink'], 0.08, 15.0)

        # f3: 止跌确认 — 不创新低 + 已开始回升
        f3 = db['no_new_low'] * 50 + _sigmoid(db['recovery'] * 100, 3, 0.5) * 0.5

        # f4: 双底间隔 — 不要太短也别太长
        span = db['v2_idx'] - db['v1_idx']
        f4 = _bell(span, 18, 10)

        # f5: 中间反弹幅度 — 适中最优
        f5 = _bell(db['rebound'] * 100, 18, 12)

        # f6: 时效性 — 刚从第二底起来最佳
        days_since_v2 = n - 1 - db['v2_idx']
        f6 = _bell(days_since_v2, 3, 5)

        return _geom_match(
            {'f1': f1, 'f2': f2, 'f3': f3, 'f4': f4, 'f5': f5, 'f6': f6},
            {'f1': 0.25, 'f2': 0.20, 'f3': 0.20, 'f4': 0.15, 'f5': 0.10, 'f6': 0.10})

    # ── 双底内评分 ───────────────────────────────────
    def score(self, fe):
        """双底内评分: 结构质量 + 止跌确认 + 量能 + 反弹空间"""
        db = self._db_info
        d = fe.df; n = len(d)

        if db is None:
            return 20

        v2 = db['v2_idx']

        # 1. 双底结构质量 (35%)
        struct_q = db['quality']

        # 2. 止跌确认 (25%): 第二底附近的K线特征
        seg = d.iloc[max(0, v2 - 1):min(n, v2 + 4)]
        hammer = seg['lower_shadow_pct'].max()
        red_days = (seg['ret'] > 0).sum()
        stop_q = _sigmoid(hammer * 100, 3, 1.0) * 0.5 + min(100, red_days * 25 + 25) * 0.5

        # 3. 量能确认 (20%)
        vol_q = _sigmoid(db['vol_shrink'], 0.08, 15.0)

        # 4. 反弹空间 (20%): 距颈线(中间高点)的空间
        neckline = db['mid_high']
        current = d['close'].iloc[-1]
        space_to_neck = (neckline / current - 1) if current > 0 else 0
        space_q = _sigmoid(space_to_neck * 100, 8, 0.3)

        raw = struct_q * 0.35 + stop_q * 0.25 + vol_q * 0.20 + space_q * 0.20
        return raw
