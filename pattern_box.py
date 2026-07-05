"""
箱体蓄力形态 (Box Accumulation)
================================
从 ZigZag 极值点定箱体边界 (一个peak + 一个valley),
验证价格在箱体内运行, 评分侧重位置+量能+蓄力信号.
"""
import numpy as np
import pandas as pd
from scipy import stats
from pattern_utils import _sigmoid, _bell, _geom_match, _annual_slope


class PatternBoxAccumulation:
    """P3: 箱体蓄力 — ZigZag峰谷定边界 + 箱体内评分"""
    name = 'box_accumulation'
    label = '箱体蓄力'

    def __init__(self):
        self._box_info = None  # match() 存入, score() 复用

    # ── 箱体发现 ─────────────────────────────────────
    def _find_box(self, fe):
        """用 ZigZag 极值点确定箱体: 一个高点(peak) + 一个低点(valley).

        箱体确认期 = 从较晚的极值点之后, 价格必须在箱体内运行.
        """
        d = fe.df; n = len(d)
        peaks = fe.peaks
        valleys = fe.valleys

        if len(peaks) < 1 or len(valleys) < 1:
            return None

        # 只看近50天内的极值点
        recent_peaks = [p for p in peaks if p >= n - 50]
        recent_valleys = [v for v in valleys if v >= n - 50]
        if not recent_peaks or not recent_valleys:
            return None

        best = None
        best_quality = 0

        for pk in recent_peaks:
            peak_price = d['high'].iloc[pk]
            for vl in recent_valleys:
                valley_price = d['low'].iloc[vl]

                # 高 > 低, 至少间隔4天
                if peak_price <= valley_price or abs(pk - vl) < 4:
                    continue

                box_high = peak_price
                box_low = valley_price
                box_mid = (box_high + box_low) / 2
                box_width = (box_high - box_low) / box_mid

                # 宽度合理 (5%~30%)
                if box_width < 0.05 or box_width > 0.30:
                    continue

                # 确认期: 从较晚的极值点开始 (此时两个边界都已知)
                confirm_start = max(pk, vl)
                confirm_end = n - 1
                if confirm_end - confirm_start < 8:  # 至少8天确认
                    continue

                seg = d.iloc[confirm_start:confirm_end + 1]

                # 验证: 确认期内价格是否主要在箱体内
                in_box = ((seg['high'] <= box_high * 1.03) &
                          (seg['low'] >= box_low * 0.97))
                in_box_pct = in_box.sum() / len(seg)
                if in_box_pct < 0.70:
                    continue

                # 触及边界次数
                high_touch = (seg['high'] >= box_high * 0.97).sum()
                low_touch = (seg['low'] <= box_low * 1.03).sum()
                touches = high_touch + low_touch
                if touches < 2:
                    continue

                # 箱体确认期内缩量程度
                half = len(seg) // 2
                first_vol = seg['vol_ratio'].iloc[:half].mean()
                second_vol = seg['vol_ratio'].iloc[half:].mean()
                vol_decline = 1.0 - second_vol / max(first_vol, 0.01)

                # 箱体质量分
                width_q = _bell(box_width * 100, 18, 10)
                in_box_q = in_box_pct * 100
                touch_q = min(100, touches * 15 + 30)
                dur_q = min(100, len(seg) * 4)

                quality = width_q * 0.35 + in_box_q * 0.30 + touch_q * 0.20 + dur_q * 0.15

                if quality > best_quality:
                    best_quality = quality
                    best = {
                        'box_high': box_high,
                        'box_low': box_low,
                        'box_mid': box_mid,
                        'box_width': box_width,
                        'confirm_start': confirm_start,
                        'confirm_end': confirm_end,
                        'high_touches': high_touch,
                        'low_touches': low_touch,
                        'in_box_pct': in_box_pct,
                        'vol_decline': vol_decline,
                        'quality': quality,
                    }

        return best

    # ── 形态匹配度 ───────────────────────────────────
    def match(self, fe):
        box = self._find_box(fe)
        self._box_info = box

        if box is None:
            return 0

        d = fe.df; last = d.iloc[-1]; n = len(d)

        # f1: 箱体宽度适中度 (bell中心18%, sigma=10)
        f1 = _bell(box['box_width'] * 100, 18, 10)

        # f2: 价格在箱体内占比 — 越高越好
        f2 = box['in_box_pct'] * 100

        # f3: 缩量程度
        f3 = _sigmoid(box['vol_decline'], 0.03, 15.0)

        # f4: 边界有效性 (触及次数)
        touches = box['high_touches'] + box['low_touches']
        f4 = min(100, touches * 15 + 30)

        # f5: 时效性 — 确认期结束越近越好
        days_since = n - 1 - box['confirm_end']
        f5 = _bell(days_since, 3, 8)

        # f6: 当前位置 vs 箱体 — 偏下部更安全
        if box['box_high'] > box['box_low']:
            pos = (last['close'] - box['box_low']) / (box['box_high'] - box['box_low'])
        else:
            pos = 0.5
        f6 = _bell(pos, 0.35, 0.40)

        return _geom_match(
            {'f1': f1, 'f2': f2, 'f3': f3, 'f4': f4, 'f5': f5, 'f6': f6},
            {'f1': 0.25, 'f2': 0.25, 'f3': 0.15, 'f4': 0.15, 'f5': 0.10, 'f6': 0.10})

    # ── 箱体内评分 ───────────────────────────────────
    def score(self, fe):
        """箱体内评分: 侧重位置+量能+蓄力信号"""
        box = self._box_info
        d = fe.df; last = d.iloc[-1]; n = len(d)

        if box is None:
            return 20

        seg = d.iloc[box['confirm_start']:box['confirm_end'] + 1]

        # 1. 箱体稳固度 (30%): 价格在箱体内的稳定性
        box_q = box['quality']

        # 2. 箱体内位置 (30%): 偏下沿 = 安全买点
        if box['box_high'] > box['box_low']:
            pos = (last['close'] - box['box_low']) / (box['box_high'] - box['box_low'])
        else:
            pos = 0.5
        # 最佳位置: 箱体下1/3 (pos=0~0.33) → 高分; 上沿附近 → 低分
        position_score = 100 - abs(pos - 0.20) * 120
        position_score = max(10, min(100, position_score))

        # 3. 缩量蓄力 (20%): 箱体内量能萎缩程度
        vol_q = _sigmoid(box['vol_decline'], 0.03, 15.0)

        # 4. 资金方向 (10%): 阳线放量 > 阴线放量 = 吸筹
        up_amt = seg[seg['ret'] > 0]['amount'].sum() if len(seg[seg['ret'] > 0]) > 0 else 0
        down_amt = seg[seg['ret'] < 0]['amount'].sum() if len(seg[seg['ret'] < 0]) > 0 else 0
        bias = _sigmoid(up_amt / max(down_amt, 0.01), 1.15, 5.0) if down_amt > 0 else 75

        # 5. 试盘/突破信号 (10%): 上影线测试箱体上沿
        probe = ((seg['vol_ratio'] > 1.2) & (seg['upper_shadow_pct'] > 0.03) &
                 (seg['is_limit_up'] == 0) & (seg['ret'] < 0.08))
        probe_score = min(100, probe.sum() * 25 + 20)

        raw = box_q * 0.30 + position_score * 0.30 + vol_q * 0.20 + bias * 0.10 + probe_score * 0.10

        # 子类型修正: 上涨中继 vs 底部箱体
        if n >= 60:
            mid_price = d['close'].iloc[n // 2]
            long_gain = (last['close'] / mid_price - 1) if mid_price > 0 else 0
        else:
            long_gain = 0
        subtype_bonus = 1.10 if long_gain > 0.10 else (0.85 if long_gain < -0.05 else 1.0)

        return raw * subtype_bonus
