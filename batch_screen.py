"""
批量多日选股打分引擎
==================
对多个目标日期分别运行完整筛选流程，结果存入 screening_history 表。
从 stock_screener.py 提取核心逻辑，参数化 TARGET_DATE。
"""
import sys, io, os, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import os
import sqlite3
import pandas as pd
import numpy as np
from scipy import stats
from tqdm import tqdm
import warnings
warnings.filterwarnings('ignore')

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stock_data.db')
LOOKBACK_DAYS = 60  # 股票强度需要更长回溯(前45天+近15天)
MIN_TURN = 2.0
MIN_PRICE = 5.0
# MAX_PRICE 已移除 — 不限制最高股价

# ══════════════════════════════════════════════════════════════
# 算法版本号: 修改评分公式后手动升级 → 自动触发全量重评
#   V1 = 动态Y/L摆动点检测 + 趋势/震荡双引擎 (2026-07-02)
#   V2 = L窗口放宽 + 下影线替代试盘后走势 + Y/L双重验证 (2026-07-04)
# ══════════════════════════════════════════════════════════════
ALGO_VERSION = "V2"

# ============================================================
# 辅助函数
# ============================================================
def get_lookback_dates(target, n_days):
    conn = sqlite3.connect(DB_PATH)
    dates = pd.read_sql_query(
        "SELECT DISTINCT date FROM stock_daily WHERE date <= ? ORDER BY date DESC LIMIT ?",
        conn, params=(target, n_days)
    )['date'].tolist()
    conn.close()
    return sorted(dates)

def get_eligible_stocks(target, start):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(f"""
        WITH latest AS (
            SELECT code, close, volume, amount, turn, pctChg
            FROM stock_daily WHERE date = '{target}'
        ),
        avg_data AS (
            SELECT code, AVG(turn) as avg_turn, AVG(volume) as avg_vol,
                   AVG(close) as avg_close, COUNT(*) as n_days
            FROM stock_daily
            WHERE date >= '{start}' AND date <= '{target}'
            GROUP BY code
        )
        SELECT l.code, l.close, l.turn, l.pctChg,
               a.avg_turn, a.avg_close, a.n_days
        FROM latest l
        JOIN avg_data a ON l.code = a.code
        WHERE a.avg_turn >= {MIN_TURN}
          AND a.avg_close >= {MIN_PRICE}
          AND l.close > 0
          AND a.n_days >= {LOOKBACK_DAYS * 0.6}
    """, conn)
    conn.close()
    return df

def quick_filter(codes, start, end):
    """快速预筛选"""
    conn = sqlite3.connect(DB_PATH)
    placeholders = ','.join(['?'] * len(codes))
    df = pd.read_sql_query(f"""
        SELECT code, date, close, volume, amount, turn, pctChg,
               open, high, low, preclose
        FROM stock_daily
        WHERE code IN ({placeholders})
          AND date >= '{start}' AND date <= '{end}'
        ORDER BY code, date
    """, conn, params=list(codes))
    conn.close()
    df['date'] = pd.to_datetime(df['date'])

    passed = []
    failed_reasons = {'low_vol': 0, 'no_retreat': 0, 'too_stable': 0, 'distribution': 0}
    total = len(df['code'].unique())

    for code, group in tqdm(df.groupby('code'), desc='  预筛选', total=total, leave=False):
        g = group.sort_values('date').copy()
        if len(g) < 10:
            continue
        g['ret'] = g['close'].pct_change()
        g['vol_ma5'] = g['volume'].rolling(5).mean()
        g['vol_ratio'] = g['volume'] / g['vol_ma5']
        g['amplitude'] = (g['high'] - g['low']) / g['preclose']
        recent = g.tail(15)
        if recent['amplitude'].mean() < 0.03:
            failed_reasons['too_stable'] += 1
            continue
        last5 = g.tail(5)
        dist_days = ((last5['ret'] < 0) & (last5['vol_ratio'] > 1.15)).sum()
        if dist_days >= 3:
            failed_reasons['distribution'] += 1
            continue
        retreat_mask = (recent['ret'] < 0) & (recent['vol_ratio'] < 0.85)
        if retreat_mask.sum() == 0:
            failed_reasons['no_retreat'] += 1
            continue
        passed.append(code)
    return passed, failed_reasons

