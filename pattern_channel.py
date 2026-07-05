"""
上升通道形态 (Uptrend Channel)
===============================
从 ZigZag 峰谷定通道 (peaks连线=上轨 + valleys连线=下轨),
验证 HH/HL + 价格沿通道运行, 评分侧重位置+趋势健康+量能.
"""
import numpy as np
import pandas as pd
from scipy import stats
from pattern_utils import _sigmoid, _bell, _geom_match, _annual_slope


class PatternUptrendChannel:
    """P2: 上升通道 — ZigZag峰谷定通道 + HH/HL + 通道内评分"""
    name = 'uptrend_channel'
    label = '上升通道'

    def __init__(self):
        self._channel_info = None  # match() 存入, score() 复用

    # ── 通道发现 ─────────────────────────────────────
    def _find_channel(self, fe):
        """从ZigZag极值点确定上升通道.

        通道 = peaks连线(上轨) + valleys连线(下轨).
        必须满足 HH (高点递增) 和 HL (低点递增).
        """
        d = fe.df; n = len(d)
        peaks = fe.peaks
        valleys = fe.valleys

        if len(peaks) < 2 or len(valleys) < 2:
            return None

        # 取最近3个peak和valley
        rp = peaks[-3:] if len(peaks) >= 3 else peaks[-2:]
        rv = valleys[-3:] if len(valleys) >= 3 else valleys[-2:]

        # HH: 用high价格 (不是close)
        pk_prices = [d['high'].iloc[p] for p in rp]
        hh = all(pk_prices[i] > pk_prices[i - 1] for i in range(1, len(pk_prices)))

        # HL: 用low价格 (不是close)
        vl_prices = [d['low'].iloc[v] for v in rv]
        hl = all(vl_prices[i] > vl_prices[i - 1] for i in range(1, len(vl_prices)))

        if not (hh and hl):
            return None

        # 通道期间: 从最早valley到当前
        ch_start = rv[0]
        ch_end = n - 1
        if ch_end - ch_start < 10:
            return None

        seg = d.iloc[ch_start:ch_end + 1]

        # 只取通道内的peaks/valleys (排除通道开始前的)
        ch_peaks = [p for p in rp if p >= ch_start]
        ch_valleys = [v for v in rv if v >= ch_start]
        if len(ch_peaks) < 2 or len(ch_valleys) < 2:
            return None
        ch_pk_prices = [d['high'].iloc[p] for p in ch_peaks]
        ch_vl_prices = [d['low'].iloc[v] for v in ch_valleys]

        # 上轨: 通道内peaks的线性回归
        px = np.array([p - ch_start for p in ch_peaks])
        py = np.array(ch_pk_prices)
        u_slope, u_intercept, u_r, _, _ = stats.linregress(px, py)
        upper_r2 = max(0, u_r) ** 2

        # 下轨: 通道内valleys的线性回归
        vx = np.array([v - ch_start for v in ch_valleys])
        vy = np.array(ch_vl_prices)
        l_slope, l_intercept, l_r, _, _ = stats.linregress(vx, vy)
        lower_r2 = max(0, l_r) ** 2

        # 通道斜率 (年化%, 上下轨平均)
        mid_price = d['close'].iloc[ch_start]
        ch_slope_annual = (u_slope + l_slope) / 2 * 250 / max(mid_price, 0.01) * 100

        # 验证: 通道期内价格是否在上下轨之间
        x_all = np.arange(len(seg))
        upper_line = u_intercept + u_slope * x_all
        lower_line = l_intercept + l_slope * x_all

        in_ch = ((seg['high'].values <= upper_line * 1.05) &
                 (seg['low'].values >= lower_line * 0.95))
        in_ch_pct = in_ch.sum() / len(seg)
        if in_ch_pct < 0.65:
            return None

        # 触及上下轨次数
        upper_touch = (seg['high'].values >= upper_line * 0.97).sum()
        lower_touch = (seg['low'].values <= lower_line * 1.03).sum()

        # 当前价格在通道内的位置 (0=下轨, 1=上轨)
        cur_upper = u_intercept + u_slope * (n - 1 - ch_start)
        cur_lower = l_intercept + l_slope * (n - 1 - ch_start)
        if cur_upper > cur_lower:
            pos_in_ch = (d['close'].iloc[-1] - cur_lower) / (cur_upper - cur_lower)
        else:
            pos_in_ch = 0.5

        # 通道宽度
        ch_width = np.mean((upper_line - lower_line) /
                           np.maximum((upper_line + lower_line) / 2, 0.01))

        return {
            'ch_start': ch_start,
            'ch_end': ch_end,
            'pk_prices': ch_pk_prices,
            'vl_prices': ch_vl_prices,
            'upper_slope': u_slope,
            'lower_slope': l_slope,
            'ch_slope_annual': ch_slope_annual,
            'upper_r2': upper_r2,
            'lower_r2': lower_r2,
            'in_ch_pct': in_ch_pct,
            'pos_in_ch': pos_in_ch,
            'ch_width': ch_width,
            'upper_touch': upper_touch,
            'lower_touch': lower_touch,
        }

    # ── 形态匹配度 ───────────────────────────────────
    def match(self, fe):
        ch = self._find_channel(fe)
        self._channel_info = ch

        if ch is None:
            return 0

        d = fe.df; last = d.iloc[-1]; n = len(d)

        # f1: HH/HL 递增强度 — 峰谷间距越大越好
        pk_gaps = [(ch['pk_prices'][i] / ch['pk_prices'][i - 1] - 1)
                    for i in range(1, len(ch['pk_prices']))]
        vl_gaps = [(ch['vl_prices'][i] / ch['vl_prices'][i - 1] - 1)
                    for i in range(1, len(ch['vl_prices']))]
        avg_gap = (np.mean(pk_gaps) + np.mean(vl_gaps)) / 2
        f1 = _sigmoid(avg_gap * 100, 8, 0.4)

        # f2: 通道规整度 — 上下轨R²均值
        r2_avg = (ch['upper_r2'] + ch['lower_r2']) / 2
        f2 = min(100, max(10, r2_avg * 100 + 15))

        # f3: 通道内占比 — 价格是否沿通道运行
        f3 = ch['in_ch_pct'] * 100

        # f4: 通道斜率健康度 — sigmoid奖励正斜率, 只惩罚负斜率
        slope = ch['ch_slope_annual']
        f4 = _sigmoid(slope, 15.0, 0.06)
        if slope > 200:
            f4 *= max(0.3, 1.0 - (slope - 200) / 300)
        if slope < -5:
            f4 *= 0.2

        # f5: 通道内位置 — 偏下轨更安全
        f5 = _bell(ch['pos_in_ch'], 0.25, 0.40)

        # f6: MA支撑 — 短均>长均
        mas_ok = 0
        for a, b in [(5, 10), (10, 20)]:
            va = last.get(f'ma{a}'); vb = last.get(f'ma{b}')
            if pd.notna(va) and pd.notna(vb) and va > vb:
                mas_ok += 1
        v20 = last.get('ma20'); v60 = last.get('ma60')
        if pd.notna(v20) and pd.notna(v60) and v20 > v60:
            mas_ok += 1
        f6 = [10, 40, 70, 95][mas_ok]

        return _geom_match(
            {'f1': f1, 'f2': f2, 'f3': f3, 'f4': f4, 'f5': f5, 'f6': f6},
            {'f1': 0.25, 'f2': 0.20, 'f3': 0.20, 'f4': 0.15, 'f5': 0.10, 'f6': 0.10})

    # ── 通道内评分 ───────────────────────────────────
    def score(self, fe):
        """通道内评分: 位置+趋势健康+量能"""
        ch = self._channel_info
        d = fe.df; n = len(d)

        if ch is None:
            return 20

        seg = d.iloc[ch['ch_start']:ch['ch_end'] + 1]

        # 1. 通道质量 (30%): 规整度 + 占比
        r2_avg = (ch['upper_r2'] + ch['lower_r2']) / 2
        ch_q = min(100, r2_avg * 100 + 20) * 0.5 + ch['in_ch_pct'] * 100 * 0.5

        # 2. 通道内位置 (30%): 下轨附近 = 安全买点
        pos = ch['pos_in_ch']
        position_score = 100 - abs(pos - 0.20) * 120
        position_score = max(10, min(100, position_score))

        # 3. 量能健康 (20%): 阳线量 > 阴线量
        up_v = seg[seg['ret'] > 0]['vol_ratio'].mean() if len(seg[seg['ret'] > 0]) > 0 else 0
        down_v = seg[seg['ret'] < 0]['vol_ratio'].mean() if len(seg[seg['ret'] < 0]) > 0 else 0
        vol_bias = _sigmoid(up_v / max(down_v, 0.01), 1.10, 8.0) if down_v > 0 else 65

        # 4. 趋势持续力 (20%): 后半段 vs 前半段斜率
        half = len(seg) // 2
        if half >= 5:
            s_early = _annual_slope(seg['close'].iloc[:half].values)
            s_late = _annual_slope(seg['close'].iloc[half:].values)
            ratio = s_late / max(abs(s_early), 1.0)
            if ratio > 2.0:
                stamina = 30
            elif ratio > 1.3:
                stamina = 70
            elif ratio > 0.7:
                stamina = 85
            elif ratio > 0.3:
                stamina = 55
            else:
                stamina = 30
        else:
            stamina = 60

        raw = ch_q * 0.30 + position_score * 0.30 + vol_bias * 0.20 + stamina * 0.20
        return raw