def load_stock_data(code, start, end):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT code, date, open, high, low, close, preclose, volume, amount, turn, pctChg
        FROM stock_daily WHERE code=? AND date>=? AND date<=? ORDER BY date
    """, conn, params=(code, start, end))
    conn.close()
    if len(df) < 10:
        return None
    df['date'] = pd.to_datetime(df['date'])
    return df


# ============================================================
# StockScorer (from stock_screener.py)
# ============================================================
class StockScorer:
    """单只股票的完整量价指标计算与评分"""
    def __init__(self, df: pd.DataFrame, index_returns: pd.Series = None):
        self.df = df.reset_index(drop=True)
        self.index_returns = index_returns  # 沪深300日收益率 (date-indexed Series)
        self._precompute()
        self.scores = {}

    def _precompute(self):
        d = self.df
        d['ret'] = d['close'].pct_change()
        d['ret_sign'] = np.sign(d['ret'])
        d['amplitude'] = (d['high'] - d['low']) / d['preclose']
        d['vol_ma5'] = d['volume'].rolling(5).mean()
        d['vol_ma10'] = d['volume'].rolling(10).mean()
        d['vol_ratio'] = d['volume'] / d['vol_ma5']
        d['amount_ma5'] = d['amount'].rolling(5).mean()
        d['obv'] = (d['ret_sign'] * d['volume']).fillna(0).cumsum()
        d['typical_price'] = (d['high'] + d['low'] + d['close']) / 3
        d['vwap'] = (d['amount'] / d['volume']).fillna(d['close'])
        d['ma5'] = d['close'].rolling(5).mean()
        d['ma10'] = d['close'].rolling(10).mean()
        d['ma20'] = d['close'].rolling(20).mean()
        d['ma60'] = d['close'].rolling(60).mean()
        d['upper_shadow_pct'] = (d['high'] - d[['open', 'close']].max(axis=1)) / d['open']
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
        self.df = d
        self.limit_pct = limit_pct

    # ── 动态 Y/L 摆动点检测 ──
    def _find_swing_points(self, M=6, L_lookback=45):
        """找到真正的局部高 Y 和局部低 L，自适应划分上涨段/调整段。

        算法：
          Y: 从 T-1 向前找第一个局部高点（M 日窗口），且之后到 T-1 无更高收盘价
          L: 从 Y-1 向前找第一个局部低点（M 日窗口），且在其前 L_lookback 天内无更低收盘价
             (默认45天≈2个月, 避免一年前的历史低点被当成当前上涨段的起点)

        自适应 M: 高波动率 → 加大 M 滤噪，低波动率 → 减小 M 避免漏信号
        递归降级: M 找不到 → M-2 重试，最小到 2
        兜底: 找不到就用 argmax/argmin

        返回 (Y_idx, L_idx)，索引从0起
        """
        closes = self.df['close'].values
        n = len(closes)
        if n < 20:
            # 数据太短，回退固定切分
            split = max(n - 5, n * 2 // 3)
            return split, split // 2

        # ── M 自适应：基于近20日振幅均值 ──
        if 'amplitude' in self.df.columns:
            recent_amp = self.df['amplitude'].tail(20).mean()
        else:
            recent_amp = 0.03  # 默认 3%
        if recent_amp > 0.055:
            M = min(M + 2, 10)   # 高波动 → 大窗口滤噪
        elif recent_amp < 0.022:
            M = max(M - 2, 3)    # 低波动 → 小窗口不丢信号

        def _is_local_max(i, m):
            # 左边界必须完整; 右边界允许不足 (由条件2 "之后无更高" 兜底)
            if i < m:
                return False
            r = min(n, i + m + 1)
            w = closes[i-m:r]
            mx = w.max()
            return closes[i] == mx and list(w).count(mx) == 1

        def _is_local_min(i, m):
            if i < m or i + m >= n:
                return False
            w = closes[i-m:i+m+1]
            mn = w.min()
            return closes[i] == mn and list(w).count(mn) == 1

        # ── 找 Y：最近局部高点，之后无新高 ──
        Y_idx = None
        cur_M = M
        while Y_idx is None and cur_M >= 2:
            for i in range(n - 2, cur_M - 1, -1):
                if _is_local_max(i, cur_M):
                    # 条件2：之后到 T-1 无更高收盘价
                    if closes[i] >= closes[i+1:].max():
                        Y_idx = i
                        break
            cur_M -= 2

        # 兜底：用 [0, T-1] 区间最高点 (但要满足"之后无更高")
        if Y_idx is None:
            Y_idx = int(np.argmax(closes[:-1])) if n > 1 else n - 2

        # ── Y 最终验证：之后到 T-1 无更高收盘价 ──
        after_max = closes[Y_idx+1:].max()
        if closes[Y_idx] < after_max:
            # Y 被后面的高点超越, 把 Y 推到那个高点
            offset = int(np.argmax(closes[Y_idx+1:])) + 1
            Y_idx = Y_idx + offset
            # 再次检查最小调整段
            if Y_idx > n - 3:
                Y_idx = n - 3  # 至少留2天调整段

        # 软约束：调整段至少留 1 天, 上涨段至少留 2 天
        if Y_idx >= n - 1:
            Y_idx = n - 2  # 调整段为0才修正
        if Y_idx < 2:
            Y_idx = 2      # 上涨段为0才修正

        # ── 找 L：Y 之前最近的局部低点 ──
        # 只要求 M-window 局部最低, 不要求全局/窗口最低 (否则长牛股 L 会被拖到远古低点)
        L_idx = None
        cur_M = M
        while L_idx is None and cur_M >= 2:
            for i in range(Y_idx - 1, cur_M - 1, -1):
                if _is_local_min(i, cur_M):
                    L_idx = i
                    break
            cur_M -= 2

        # 兜底：用 Y 之前 L_lookback 窗口内最低点
        if L_idx is None:
            lb = max(0, Y_idx - L_lookback)
            L_idx = int(lb + np.argmin(closes[lb:Y_idx]))

        # 确保最小上涨段长度（至少 12 天），但爆发式行情放宽
        min_up_len = 12
        if Y_idx - L_idx < min_up_len:
            up_gain = closes[Y_idx] / closes[L_idx] - 1
            if up_gain < 0.25:  # 涨幅不足25%才补天数, 爆发式行情(>25%)保持原L
                search_start = max(0, Y_idx - 30)
                L_idx = int(search_start + np.argmin(closes[search_start:Y_idx]))
                if Y_idx - L_idx < min_up_len:
                    L_idx = max(0, Y_idx - min_up_len)

        # ── 最终验证：L 必须是 [L, Y] 区间内的最低点 (最后一步) ──
        seg = closes[L_idx : Y_idx + 1]
        true_min_offset = int(np.argmin(seg))
        if true_min_offset > 0:
            L_idx = L_idx + true_min_offset

        return Y_idx, L_idx

    # ── 连续映射工具函数 ──
    @staticmethod
    def _sigmoid(x, center, steepness):
        """Sigmoid: 100 / (1 + exp(-k * (x - x0))), 输出 [0, 100]"""
        return 100.0 / (1.0 + np.exp(-steepness * (x - center)))

    @staticmethod
    def _bell(x, mu, sigma):
        """钟形曲线: 100 * exp(-((x - mu) / sigma)^2), 输出 [0, 100]"""
        return 100.0 * np.exp(-((x - mu) / sigma) ** 2)

    # ── 六维评分 (全连续函数, 无硬阈值) ──

    def score_washout_quality(self, recent_days=15, strength_score=None):
        """洗盘质量: 缩量程度40% + 缩量占比35% + 回撤深度25% (全连续)
        v4: 使用动态调整段 [Y, T]，无动态划分时退回到 tail(recent_days)
        P0门控：strength_score<20 → 无涨可洗，缩量阴跌不是洗盘 → return 0"""
        # 新增前置门控：没有上涨前科的缩量下跌 = 阴跌，不是洗盘
        if strength_score is not None and strength_score < 20:
            return 0

        # v4: 优先用动态调整段
        Y_idx = getattr(self, 'Y_idx', None)
        if Y_idx is not None and Y_idx + 1 < len(self.df):
            d = self.df.iloc[Y_idx + 1:]  # 动态调整段 [Y+1, T]
        else:
            d = self.df.tail(recent_days)  # fallback
        down_days = d[d['ret'] < 0]
        n_down = len(down_days)

        if n_down < 2:
            return 30

        # 缩量程度 (40%): sigmoid — vol_ratio 越低越好, center=0.75
        avg_shrink = down_days['vol_ratio'].mean()
        shrink_score = self._sigmoid(avg_shrink, 0.75, 8.0)

        # 缩量占比 (35%): 下跌日中 vol_ratio<0.85 的比例, 线性
        shrink_pct = (down_days['vol_ratio'] < 0.85).sum() / n_down
        shrink_pct_score = shrink_pct * 100

        # 回撤深度 (25%): bell curve — 最优 15%, sigma=10%
        cummax = d['close'].cummax()
        max_dd = ((cummax - d['close']) / cummax).max()
        dd_score = self._bell(max_dd, 0.15, 0.10)
        # 极端衰减: <3% 或 >40% 回撤
        if max_dd < 0.03 or max_dd > 0.40:
            dd_score *= 0.4

        return shrink_score * 0.40 + shrink_pct_score * 0.35 + dd_score * 0.25

    def score_probe_test(self, recent_days=15):
        """试盘信号: 上影线30% + 量能25% + 下影线质量35% + 频率加成10% (全连续)
        v4: 使用动态调整段 [Y, T]，无动态划分时退回到 tail(recent_days)"""
        # v4: 优先用动态调整段
        Y_idx = getattr(self, 'Y_idx', None)
        if Y_idx is not None and Y_idx + 1 < len(self.df):
            d = self.df.iloc[Y_idx + 1:]  # 动态调整段 [Y+1, T]
        else:
            d = self.df.tail(recent_days)  # fallback
        probe_mask = (
            (d['vol_ratio'] > 1.2) &
            (d['upper_shadow_pct'] > 0.03) &
            (d['is_limit_up'] == 0) &
            (d['ret'] < 0.08)
        )
        probe_days = d[probe_mask]
        if len(probe_days) == 0:
            return 15

        latest = probe_days.iloc[-1]

        # 上影线质量 (30%): 3%-12% 线性映射 [90→30], 两侧连续衰减
        shadow = latest['upper_shadow_pct']
        if shadow <= 0.03:
            shadow_score = max(10, 30 - (0.03 - shadow) * 200)
        elif shadow <= 0.12:
            shadow_score = 90 - (shadow - 0.03) / 0.09 * 60
        else:
            shadow_score = max(10, 30 - (shadow - 0.12) * 40)

        # 试盘量能 (25%): 1.2-3.5 线性映射 [90→30]
        vr = latest['vol_ratio']
        if vr <= 1.2:
            vol_score = max(10, 30 - (1.2 - vr) * 30)
        elif vr <= 3.5:
            vol_score = 90 - (vr - 1.2) / 2.3 * 60
        else:
            vol_score = max(10, 30 - (vr - 3.5) * 10)

        # 下影线质量 (35%): bell(下影线占比, 中心5%) — 锤子线形态, 有支撑信号
        low_shadow = (min(latest['open'], latest['close']) - latest['low']) / latest['preclose']
        low_shadow_score = self._bell(low_shadow * 100, 5.0, 3.0)  # 最优5%, sigma=3%

        freq_bonus = min(15, (len(probe_days) - 1) * 8)
        return min(100, shadow_score * 0.30 + vol_score * 0.25 + low_shadow_score * 0.35 + freq_bonus)

    def score_launch_readiness(self, recent_days=5):
        """启动准备: 质量阳线55% + 均线配合15% + 稳定性10% + 信号新鲜度20% (涨停不惩罚)"""
        d = self.df.tail(recent_days)
        full = self.df

        # 质量阳线 (55%): bell(涨幅, 4.5%) + bell(量比, 1.5)
        quality_up = d[(d['ret'] > 0.02) & (d['vol_ratio'] > 1.0) & (d['is_limit_up'] == 0)]
        if len(quality_up) == 0:
            # 无质量阳线, 用最近阳线降级评估
            any_up = d[d['ret'] > 0]
            if len(any_up) > 0:
                latest = any_up.iloc[-1]
                base_score = self._bell(latest['ret'] * 100, 4.5, 3.5) * 0.55 + self._bell(latest['vol_ratio'], 1.5, 1.0) * 0.45
                base_score *= 0.5  # 降级
            else:
                base_score = 10
        else:
            latest = quality_up.iloc[-1]
            ret_score = self._bell(latest['ret'] * 100, 4.5, 2.5)
            vol_score = self._bell(latest['vol_ratio'], 1.5, 0.8)
            base_score = ret_score * 0.55 + vol_score * 0.45

        # 均线配合 (15%): 线性 — 站上MA数
        full_ma5 = full['close'].rolling(5).mean()
        full_ma10 = full['close'].rolling(10).mean()
        full_ma20 = full['close'].rolling(20).mean()
        ma_count = 0
        c = full['close'].iloc[-1]
        if pd.notna(full_ma5.iloc[-1]) and c > full_ma5.iloc[-1]:
            ma_count += 1
        if pd.notna(full_ma10.iloc[-1]) and c > full_ma10.iloc[-1]:
            ma_count += 1
        if pd.notna(full_ma20.iloc[-1]) and c > full_ma20.iloc[-1]:
            ma_count += 1
        ma_bonus = [-8, 0, 6, 12][ma_count]

        # 稳定性 (10%): 近3日振幅+价格平坦
        last3 = d.tail(3)
        if len(last3) >= 3:
            amp_score = self._sigmoid(last3['amplitude'].std(), 0.012, -200.0)
            flat_score = self._bell(abs(last3['close'].iloc[-1] / last3['close'].iloc[0] - 1), 0, 0.02)
            stable_bonus = (amp_score * 0.5 + flat_score * 0.5) * 0.10
        else:
            stable_bonus = 0

        # 信号新鲜度 (20%): 连续衰减 days_ago * 5
        staleness_penalty = 0
        if len(quality_up) > 0:
            last_up_idx = quality_up.index[-1]
            days_since_up = len(d) - 1 - (last_up_idx - d.index[0])
            staleness_penalty = min(20, days_since_up * 5)
        else:
            staleness_penalty = 15

        raw = base_score + ma_bonus + stable_bonus - staleness_penalty
        return max(0, min(100, raw))

    def score_ma_convergence(self):
        """均线粘合: 粘合度55% + 价格位置 + 均线排列 + 收敛加成 (sigmoid连续化)"""
        d = self.df
        close = d['close'].iloc[-1]
        ma_values = {}
        for n in [5, 10, 20, 60]:
            val = d[f'ma{n}'].iloc[-1]
            if pd.notna(val) and val > 0:
                ma_values[n] = val
        if len(ma_values) < 3:
            return 40

        mas = list(ma_values.values())
        ma_range = max(mas) - min(mas)
        ma_mean = np.mean(mas)
        conv_ratio = 1.0 - (ma_range / ma_mean)

        # 粘合度 (55%): sigmoid center=0.92, 陡峭度20
        conv_score = self._sigmoid(conv_ratio, 0.92, 20.0)

        # 价格位置: 连续衰减 — 偏离越远分越低
        price_dev = abs(close - ma_mean) / ma_mean
        pos_score = max(-5, 12 - price_dev * 180)

        # 均线排列: 多头/混乱/空头
        if 5 in ma_values and 10 in ma_values and 20 in ma_values:
            if ma_values[5] > ma_values[10] > ma_values[20]:
                align_bonus = 10
            elif ma_values[5] > ma_values[10]:
                align_bonus = 5
            elif ma_values[5] < ma_values[10] < ma_values[20]:
                align_bonus = -5
            else:
                align_bonus = 0
        else:
            align_bonus = 0

        # 收敛加成: 粘合度在变紧
        tight_bonus = 0
        if len(d) >= 6:
            past_mas = []
            for n in [5, 10, 20, 60]:
                pv = d[f'ma{n}'].iloc[-6]
                if pd.notna(pv) and pv > 0:
                    past_mas.append(pv)
            if len(past_mas) >= 3:
                past_conv = 1.0 - (max(past_mas) - min(past_mas)) / np.mean(past_mas)
                delta = conv_ratio - past_conv
                tight_bonus = self._sigmoid(delta, 0.003, 300.0) * 0.10

        return max(0, min(100, conv_score * 0.55 + pos_score + align_bonus + tight_bonus))

    def score_fund_flow(self, recent_days=15):
        """资金流向: OBV趋势30% + OBV强度20% + VWAP位置25% + 量比偏斜25% (全连续)"""
        d = self.df.tail(recent_days)

        # OBV趋势 (30%): 连续 — 归一化斜率
        obv_slope, _, r_value, _, _ = stats.linregress(np.arange(len(d)), d['obv'].values)
        r_value = np.nan_to_num(r_value, nan=0.0)  # NaN guard: 常数列→无相关性
        r2 = r_value ** 2
        obv_mean = abs(d['obv'].mean())
        if obv_mean > 0:
            norm_slope = obv_slope / obv_mean * 100
        else:
            norm_slope = 0
        obv_trend = 50 + np.clip(norm_slope * 2, -40, 50)

        # OBV强度 (20%): R² 线性映射
        obv_strength = min(100, r2 * 100)

        # VWAP位置 (25%): sigmoid — 价格高于VWAP是好事
        vwap_premium = (d['close'].iloc[-1] / d['vwap'].iloc[-1] - 1)
        vwap_score = self._sigmoid(vwap_premium, 0.005, 300.0)

        # 量比偏斜 (25%): sigmoid — 涨放量/跌缩量比率
        up_vol = d[d['ret'] > 0]['vol_ratio'].mean()
        down_vol = d[d['ret'] < 0]['vol_ratio'].mean()
        if pd.notna(up_vol) and pd.notna(down_vol) and down_vol > 0:
            bias = up_vol / down_vol
            bias_score = self._sigmoid(bias, 1.3, 4.0)
        else:
            bias_score = 50

        return obv_trend * 0.30 + obv_strength * 0.20 + vwap_score * 0.25 + bias_score * 0.25

    def score_volume_health(self, recent_days=15):
        """量价健康: 健康日35% + 出货惩罚15% + 洗盘加成30% + 量价同步20% (全连续)"""
        d = self.df.tail(recent_days)
        n = max(len(d), 1)

        # 健康日 (35%): 涨放量 — sigmoid
        healthy_pct = ((d['ret'] > 0) & (d['vol_ratio'] > 1.0)).sum() / n
        h_score = self._sigmoid(healthy_pct, 0.25, 10.0)

        # 出货惩罚 (15%): 跌放量 — 反向sigmoid (越高越差)
        dist_pct = ((d['ret'] < 0) & (d['vol_ratio'] > 1.15)).sum() / n
        d_penalty = 100 - self._sigmoid(dist_pct, 0.15, 15.0)

        # 洗盘加成 (30%): 跌缩量 — sigmoid
        washout_pct = ((d['ret'] < 0) & (d['vol_ratio'] < 0.85)).sum() / n
        w_bonus = self._sigmoid(washout_pct, 0.20, 8.0)

        # 量价同步 (20%): 量价方向一致性
        sync = (np.sign(d['close'].diff()) == np.sign(d['volume'].diff())).mean()
        sync_score = (sync - 0.50) * 200

        raw = h_score * 0.35 + d_penalty * 0.15 + w_bonus * 0.30 + sync_score * 0.20
        return max(0, min(100, raw))

    def score_volume_price_health(self):
        """量价健康：资金流向 + 量价健康 等权合并"""
        ff = np.nan_to_num(self.score_fund_flow(), nan=50.0)
        vh = np.nan_to_num(self.score_volume_health(), nan=50.0)
        return (ff + vh) / 2.0

    # ── 股票强度 (Stock Strength) ──
    def score_stock_strength(self, force_class=None):
        """衡量洗盘前的上涨强度：趋势+量能+回调+相对优势
        v4: 使用动态 Y/L 划分上涨段和调整段，替代固定 45/15 窗口
        force_class: 若提供则跳过分类器, 强制使用 'trend'/'choppy' 参数集"""
        d = self.df
        n = len(d)

        # v4: 使用动态 Y/L 划分
        Y_idx = getattr(self, 'Y_idx', n - 15)
        L_idx = getattr(self, 'L_idx', 0)

        strength = d.iloc[L_idx : Y_idx + 1].copy()  # 上涨段 [L, Y]
        washout = d.iloc[Y_idx + 1:]                  # 调整段 [Y+1, T]

        if len(strength) < 10:
            return 0  # 上涨段太短 → 出局

        # === 趋势熔断（P1收紧：下跌直接归零） ===
        x = np.arange(len(strength))
        log_y = np.log(np.maximum(strength['close'].values, 0.01))
        slope, _, r_value, _, _ = stats.linregress(x, log_y)
        r_value = np.nan_to_num(r_value, nan=0.0)  # NaN guard
        annual_slope = slope * 250 * 100  # 年化%

        # 硬闸1：明显下跌趋势 → 直接出局
        if annual_slope < -5:
            return 0

        # 硬闸2：微跌或零增长 → 几乎出局
        if annual_slope < 0:
            return max(0, 5 + annual_slope)  # -5%→0分, -1%→4分, 逼近0%→5分

        # 硬闸3：横盘无趋势 + R²极低 = 随机游走
        r2 = r_value ** 2
        if annual_slope < 3 and r2 < 0.30:
            return 5

        # ★ 趋势/震荡分类 (在子函数调用前, 使各因子感知类别)
        if force_class is not None:
            trend_class = force_class
        else:
            trend_class, _ = self._classify_trend(strength)

        # A. 前期趋势强度 (35%) — 传类别
        trend_score = self._strength_trend(strength, trend_class)

        # 短路：趋势子因子极低 → 不给其他三个因子救场机会
        if trend_score < 15:
            return trend_score

        # B. 量能积累确认 (25%)
        volume_score = self._strength_volume(strength)

        # C. 回调有序性 (25%) — 传类别: 趋势类奖励浅回调, 震荡类奖励适度回调
        pullback_score = self._strength_pullback(strength, washout, trend_class)

        # D. 相对优势 vs 沪深300 (15%)
        relative_score = self._strength_relative(strength)

        raw = trend_score * 0.35 + volume_score * 0.25 + pullback_score * 0.25 + relative_score * 0.15
        return max(0, min(100, np.nan_to_num(raw, nan=0.0)))

    def _strength_trend(self, strength, trend_class='choppy'):
        """子因子A：前期趋势强度（对数OLS斜率+R², bell曲线）
        趋势类: Bell(optimal=35%, sigma=30) 奖励强势斜率
        震荡类: Bell(optimal=25%, sigma=18) 原逻辑"""
        x = np.arange(len(strength))
        log_y = np.log(np.maximum(strength['close'].values, 0.01))
        slope, _, r_value, _, _ = stats.linregress(x, log_y)
        r_value = np.nan_to_num(r_value, nan=0.0)  # NaN guard
        r2 = r_value ** 2
        annual_slope = slope * 250 * 100  # 年化%

        # 斜率评分: 趋势类用sigmoid(奖励涨得快), 震荡类用Bell(奖励适中)
        if trend_class == 'trend':
            # sigmoid: 年化25%→50分, 50%→88分, 75%+→98分
            slope_score = self._sigmoid(annual_slope, 25.0, 0.08)
            # 极值保护: 年化>120% → 加速赶顶风险, 施加连续衰减
            # 120%→不衰减, 160%→打8折, 200%→打6折, 220%→打5折(底线)
            if annual_slope > 120:
                decay = max(0.50, 1.0 - (annual_slope - 120) / 200.0)
                slope_score *= decay
        else:
            slope_score = self._bell(annual_slope, 25, 18)

        # 斜率折扣
        if trend_class == 'trend':
            # 趋势类: 仅惩罚极弱斜率, 不惩罚强势
            if annual_slope < 0:
                slope_score *= 0.1   # 负斜率 → 几乎清零
            elif annual_slope < 10:
                slope_score *= 0.40  # <10% 温和打折
        else:
            if annual_slope > 80:
                slope_score *= 0.5
            elif annual_slope < 5:    # 0~5% 正斜率，仍然偏弱
                slope_score *= 0.25
            elif annual_slope < 15:   # 5~15% 温和上涨适当打折
                slope_score *= 0.70

        # R²: 高加分, 低不扣 — 线性映射带保底
        if r2 >= 0.70:
            r2_score = 85 + (r2 - 0.70) / 0.30 * 15
        elif r2 >= 0.40:
            r2_score = 60 + (r2 - 0.40) / 0.30 * 25
        else:
            r2_score = 40 + r2 * 50

        # 趋势斜率很低（<10%），R²再高也不能救太多 (趋势类不惩罚)
        raw = slope_score * 0.55 + r2_score * 0.45
        if trend_class != 'trend' and annual_slope < 10:
            raw *= 0.6  # 震荡类: 斜率不够，高R²只是"稳定横盘"

        return max(0, min(100, raw))

    def _strength_volume(self, strength):
        """子因子B：量能积累确认（连续溢价+OBV+背离检测）"""
        up = strength[strength['ret'] > 0]
        if len(up) < 3:
            return 25

        all_avg_vr = strength['vol_ratio'].mean()
        up_avg_vr = up['vol_ratio'].mean()

        # 上涨日量比溢价: sigmoid — premium>1.0 即好
        if all_avg_vr > 0:
            premium = up_avg_vr / all_avg_vr
            premium_score = self._sigmoid(premium, 1.08, 15.0)
        else:
            premium_score = 50

        # OBV 趋势: 信息比率化（P2修复：替代 obv_slope/obv_mean 归一化）
        x = np.arange(len(strength))
        obv_slope, _, obv_r_value, _, _ = stats.linregress(x, strength['obv'].values)
        obv_r_value = np.nan_to_num(obv_r_value, nan=0.0)  # NaN guard
        obv_r2 = obv_r_value ** 2
        obv_diff = strength['obv'].diff().dropna()
        if len(obv_diff) > 1 and obv_diff.std() > 0:
            obv_ir = obv_diff.mean() / (obv_diff.std() + 1e-10)  # 信息比率
            obv_trend = 50 + np.clip(obv_ir * 10, -30, 50)
        else:
            obv_trend = 50
        obv_str = min(100, max(10, obv_r2 * 100))

        # 量价背离: 连续 — 价涨量不跟按程度衰减
        close_slope, _, _, _, _ = stats.linregress(x, strength['close'].values)
        if close_slope > 0 and obv_slope <= 0:
            div_penalty = 20
        elif close_slope > 0:
            ratio = obv_slope / max(close_slope, 1e-10)
            div_penalty = self._sigmoid(ratio, 0.5, -5.0) * 0.20  # ratio低→惩罚高
        else:
            div_penalty = 0

        return max(0, min(100,
            premium_score * 0.40 + (obv_trend * 0.50 + obv_str * 0.50) * 0.40 - div_penalty * 0.20
        ))

    def _strength_pullback(self, strength, washout, trend_class='choppy'):
        """子因子C：回调有序性（深度bell/sigmoid + 缩量sigmoid + 底部收敛）
        趋势类: sigmoid奖励浅回调(越浅越高分); 震荡类: bell奖励适度回调(25%最优)"""
        peak_val = strength['close'].max()
        end_val = strength['close'].iloc[-1]

        # === 判断 peak 出现的时间位置 ===
        peak_idx_pos = strength['close'].idxmax()
        # peak 在窗口前 30% 就见顶，之后一路跌 → 这不是"回调"，是"见顶下跌"
        if peak_idx_pos < len(strength) * 0.30:
            post_peak = strength.loc[peak_idx_pos:]
            if post_peak['close'].iloc[-1] < peak_val * 0.85:
                return 3  # 主跌浪，不是洗盘回调

        dd = (peak_val - end_val) / peak_val if peak_val > 0 else 0

        # 回调深度 (35%): 趋势类 vs 震荡类 不同函数
        if trend_class == 'trend':
            # 趋势类: sigmoid — 回撤越浅越高分, 奖励顺势持有
            depth_score = self._sigmoid(dd, 0.10, -30.0)
            # 无浅回调惩罚 (浅回撤=趋势强, 是好事)
        else:
            # 震荡类: bell — 25%回撤最优 (原逻辑)
            depth_score = self._bell(dd, 0.25, 0.12)
            if dd < 0.05:
                depth_score *= 0.25  # 没回调=没洗盘
        # 极端回撤 (两类共用)
        if dd > 0.60:
            return 3              # 跌超60%，直接给接近0分
        elif dd > 0.50:
            depth_score *= 0.1    # 跌太深趋势已坏

        # 回调阶段缩量 (35%): sigmoid
        peak_idx = strength['close'].idxmax()
        pullback = strength.loc[peak_idx:]
        down_in_pb = pullback[pullback['ret'] < 0]
        if len(down_in_pb) >= 2:
            avg_shrink = down_in_pb['vol_ratio'].mean()
            shrink_score = self._sigmoid(avg_shrink, 0.75, 8.0)
        else:
            shrink_score = 40

        # 底部形态 (30%): 振幅std + 价格平坦 — 连续
        bottom = strength.tail(5)
        amp_std = bottom['amplitude'].std()
        price_flat = abs(bottom['close'].iloc[-1] / bottom['close'].iloc[0] - 1)
        amp_q = self._sigmoid(amp_std, 0.02, -200.0)
        flat_q = self._bell(price_flat, 0, 0.03)
        bottom_score = amp_q * 0.5 + flat_q * 0.5

        return max(0, min(100,
            depth_score * 0.35 + shrink_score * 0.35 + bottom_score * 0.30
        ))

    def _strength_relative(self, strength):
        """子因子D：相对优势 vs 沪深300 (sigmoid超额 + sigmoid胜率)"""
        if self.index_returns is None or len(self.index_returns) == 0:
            return 50

        excess_sum = 0.0
        win_count = 0
        total = 0

        for _, row in strength.iterrows():
            d_val = row['date']
            if hasattr(d_val, 'strftime'):
                d_str = d_val.strftime('%Y-%m-%d')
            else:
                d_str = str(d_val)[:10]

            if d_str not in self.index_returns.index:
                continue
            stock_ret = row['ret']
            if pd.isna(stock_ret):
                continue
            idx_ret = self.index_returns[d_str]
            excess_sum += (stock_ret - idx_ret)
            if stock_ret > idx_ret:
                win_count += 1
            total += 1

        if total < 5:
            return 50

        # 年化超额收益: sigmoid — 0%→50分, 15%→75分, 30%→95分
        avg_excess = excess_sum / total * 100
        annual_excess = avg_excess * 250
        excess_score = self._sigmoid(annual_excess, 8, 0.06)

        # 胜率: sigmoid — 50%→50分, 55%→70分, 60%→88分
        win_rate = win_count / total
        wr_score = self._sigmoid(win_rate, 0.52, 20.0)

        return max(0, min(100, excess_score * 0.55 + wr_score * 0.45))

    @staticmethod
    def _ma_slope(closes, window):
        """计算收盘价 MA-window 的年化对数OLS斜率 (%)
        返回: annual_slope (%) 或 0 (数据不足)"""
        n = len(closes)
        if n < window + 5:
            return 0.0
        ma = np.array([closes[max(0,i-window+1):i+1].mean() for i in range(n)])
        # 取最后 window 个 MA 值
        ma_seg = ma[-window:]
        x = np.arange(len(ma_seg))
        log_ma = np.log(np.maximum(ma_seg, 0.01))
        slope, _, _, _, _ = stats.linregress(x, log_ma)
        return slope * 250 * 100  # 年化%

    def _classify_trend(self, strength):
        """趋势/震荡分类器 v2: MA60定方向 + MA5验动量 + Peak-DD + R²
        - MA60 年化斜率: 中期趋势方向 (权重 35%)
        - MA5 联动确认:    短期动量是否与中期同向 (权重 15%)
        - Peak-End DD%:    回撤控制 (权重 25%)
        - R²:              趋势可靠性 (权重 25%)
        >= 55 → 'trend', < 55 → 'choppy'"""
        closes = strength['close'].values
        n = len(closes)
        if n < 65:
            # 数据不足60天 → fallback: 用全程 ER (原逻辑)
            path_len = np.sum(np.abs(np.diff(closes)))
            net_len = abs(closes[-1] - closes[0])
            er = net_len / path_len if path_len > 0 else 0
            er_score = self._sigmoid(er, 0.12, 30.0)
            ma60_score = er_score
            ma5_score = 50  # 中性
        else:
            # 1) MA60 年化斜率 — 中期趋势方向
            ma60_slope = self._ma_slope(closes, 60)
            # 中心 15%: 年化>15%开始视为趋势, <15%视为弱势
            ma60_score = self._sigmoid(ma60_slope, 15.0, 8.0)

            # 2) MA5 联动确认 — 短期动量校验 (用20点MA5而非5点,避免噪声)
            ma5_slope = self._ma_slope(closes, 5)  # 先算MA5序列
            # 用最后20个MA5值做斜率, 更稳健 (5个点太噪, 年化放大荒谬)
            ma5_series = np.array([closes[max(0,i-4):i+1].mean() for i in range(len(closes))])
            if len(ma5_series) >= 20:
                ma5_seg = ma5_series[-20:]
                x5 = np.arange(20)
                log_ma5_seg = np.log(np.maximum(ma5_seg, 0.01))
                s5, _, _, _, _ = stats.linregress(x5, log_ma5_seg)
                ma5_slope = s5 * 250 * 100
            ma5_divergence = abs(ma5_slope - ma60_slope)  # 背离幅度
            if np.sign(ma5_slope) == np.sign(ma60_slope) and ma60_slope > 0:
                # 同向向上: 趋势被短期确认 → 高分
                ma5_score = self._sigmoid(ma5_slope, 10.0, 10.0)
            elif ma60_slope > 0 and ma5_slope < 0:
                # 中期向上但短期向下: 趋势在瓦解 — 按背离幅度惩罚
                ratio = abs(ma5_slope) / max(abs(ma60_slope), 0.1)
                ma5_score = max(0, 40 - ratio * 8)
            else:
                # 中期向下: 无论短期如何都不算趋势
                ma5_score = 15

        # 3) Peak-End DD%: 终点离最高点多远 (提前算, 不依赖MA)
        peak_dd = 1.0 - closes[-1] / closes.max() if closes.max() > 0 else 0
        dd_score = 100.0 - self._sigmoid(peak_dd, 0.15, 20.0)

        # 4) R²: 趋势可靠性 (提前算, 后续可能被MA5背离打折)
        x = np.arange(n)
        log_y = np.log(np.maximum(closes, 0.01))
        _, _, r_value, _, _ = stats.linregress(x, log_y)
        r_value = np.nan_to_num(r_value, nan=0.0)
        r2 = r_value ** 2
        r2_score = max(0, min(100, r2 * 100))

        if n >= 65:
            # 严重背离时, 历史R²不再可靠 (趋势已经裂了)
            if ma60_slope > 0 and ma5_slope < 0 and abs(ma5_slope - ma60_slope) > 80:
                r2_score *= 0.5

        # 5) 合成分类分数
        score = ma60_score * 0.35 + ma5_score * 0.15 + dd_score * 0.25 + r2_score * 0.25
        trend_class = 'trend' if score >= 55 else 'choppy'
        return (trend_class, round(score, 1))

    def _detect_trend_phase(self, strength):
        """趋势生命周期识别：将强度窗口分为前段(前2/3)和后段(后1/3)，比较斜率变化。
        A股现实：加速赶顶是最大的追高风险。
        返回: ('accelerating'|'steady'|'decelerating', 后段斜率, 前段斜率)"""
        n = len(strength)
        if n < 30:
            return ('steady', 0, 0)  # 数据不足, 默认匀速

        split = n * 2 // 3  # 前2/3 vs 后1/3
        early = strength.iloc[:split]
        late = strength.iloc[split:]

        if len(early) < 10 or len(late) < 10:
            return ('steady', 0, 0)

        # 前段斜率
        x1 = np.arange(len(early))
        log_y1 = np.log(np.maximum(early['close'].values, 0.01))
        s1, _, _, _, _ = stats.linregress(x1, log_y1)
        early_slope = s1 * 250 * 100  # 年化%

        # 后段斜率
        x2 = np.arange(len(late))
        log_y2 = np.log(np.maximum(late['close'].values, 0.01))
        s2, _, _, _, _ = stats.linregress(x2, log_y2)
        late_slope = s2 * 250 * 100  # 年化%

        # 阶段判断
        if abs(early_slope) < 0.5:
            return ('steady', late_slope, early_slope)  # 前段基本没趋势

        ratio = late_slope / max(abs(early_slope), 0.1)
        if early_slope > 0:
            # 上升趋势中
            if ratio > 1.5:
                return ('accelerating', late_slope, early_slope)
            elif ratio < 0.5:
                return ('decelerating', late_slope, early_slope)
            else:
                return ('steady', late_slope, early_slope)
        else:
            # 下降趋势
            return ('steady', late_slope, early_slope)

    def trend_consistency(self):
        """P2：前期趋势 vs 近期调整的方向一致性校验
        v4: 使用动态 Y/L 划分上涨段和调整段
        - 前期涨 + 近期回调 → 健康洗盘 (高分)
        - 前期跌 + 近期也跌 → 主跌浪 (0分)
        - 前期涨 + 近期也涨 → 没有洗盘 (低分)"""
        Y_idx = getattr(self, 'Y_idx', None)
        L_idx = getattr(self, 'L_idx', None)
        if Y_idx is not None and L_idx is not None:
            strength = self.df.iloc[L_idx : Y_idx + 1]  # 上涨段
            recent = self.df.iloc[Y_idx + 1:]            # 调整段
        else:
            strength = self.df.iloc[:-15] if len(self.df) > 15 else self.df
            recent = self.df.tail(15)

        if len(strength) < 5 or len(recent) < 3:
            return 50  # 数据不足，中性

        # 前期趋势方向
        pre_x = np.arange(len(strength))
        pre_log = np.log(np.maximum(strength['close'].values, 0.01))
        pre_slope, _, _, _, _ = stats.linregress(pre_x, pre_log)
        pre_trend = np.sign(pre_slope)

        # 近期调整方向
        recent_mean = recent['close'].mean()
        pre_end = strength['close'].iloc[-1]

        if pre_trend > 0 and recent_mean < pre_end * 0.98:
            return 100  # 前期涨，近期回调 → 健康洗盘
        elif pre_trend > 0 and recent_mean >= pre_end:
            return 30   # 前期涨，近期也涨 → 没有洗盘
        elif pre_trend < 0 and recent_mean < pre_end:
            return 0    # 前期跌，近期也跌 → 主跌浪
        else:
            return 50   # 中性

    def _compute_trend_total(self, ss, class_score=100):
        """趋势引擎：6维度 + 趋势门控 + MA5健康度校验。
        答'这波趋势值得追吗' — 奖励趋势强度+回调健康+资金持续"""
        import math
        wo = self.score_washout_quality(strength_score=ss)
        pt = self.score_probe_test()
        mc = self.score_ma_convergence()
        lr = self.score_launch_readiness()
        vph = self.score_volume_price_health()
        ff = self.score_fund_flow()
        vh = self.score_volume_health()

        # 趋势股权重: 强度+回调质量为核心, 试盘信号降权(趋势不靠试盘)
        raw = ss*0.35 + wo*0.25 + vph*0.20 + lr*0.10 + pt*0.05 + mc*0.05

        # 趋势门控: 强度中心降到30
        gate = 1.0 / (1.0 + math.exp(-0.15 * (ss - 30)))

        # 趋势生命周期调整: 加速段微奖励, 减速段降权
        phase = getattr(self, '_trend_phase', 'steady')
        if phase == 'accelerating':
            gate *= 1.05  # 加速段: 顺势持有, 微奖励
        elif phase == 'decelerating':
            gate *= 0.75  # 减速段: 趋势在瓦解, 显著降权

        # MA5 健康度校验: 在上涨段上测 MA5 斜率，判断趋势是否仍健康
        Y_idx = getattr(self, 'Y_idx', None)
        if Y_idx is not None and Y_idx >= 20:
            s_closes = self.df.iloc[:Y_idx + 1]['close'].values  # 上涨段 [L, Y]
            # 用最后20个MA5值做斜率 (而非5点, 避免噪声放大)
            ma5_series = np.array([s_closes[max(0,i-4):i+1].mean() for i in range(len(s_closes))])
            if len(ma5_series) >= 20:
                ma5_seg = ma5_series[-20:]
                x5 = np.arange(20)
                log_ma5_seg = np.log(np.maximum(ma5_seg, 0.01))
                s5, _, _, _, _ = stats.linregress(x5, log_ma5_seg)
                ma5_slope = s5 * 250 * 100
            else:
                ma5_slope = self._ma_slope(s_closes, 5)
            if ma5_slope > 0:
                # MA5向上: 趋势健康, gate加成
                ma5_health = 1.0 + min(0.15, ma5_slope / 500.0)
            else:
                # MA5向下: 趋势在瓦解, gate打折
                ma5_health = max(0.5, 1.0 + ma5_slope / 200.0)
        else:
            ma5_health = 1.0
            ma5_slope = 0

        # 背离折扣: 分类分<75 且 MA5<0 → 趋势质量存疑, gate额外打折
        if class_score < 75 and Y_idx is not None and Y_idx >= 20 and ma5_slope < 0:
            gate *= 0.7  # 分类信心不足 + 短期向下 = 趋势可能破裂

        total = raw * gate * ma5_health
        return total, {'stock_strength': ss, 'washout_quality': wo, 'probe_test': pt,
                       'ma_convergence': mc, 'launch_readiness': lr,
                       'volume_price_health': vph, 'fund_flow': ff, 'volume_health': vh}

    def _choppy_direction_bias(self):
        """震荡突破方向偏斜：不只是'会不会突破'，而是'往哪突破'。
        A股现实：横盘后向上突破和向下破位的概率不同，需要方向判断。
        返回 0-100 分数 (>50偏多, <50偏空)"""
        # v4: 用动态上涨段尾部（Y 前 20 天）
        Y_idx = getattr(self, 'Y_idx', None)
        L_idx = getattr(self, 'L_idx', None)
        if Y_idx is not None and L_idx is not None:
            strength = self.df.iloc[L_idx : Y_idx + 1]
        else:
            strength = self.df.iloc[:len(self.df) - 15]
        if len(strength) < 10:
            return 50  # 数据不足, 中性
        d = strength.tail(20)
        if len(d) < 10:
            return 50

        # 1. 区间位置 (40%): 价格在近期区间的相对位置, 靠近上沿=偏多
        recent_high = d['high'].max()
        recent_low = d['low'].min()
        range_span = recent_high - recent_low
        if range_span > 0 and recent_low > 0:
            price_pos = (d['close'].iloc[-1] - recent_low) / range_span
            # sigmoid 中心 0.55: 价格在区间中上位置时开始加分
            pos_score = self._sigmoid(price_pos, 0.55, 8.0)
        else:
            pos_score = 50

        # 2. 量能方向 (35%): 上涨日总成交额 vs 下跌日总成交额, >1.15=资金在收集
        up_amount = d[d['ret'] > 0]['amount'].sum()
        down_amount = d[d['ret'] < 0]['amount'].sum()
        if down_amount > 0:
            amount_ratio = up_amount / down_amount
            vol_score = self._sigmoid(amount_ratio, 1.15, 5.0)
        else:
            vol_score = 80  # 无下跌日, 偏多

        # 3. 突破前兆 (25%): 最近3天振幅是否在收窄 (三角形收敛末端特征)
        last3 = d.tail(3)
        prev3 = d.iloc[-6:-3] if len(d) >= 6 else d.head(0)
        if len(last3) >= 3 and len(prev3) >= 3:
            recent_amp = last3['amplitude'].mean()
            earlier_amp = prev3['amplitude'].mean()
            if earlier_amp > 0:
                contraction = 1.0 - recent_amp / earlier_amp  # 正值=收窄
                contract_score = self._sigmoid(contraction, 0.05, 30.0)
            else:
                contract_score = 50
        else:
            contract_score = 50

        return pos_score * 0.40 + vol_score * 0.35 + contract_score * 0.25

    def _compute_choppy_total(self, ss):
        """震荡引擎：6维度 + 潜伏门控。
        答'这个横盘值得潜伏吗' — 奖励均线收敛+试盘+缩量, 弱化强度"""
        import math
        wo = self.score_washout_quality(strength_score=ss)
        pt = self.score_probe_test()
        mc = self.score_ma_convergence()
        lr = self.score_launch_readiness()
        vph = self.score_volume_price_health()
        ff = self.score_fund_flow()
        vh = self.score_volume_health()

        # 震荡股权重: 收敛+试盘是核心, 强度降到最低(没趋势是正常的)
        raw = mc*0.25 + pt*0.22 + wo*0.22 + vph*0.13 + lr*0.13 + ss*0.05

        # 震荡门控: 用均线粘合度×试盘信号 代替 strength gate
        # 两者都低 → '没收敛也没人试, 凭什么突破' → gate压制
        mc_gate = 1.0 / (1.0 + math.exp(-0.1 * (mc - 40)))
        pt_gate = 1.0 / (1.0 + math.exp(-0.1 * (pt - 30)))
        gate = mc_gate * 0.55 + pt_gate * 0.45

        # 震荡股不适用趋势一致性 (本来就没趋势)
        total = raw * gate

        # 方向偏斜调整: 向上偏斜奖励, 向下偏斜惩罚 (max ±10%)
        direction_bias = self._choppy_direction_bias()
        if direction_bias > 60:
            total *= 1.0 + (direction_bias - 60) / 400.0   # bias=100 → +10%
        elif direction_bias < 40:
            total *= 1.0 - (40 - direction_bias) / 400.0    # bias=0 → -10%
        return total, {'stock_strength': ss, 'washout_quality': wo, 'probe_test': pt,
                       'ma_convergence': mc, 'launch_readiness': lr,
                       'volume_price_health': vph, 'fund_flow': ff, 'volume_health': vh}

    def compute_total_score(self):
        """总分合成 v4：动态 Y/L 摆动点检测 + 趋势/震荡双引擎。
        Y = 最近局部高点（之后无新高），L = 最近局部低点（之前无新低）
        上涨段 [L, Y] → 强度评分，调整段 [Y, T] → 洗盘/试盘评分"""

        # 0. 动态 Y/L 检测（替代固定 45/15 切分）
        self.Y_idx, self.L_idx = self._find_swing_points()

        # 1. 分类 — 用真正的上涨段 [L, Y]
        strength = self.df.iloc[self.L_idx : self.Y_idx + 1]
        if len(strength) >= 10:
            trend_class, class_score = self._classify_trend(strength)
            # 上涨段太短 → 降级为 choppy
            if len(strength) < 15:
                class_score = min(class_score, 50)
                trend_class = 'choppy'
            # 趋势生命周期检测 (用于趋势引擎 gate 调整)
            if trend_class == 'trend':
                self._trend_phase, self._late_slope, self._early_slope = self._detect_trend_phase(strength)
            else:
                self._trend_phase = 'steady'
        else:
            trend_class, class_score = 'choppy', 0

        # 2. 股票强度 (先算, 强度=0直接出局, 两个引擎都用)
        stock_strength = self.score_stock_strength(force_class=trend_class)
        if stock_strength == 0:
            self.scores = {
                'stock_strength': 0, 'washout_quality': 0, 'probe_test': 0,
                'ma_convergence': 0, 'launch_readiness': 0,
                'volume_price_health': 0, 'fund_flow': 0, 'volume_health': 0,
                'total': 0, 'trend_class': 'choppy', 'trend_class_score': class_score,
            }
            return self.scores

        # 3. 路由到引擎
        if class_score >= 60:
            total, dims = self._compute_trend_total(stock_strength, class_score)
        elif class_score <= 50:
            total, dims = self._compute_choppy_total(stock_strength)
        else:
            # 过渡区: 两个引擎各算一次, 按 class_score 混合
            t_total, t_dims = self._compute_trend_total(
                self.score_stock_strength(force_class='trend'), class_score)
            c_total, c_dims = self._compute_choppy_total(
                self.score_stock_strength(force_class='choppy'))
            blend = class_score / 100.0
            total = t_total * blend + c_total * (1 - blend)
            dims = {}
            for k in t_dims:
                dims[k] = round(t_dims[k] * blend + c_dims.get(k, 0) * (1 - blend), 1)
            dims['stock_strength'] = stock_strength  # 保留原分类的强度

        self.scores = {
            **dims,
            'total': round(total, 1),
            'trend_class': trend_class,
            'trend_class_score': class_score,
        }
        return self.scores

    def get_summary_stats(self):
        d = self.df.tail(15)
        full = self.df
        recent_limit = d['is_limit_up'].sum()
        today_limit = d['is_limit_up'].iloc[-1] == 1
        probe_mask = (
            (full['vol_ratio'] > 1.2) &
            (full['upper_shadow_pct'] > 0.03) &
            (full['is_limit_up'] == 0) &
            (full['ret'] < 0.08)
        )
        probe_count = probe_mask.sum()
        probe_indices = full[probe_mask].index
        days_since_probe = (len(full) - 1 - probe_indices[-1]) if len(probe_indices) > 0 else 99
        return {
            'latest_close': d['close'].iloc[-1],
            'latest_pctChg': d['pctChg'].iloc[-1] if 'pctChg' in d.columns else np.nan,
            'avg_turn': d['turn'].mean() if 'turn' in d.columns else np.nan,
            'avg_amplitude': d['amplitude'].mean() * 100,
            'max_dd_pct': ((d['close'].cummax() - d['close']) / d['close'].cummax()).max() * 100,
            'up_days': int((d['ret'] > 0).sum()),
            'down_days': int((d['ret'] < 0).sum()),
            'avg_vol_ratio': d['vol_ratio'].mean(),
            'retreat_shrink': d[d['ret'] < 0]['vol_ratio'].mean() if (d['ret'] < 0).sum() > 0 else np.nan,
            'is_limit_up_today': today_limit,
            'recent_limit_days': int(recent_limit),
            'probe_count': int(probe_count),
            'days_since_probe': int(days_since_probe),
        }


# ============================================================
# 单日筛选主函数
# ============================================================
def run_for_date(target_date):
    trading_dates = get_lookback_dates(target_date, LOOKBACK_DAYS)
    start_date = trading_dates[0]
    print(f"  回溯区间: {start_date} ~ {target_date} ({len(trading_dates)}天)")

    # 1. 获取合格股票
    eligible = get_eligible_stocks(target_date, start_date)
    print(f"  符合基本条件: {len(eligible)}只")

    # 2. 预筛选
    eligible_codes = eligible['code'].tolist()
    passed_codes, reasons = quick_filter(eligible_codes, start_date, target_date)
    print(f"  预筛选: {len(passed_codes)}/{len(eligible_codes)} 通过 "
          f"(淘汰: 无回调={reasons['no_retreat']} 波动小={reasons['too_stable']} 出货={reasons['distribution']})")

    # 加载沪深300指数日收益（用于股票强度·相对优势子因子）
    index_returns = None
    try:
        idx_conn = sqlite3.connect(DB_PATH)
        idx_df = pd.read_sql_query("""
            SELECT date, pctChg FROM index_daily
            WHERE code='sh.000300' AND date >= ? AND date <= ?
            ORDER BY date
        """, idx_conn, params=(start_date, target_date))
        idx_conn.close()
        if not idx_df.empty:
            idx_df['date_dt'] = pd.to_datetime(idx_df['date'])
            idx_df['idx_ret'] = idx_df['pctChg'].astype(float) / 100.0
            index_returns = idx_df.set_index('date')['idx_ret']
    except Exception as e:
        print(f"  ⚠ 沪深300数据加载失败: {e}, 相对优势将使用默认值")

    # 3. 精选评分
    results = []
    for code in tqdm(passed_codes, desc='  精选评分', leave=False):
        df = load_stock_data(code, start_date, target_date)
        if df is None or len(df) < 10:
            continue
        try:
            scorer = StockScorer(df, index_returns=index_returns)
            scores = scorer.compute_total_score()
            summary = scorer.get_summary_stats()
            results.append({'code': code, **scores, **summary})
        except Exception:
            continue

    results_df = pd.DataFrame(results)
    if len(results_df) == 0:
        print("  ⚠ 无结果!")
        return pd.DataFrame()

    results_df = results_df.sort_values('total', ascending=False).reset_index(drop=True)
    results_df['rank'] = range(1, len(results_df) + 1)
    results_df['target_date'] = target_date

    print(f"  评分完成: {len(results_df)}只, 最高={results_df['total'].max():.1f}, 均值={results_df['total'].mean():.1f}")
    return results_df


def backfill_trend_class():
    """回填已有评分的 trend_class (不重新评分, 仅分类)"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT DISTINCT target_date FROM screening_history
        WHERE trend_class IS NULL
        ORDER BY target_date
    ''')
    dates = [r[0] for r in cur]
    if not dates:
        print('所有日期已有 trend_class, 无需回填')
        conn.close()
        return
    print(f'发现 {len(dates)} 个日期需要回填 trend_class: {dates}')

    # 加载沪深300
    idx_returns = None
    try:
        idx_df = pd.read_sql_query(
            "SELECT date, pctChg FROM index_daily WHERE code='sh.000300' ORDER BY date", conn)
        if not idx_df.empty:
            idx_df['idx_ret'] = idx_df['pctChg'].astype(float) / 100.0
            idx_returns = idx_df.set_index('date')['idx_ret']
    except Exception:
        pass

    for date in dates:
        # Get all stocks for this date that need backfill
        cur2 = conn.execute('''
            SELECT code FROM screening_history
            WHERE target_date = ? AND trend_class IS NULL
        ''', (date,))
        codes = [r[0] for r in cur2]
        print(f'\n{date}: {len(codes)} 只需回填')

        # Get date range
        trading_dates = get_lookback_dates(date, LOOKBACK_DAYS)
        start_date = trading_dates[0]

        updated = 0
        for code in codes:
            try:
                df = load_stock_data(code, start_date, date)
                if df is None or len(df) < 10:
                    continue
                scorer = StockScorer(df, index_returns=idx_returns)
                Y_idx, L_idx = scorer._find_swing_points()
                strength = scorer.df.iloc[L_idx : Y_idx + 1]
                if len(strength) >= 10:
                    tc, tcs = scorer._classify_trend(strength)
                    conn.execute(
                        'UPDATE screening_history SET trend_class=?, trend_class_score=? WHERE target_date=? AND code=?',
                        (tc, tcs, date, code))
                    updated += 1
            except Exception:
                continue

        conn.commit()
        print(f'  {date}: 回填 {updated}/{len(codes)} 只')

    conn.close()
    print('\n回填完成!')


# ============================================================
# 算法变更检测
# ============================================================
def get_algo_hash():
    """计算评分逻辑部分的 SHA256 哈希 (跳过 CLI/流水线代码)。
    以 '# 算法变更检测' 注释为界, 之前的是评分逻辑, 之后的是流水线。"""
    script_path = os.path.abspath(__file__)
    with open(script_path, 'rb') as f:
        content = f.read()
    # 只哈希评分逻辑部分: 从文件头到 '# 算法变更检测' 之前
    text = content.decode('utf-8', errors='replace')
    marker = '# 算法变更检测'
    idx = text.find(marker)
    if idx > 0:
        scoring_part = text[:idx].encode('utf-8')
    else:
        scoring_part = content  # fallback: 整文件哈希
    return hashlib.sha256(scoring_part).hexdigest()[:16]


# 版本化 key: 版本号变化时旧 hash 找不到 → 自动触发重评
def _hash_key():
    return f'algo_{ALGO_VERSION}_hash'


def store_algo_hash(conn, h):
    """将算法哈希存入 DB metadata 表"""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (_hash_key(), h))
    conn.commit()


def get_stored_algo_hash(conn):
    """读取当前版本的存储哈希, 若不存在返回空"""
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key=?", (_hash_key(),))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def check_algo_changed():
    """检查算法是否变更。版本号升级或评分逻辑改动都触发。
    返回 (changed: bool, version: str, hash: str)"""
    current = get_algo_hash()
    conn = sqlite3.connect(DB_PATH)
    stored = get_stored_algo_hash(conn)
    conn.close()
    if stored is None:
        return True, ALGO_VERSION, current
    return (stored != current), ALGO_VERSION, current


# ============================================================
# 批量主流程
# ============================================================
def get_missing_dates(min_stocks=1000):
    """返回 stock_daily 中有足够数据、但 screening_history 中尚未评分的日期"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='screening_history'")
    has_history = cur.fetchone() is not None
    if has_history:
        existing = set(pd.read_sql_query(
            "SELECT DISTINCT target_date FROM screening_history", conn
        )['target_date'].tolist())
    else:
        existing = set()
    # 只取有足够股票覆盖的日期（≥ min_stocks），不做硬编码日期过滤
    all_dates = set(pd.read_sql_query(f"""
        SELECT date FROM (
            SELECT date, COUNT(DISTINCT code) as cnt
            FROM stock_daily
            GROUP BY date
            HAVING cnt >= {min_stocks}
        ) ORDER BY date
    """, conn)['date'].tolist())
    conn.close()
    return sorted(all_dates - existing)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--latest', action='store_true', help='Only process dates not yet in screening_history')
    parser.add_argument('--date', type=str, help='Process a specific date')
    parser.add_argument('--rescore', type=str, help='Re-score specific dates (comma-separated, deletes old rows first)')
    parser.add_argument('--rescore-nan', action='store_true', help='Re-score dates that have NaN in stock_strength or volume_price_health')
    parser.add_argument('--rescore-all', action='store_true', help='Delete ALL screening_history and re-score every date from scratch')
    parser.add_argument('--backfill-class', action='store_true', help='Backfill trend_class for already-scored stocks (no re-score)')
    parser.add_argument('--auto', action='store_true', help='Auto-detect algo change: rescore-all if changed, else latest only')
    parser.add_argument('--check', action='store_true', help='Check if algo has changed (CI pre-check, outputs CHANGED/UNCHANGED)')
    args = parser.parse_args()

    # ── 算法变更检测 (CI 前置检查) ──
    if args.check:
        changed, ver, h = check_algo_changed()
        if changed:
            print(f'CHANGED {ver} {h}')
        else:
            print(f'UNCHANGED {ver} {h}')
        return

    # ── 回填模式：不评分, 仅分类 ──
    if args.backfill_class:
        backfill_trend_class()
        return

    # ── 自动模式：检测算法是否变更 ──
    if args.auto:
        changed, ver, algo_hash = check_algo_changed()
        if changed:
            print(f'[auto] 算法已变更 ({ver} hash={algo_hash}), 触发全量重评')
            args.rescore_all = True
            # 全量重评逻辑沿用下面已有的 rescore_all 分支
        else:
            print(f'[auto] 算法未变更 ({ver} hash={algo_hash}), 仅处理新日期')
            args.latest = True

    # ── 确定待处理日期 ──
    if args.rescore_all:
        conn_temp = sqlite3.connect(DB_PATH)
        cur = conn_temp.execute("SELECT DISTINCT target_date FROM screening_history ORDER BY target_date")
        old_dates = set(r[0] for r in cur)
        # 在删除前获取真正的新日期 (而非所有未评分日期)
        missing_before_delete = set(get_missing_dates())
        conn_temp.execute("DELETE FROM screening_history")
        conn_temp.commit()
        conn_temp.close()
        # 只合并比最新旧日期更新的 (排除远古未评分日期)
        latest_old = max(old_dates) if old_dates else '0000-00-00'
        fresh_dates = {d for d in missing_before_delete if d > latest_old}
        dates = sorted(old_dates | fresh_dates)
        if fresh_dates:
            print(f'全量重评: {len(old_dates)} 个旧日期 + {len(fresh_dates)} 个新日期 = {len(dates)} 个')
        else:
            print(f'全量重评: {len(dates)} 个日期')
    elif args.rescore:
        dates = [d.strip() for d in args.rescore.split(',') if d.strip()]
        rescue_mode = True
    elif args.rescore_nan:
        rescue_mode = True
    elif args.date:
        dates = [args.date]
    elif args.latest:
        dates = get_missing_dates()
        if not dates:
            print('所有日期已处理完毕，无需更新')
            return
        print(f'待处理日期: {dates}')
    else:
        # 默认：从6月8日开始的全部交易日
        dates = [
            '2026-06-08', '2026-06-09', '2026-06-10', '2026-06-11', '2026-06-12',
            '2026-06-15', '2026-06-16', '2026-06-17', '2026-06-18',
            '2026-06-22', '2026-06-23'
        ]

    # ── 连接 DB ──
    conn = sqlite3.connect(DB_PATH)

    # ── 单日期模式：删除旧评分避免主键冲突 ──
    if args.date and not args.rescore and not args.rescore_nan:
        conn.execute("DELETE FROM screening_history WHERE target_date = ?", (args.date,))
        conn.commit()
        print(f'已清理 {args.date} 的旧评分, 准备重评')

    # ── 重评模式：先清理含 NaN 的旧评分 ──
    if args.rescore_nan:
        cur = conn.execute('''
            SELECT DISTINCT target_date FROM screening_history
            WHERE stock_strength IS NULL OR stock_strength != stock_strength
               OR volume_price_health IS NULL OR volume_price_health != volume_price_health
            ORDER BY target_date
        ''')
        dates = [row[0] for row in cur]
        if not dates:
            print('没有发现含 NaN 的评分记录')
            conn.close()
            return
        print(f'发现 {len(dates)} 个含 NaN 的日期: {dates}')
    if args.rescore or args.rescore_nan:
        for d in dates:
            conn.execute("DELETE FROM screening_history WHERE target_date = ?", (d,))
        conn.commit()
        print(f'已清理 {len(dates)} 个日期的旧评分，准备重评')

    # 建表 (不存在才建)
    conn.execute('''CREATE TABLE IF NOT EXISTS screening_history
        (target_date TEXT, code TEXT, rank INTEGER, total REAL,
         washout_quality REAL, probe_test REAL, ma_convergence REAL,
         stock_strength REAL, launch_readiness REAL,
         volume_price_health REAL, fund_flow REAL, volume_health REAL,
         latest_close REAL, latest_pctChg REAL, avg_turn REAL,
         avg_amplitude REAL, max_dd_pct REAL, is_limit_up_today INTEGER,
         recent_limit_days INTEGER, probe_count INTEGER, days_since_probe INTEGER,
         up_days INTEGER, down_days INTEGER, avg_vol_ratio REAL, retreat_shrink REAL,
         trend_class TEXT, trend_class_score REAL,
         PRIMARY KEY (target_date, code))''')
    # 迁移：为已有表添加新列
    for col, col_type in [('stock_strength', 'REAL'), ('volume_price_health', 'REAL'),
                           ('trend_class', 'TEXT'), ('trend_class_score', 'REAL')]:
        try:
            conn.execute(f"ALTER TABLE screening_history ADD COLUMN {col} {col_type}")
            print(f"  ✓ 已新增 {col} 列")
        except sqlite3.OperationalError:
            pass  # 列已存在
    conn.commit()

    all_results = []
    for i, date in enumerate(dates):
        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(dates)}] 处理日期: {date}")
        print(f"{'='*60}")
        try:
            df = run_for_date(date)
            if len(df) > 0:
                all_results.append(df)
                # 批量写入
                df.to_sql('screening_history', conn, if_exists='append', index=False)
                conn.commit()
                print(f"  ✓ 已存储 {len(df)} 条")
        except Exception as e:
            print(f"  ✗ 错误: {e}")
            import traceback
            traceback.print_exc()

    # 评分完成后存储算法哈希 (用于下次 --auto 检测)
    if (args.auto or args.rescore_all or args.latest) and len(all_results) > 0:
        store_algo_hash(conn, get_algo_hash())
        print(f'  ✓ 已更新算法哈希')

    conn.close()

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        print(f"\n{'='*60}")
        print(f"全部完成! 共 {len(dates)} 个日期, {len(combined)} 条记录")
        print(f"日期范围: {combined['target_date'].min()} ~ {combined['target_date'].max()}")
        print(f"日均股票数: {len(combined) / len(dates):.0f}")
    else:
        print("\n未生成任何结果!")

if __name__ == '__main__':
    main()
