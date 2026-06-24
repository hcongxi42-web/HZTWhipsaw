"""
全市场量价选股引擎
==================
基于6大类33个量价指标，扫描全部A股，按健康度排名筛选。

选股逻辑:
  1. 预筛选: 排除ST、排除流动性过差(日均换手<2%)
  2. 指标计算: 对每只股票计算完整的33个量价指标
  3. 多维评分: 量价健康度 + 洗盘信号 + 启动信号 + 资金流向
  4. 排名输出: Top-N候选

目标日期: 2026-06-22 (模拟06-24选股)
回溯窗口: 30个交易日 (含最新日)
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sqlite3
import pandas as pd
import numpy as np
from scipy import stats
from typing import Dict, List, Tuple, Optional
from tqdm import tqdm
import warnings
import traceback as _traceback
warnings.filterwarnings('ignore')

DB_PATH = r"c:\Users\32299\Desktop\新建文件夹\stock_data.db"

# ============================================================
# 0. 配置
# ============================================================
TARGET_DATE = '2026-06-22'   # 目标选股日
LOOKBACK_DAYS = 30            # 回溯交易日数
MIN_TURN = 2.0                # 最低日均换手率(%)
MIN_PRICE = 5.0               # 最低股价(排除低价股)
MAX_PRICE = 500.0             # 最高股价
TOP_N = 30                    # 最终输出候选数

# ============================================================
# 1. 数据准备
# ============================================================
def get_lookback_dates(target, n_days):
    """获取回溯期间的交易日"""
    conn = sqlite3.connect(DB_PATH)
    dates = pd.read_sql_query(
        "SELECT DISTINCT date FROM stock_daily WHERE date <= ? ORDER BY date DESC LIMIT ?",
        conn, params=(target, n_days)
    )['date'].tolist()
    conn.close()
    return sorted(dates)

TRADING_DATES = get_lookback_dates(TARGET_DATE, LOOKBACK_DAYS)
START_DATE = TRADING_DATES[0]
print(f"目标日: {TARGET_DATE} | 回溯区间: {START_DATE} ~ {TARGET_DATE} | 共{len(TRADING_DATES)}个交易日")

def get_eligible_stocks():
    """获取符合基本条件的股票列表（排除ST、上市不足60天等）"""
    conn = sqlite3.connect(DB_PATH)
    # 获取最近一日有数据且满足基本流动性的股票
    df = pd.read_sql_query(f"""
        WITH latest AS (
            SELECT code, close, volume, amount, turn, pctChg
            FROM stock_daily
            WHERE date = '{TARGET_DATE}'
        ),
        avg_data AS (
            SELECT code, AVG(turn) as avg_turn, AVG(volume) as avg_vol,
                   AVG(close) as avg_close, COUNT(*) as n_days
            FROM stock_daily
            WHERE date >= '{START_DATE}' AND date <= '{TARGET_DATE}'
            GROUP BY code
        )
        SELECT l.code, l.close, l.turn, l.pctChg,
               a.avg_turn, a.avg_close, a.n_days
        FROM latest l
        JOIN avg_data a ON l.code = a.code
        WHERE a.avg_turn >= {MIN_TURN}
          AND a.avg_close >= {MIN_PRICE}
          AND a.avg_close <= {MAX_PRICE}
          AND l.close > 0
          AND a.n_days >= {LOOKBACK_DAYS * 0.6}
    """, conn)
    conn.close()
    return df

print("获取符合条件的股票列表...")
eligible = get_eligible_stocks()
print(f"符合基本条件: {len(eligible)}只 (换手≥{MIN_TURN}%, 价格¥{MIN_PRICE}-¥{MAX_PRICE})")

# ============================================================
# 2. 快速预筛选 —— 基础量价过滤
# ============================================================
def quick_filter(codes, start, end):
    """
    快速过滤: 计算简化的关键指标，筛掉明显不合格的
    返回通过预筛选的股票列表
    """
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

    for code, group in tqdm(df.groupby('code'), desc='预筛选', total=total):
        g = group.sort_values('date').copy()
        if len(g) < 10:
            continue

        # 计算简化指标
        g['ret'] = g['close'].pct_change()
        g['vol_ma5'] = g['volume'].rolling(5).mean()
        g['vol_ratio'] = g['volume'] / g['vol_ma5']
        g['amplitude'] = (g['high'] - g['low']) / g['preclose']

        recent = g.tail(15)  # 取最近15天

        # 过滤1: 近期振幅不能太小（没波动的股票pass）
        if recent['amplitude'].mean() < 0.03:
            failed_reasons['too_stable'] += 1
            continue

        # 过滤2: 近5天不能全是放量下跌（明显出货）
        last5 = g.tail(5)
        dist_days = ((last5['ret'] < 0) & (last5['vol_ratio'] > 1.15)).sum()
        if dist_days >= 3:
            failed_reasons['distribution'] += 1
            continue

        # 过滤3: 必须出现过缩量回调（洗盘特征）
        retreat_mask = (recent['ret'] < 0) & (recent['vol_ratio'] < 0.85)
        if retreat_mask.sum() == 0:
            failed_reasons['no_retreat'] += 1
            continue

        passed.append(code)

    print(f"  预筛选结果: {len(passed)}/{total} 通过")
    print(f"  淘汰原因: 无缩量回调={failed_reasons['no_retreat']}, "
          f"波动太小={failed_reasons['too_stable']}, "
          f"疑似出货={failed_reasons['distribution']}")

    return passed

# ============================================================
# 3. 精选指标计算 —— 对通过预筛选的股票计算完整指标
# ============================================================
class StockScorer:
    """
    单只股票的完整量价指标计算与评分

    六维评分体系:
      washout_quality  — 洗盘质量 (缩量回调程度)
      probe_test       — 试盘信号 (放量+上影线, 冲高回落的主力侦察行为)
      launch_readiness — 启动准备度 (非涨停的放量突破、均线位置)
              ma_convergence   — 均线粘合 (MA5/10/20/60密集缠绕, 变盘前兆)
      fund_flow        — 资金流向 (OBV趋势+VWAP+上涨放量偏斜)
      volume_health    — 量价健康度 (四象限占比)

    惩罚项:
      limit_up_penalty — 涨停惩罚 (近N日内涨停即扣分, 已兑现的预期没有价值)
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
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

        # 均线 (用于粘合度计算)
        d['ma5'] = d['close'].rolling(5).mean()
        d['ma10'] = d['close'].rolling(10).mean()
        d['ma20'] = d['close'].rolling(20).mean()
        d['ma60'] = d['close'].rolling(60).mean()

        # 上影线长度 (相对开盘价)
        # 阳线: high - close; 阴线: high - open
        d['upper_shadow_pct'] = (d['high'] - d[['open', 'close']].max(axis=1)) / d['open']

        # 涨停检测 (A股: 主板±10%, 科创/创业±20%, 北交所±30%)
        # 通过代码前缀判断板块
        code = d['code'].iloc[0] if 'code' in d.columns else ''
        if code.startswith('sh.68') or code.startswith('sz.30'):
            limit_pct = 0.198  # 科创板/创业板 20%涨跌幅
        elif code.startswith('bj.'):
            limit_pct = 0.298  # 北交所 30%
        else:
            limit_pct = 0.098  # 主板 10%

        d['is_limit_up'] = (
            (d['ret'] >= limit_pct) |
            ((d['high'] / d['preclose'] - 1) >= limit_pct)  # 盘中触及涨停
        ).astype(int)

        self.df = d
        self.limit_pct = limit_pct

    # --------------------------------------------------------
    # 维度1: 洗盘质量
    # --------------------------------------------------------
    def score_washout_quality(self, recent_days=15):
        """洗盘质量评分 (0-100)"""
        d = self.df.tail(recent_days)

        down_days = d[d['ret'] < 0]
        if len(down_days) < 2:
            return 30

        # 缩量程度: 下跌日量比越低越好
        avg_shrink = down_days['vol_ratio'].mean()
        shrink_score = max(0, min(100, (1.0 - avg_shrink) * 200))

        # 缩量下跌占比
        shrink_down_pct = (down_days['vol_ratio'] < 0.85).sum() / len(down_days) * 100

        # 回调幅度适中(太浅=没洗够, 太深=走坏了)
        cummax = d['close'].cummax()
        max_dd = ((cummax - d['close']) / cummax).max()
        if 0.05 <= max_dd <= 0.25:
            dd_score = 80  # 5%-25%回撤是最佳洗盘区间
        elif max_dd < 0.05:
            dd_score = 40  # 几乎没回调, 洗盘不充分
        else:
            dd_score = max(0, (0.40 - max_dd) * 200)  # 超过25%逐步扣分

        return shrink_score * 0.40 + shrink_down_pct * 0.35 + dd_score * 0.25

    # --------------------------------------------------------
    # 维度2: 试盘信号 (核心新增)
    # --------------------------------------------------------
    def score_probe_test(self, recent_days=15):
        """
        试盘信号评分 (0-100)

        试盘 = 主力用小量资金拉升试探上方抛压, 特征是:
          - 放量 (vol_ratio > 1.2): 有资金在动作
          - 长上影线 (upper_shadow > 3%): 冲高回落, 刻意不封板
          - 非涨停 (ret < limit_pct): 不是真正的拉升, 是试探
          - 收盘在均线上方: 趋势支撑

        与涨停的区别: 涨停=已经兑现, 试盘=还没涨但资金在试探, 潜力更大
        """
        d = self.df.tail(recent_days)

        # 筛选试盘日: 放量 + 长上影 + 非涨停
        probe_mask = (
            (d['vol_ratio'] > 1.2) &
            (d['upper_shadow_pct'] > 0.03) &
            (d['is_limit_up'] == 0) &
            (d['ret'] < 0.08)  # 涨幅不超过8%, 排除准涨停
        )
        probe_days = d[probe_mask]

        if len(probe_days) == 0:
            # 无试盘信号 = 低分 (不是每只股票都有主力试盘)
            return 15

        # 最近一次试盘的评分
        latest = probe_days.iloc[-1]

        # 上影线质量: 3%-7%最佳 (影线太短=试探不充分, 太长=抛压太重)
        shadow = latest['upper_shadow_pct']
        if 0.03 <= shadow <= 0.07:
            shadow_score = 90
        elif shadow <= 0.12:
            shadow_score = 60
        else:
            shadow_score = 30

        # 放量程度: 量比1.3-2.5适中 (太大量=对倒嫌疑)
        vr = latest['vol_ratio']
        if 1.3 <= vr <= 2.5:
            vol_score = 90
        elif vr <= 3.5:
            vol_score = 65
        else:
            vol_score = 40

        # 试盘后的表现
        probe_idx = probe_days.index[-1]
        after_probe = d.loc[probe_idx:]

        # 试盘后是否缩量企稳 (最佳: 试盘后缩量不破位)
        if len(after_probe) >= 2:
            after_ret = after_probe['close'].iloc[-1] / after_probe['close'].iloc[0] - 1
            after_vol = after_probe['vol_ratio'].iloc[1:].mean() if len(after_probe) > 1 else 1.0

            # 试盘后价格不跌 + 量萎缩 = 洗盘成功
            if -0.03 <= after_ret <= 0.05 and after_vol < 1.0:
                follow_score = 100  # 完美: 试盘后缩量横盘
            elif after_ret > -0.05:
                follow_score = 70
            else:
                follow_score = 30  # 试盘后破位, 信号失效
        else:
            follow_score = 50

        # 试盘频次: 多次试盘 = 主力更积极
        freq_bonus = min(15, (len(probe_days) - 1) * 8)

        return min(100, shadow_score * 0.30 + vol_score * 0.25 +
                   follow_score * 0.35 + freq_bonus)

    # --------------------------------------------------------
    # 维度3: 启动准备度 (重写: 惩罚涨停)
    # --------------------------------------------------------
    def score_launch_readiness(self, recent_days=5):
        """
        启动准备度评分 (0-100)

        核心逻辑:
          - 放量阳线但非涨停 → 高分 (有资金但未透支)
          - 涨停当天 → 低分 (已经涨完了, 追高风险大)
          - 站上均线上方 → 加分 (缩小幅度)
          - 缩量横盘企稳 → 加分 (更严格的门坎)

        改进: 缩小加分项幅度, 引入均线下方的惩罚, 让分数自然分布在30-85之间
        """
        d = self.df.tail(recent_days)
        full = self.df

        # --- 涨停惩罚 ---
        recent_limit_days = d['is_limit_up'].sum()
        limit_penalty = recent_limit_days * 30
        if d['is_limit_up'].iloc[-1] == 1:
            limit_penalty += 35

        # --- 非涨停放量阳线加分 ---
        # 降低各档分值, 使最优组合约73分(原82.75)
        quality_up = d[(d['ret'] > 0.02) & (d['vol_ratio'] > 1.0) & (d['is_limit_up'] == 0)]

        if len(quality_up) == 0:
            base_score = 10
        else:
            latest = quality_up.iloc[-1]
            ret = latest['ret']
            if 0.02 <= ret <= 0.07:
                ret_score = 75   # 原85→75
            elif ret <= 0.10:
                ret_score = 45   # 原55→45
            else:
                ret_score = 20   # 原25→20

            vr = latest['vol_ratio']
            if 1.0 <= vr <= 2.0:
                vol_score = 70   # 原80→70
            elif vr <= 3.0:
                vol_score = 45   # 原55→45
            else:
                vol_score = 25   # 原30→25

            base_score = ret_score * 0.55 + vol_score * 0.45  # 最优: 75*0.55+70*0.45 = 72.75

        # --- 均线位置 (缩小幅度) ---
        full_ma5 = full['close'].rolling(5).mean()
        full_ma10 = full['close'].rolling(10).mean()
        above_ma5 = full['close'].iloc[-1] > full_ma5.iloc[-1]
        above_ma10 = full['close'].iloc[-1] > full_ma10.iloc[-1]

        if above_ma5 and above_ma10:
            ma_bonus = 8    # 原15→8
        elif above_ma5 or above_ma10:
            ma_bonus = 3    # 原8→3
        else:
            ma_bonus = -8   # 新增: 均线下方惩罚

        # --- 企稳加分 (更严格) ---
        last3 = d.tail(3)
        if len(last3) >= 3:
            amp_narrow = last3['amplitude'].std() < 0.015    # 原0.02→0.015
            price_flat = abs(last3['close'].iloc[-1] / last3['close'].iloc[0] - 1) < 0.02  # 原0.03→0.02
            stable_bonus = 10 if (amp_narrow and price_flat) else 0
        else:
            stable_bonus = 0

        # --- 阳线时效惩罚: 距上次优质阳线越久越扣分 ---
        staleness_penalty = 0
        if len(quality_up) > 0:
            last_up_idx = quality_up.index[-1]
            days_since_up = len(d) - 1 - (last_up_idx - d.index[0])
            if days_since_up >= 4:
                staleness_penalty = 8    # 4天前的大阳线已经凉了
            elif days_since_up >= 2:
                staleness_penalty = 3    # 稍微有点远
        else:
            staleness_penalty = 12       # 完全没有优质阳线

        raw = base_score + ma_bonus + stable_bonus - limit_penalty - staleness_penalty
        return max(0, min(100, raw))

    # --------------------------------------------------------
    # 维度4: 均线粘合程度
    # --------------------------------------------------------
    def score_ma_convergence(self):
        """
        均线粘合程度评分 (0-100)

        核心逻辑:
          - 多根均线(MA5/10/20/60)密集缠绕 → 高粘合 → 多空成本趋于一致 → 变盘前兆
          - 价格贴近均线束 → 起爆点确认
          - 短均>中均>长均 → 多头排列加分
          - 粘合在收紧中 → 变盘紧迫感
        """
        d = self.df
        close = d['close'].iloc[-1]

        # 收集可用的均线值
        ma_values = {}
        for n in [5, 10, 20, 60]:
            val = d[f'ma{n}'].iloc[-1]
            if pd.notna(val) and val > 0:
                ma_values[n] = val

        if len(ma_values) < 3:
            # 数据不足 (如次新股尚无60日线), 给中性分
            return 40

        mas = list(ma_values.values())
        ma_range = max(mas) - min(mas)
        ma_mean = np.mean(mas)

        # --- 核心指标: 粘合率 = 1 - (均线极差 / 均线均值) ---
        # 0.98+ = 极度粘合, 0.95+ = 紧密粘合, 0.90+ = 中度粘合
        # 0.85+ = 松散粘合, 0.80- = 发散状态
        conv_ratio = 1.0 - (ma_range / ma_mean)

        if conv_ratio >= 0.98:
            conv_score = 100
        elif conv_ratio >= 0.95:
            conv_score = 85 + (conv_ratio - 0.95) / 0.03 * 15  # 85→100
        elif conv_ratio >= 0.90:
            conv_score = 65 + (conv_ratio - 0.90) / 0.05 * 20  # 65→85
        elif conv_ratio >= 0.85:
            conv_score = 40 + (conv_ratio - 0.85) / 0.05 * 25  # 40→65
        elif conv_ratio >= 0.80:
            conv_score = 20 + (conv_ratio - 0.80) / 0.05 * 20  # 20→40
        else:
            conv_score = max(5, conv_ratio * 25)               # 0→20

        # --- 价格与均线束的距离 ---
        # 价格在均线束附近 → 筹码共振点, 最有意义
        price_dev = abs(close - ma_mean) / ma_mean
        if price_dev <= 0.02:
            pos_bonus = 10
        elif price_dev <= 0.05:
            pos_bonus = 5
        elif price_dev <= 0.08:
            pos_bonus = 2
        else:
            pos_bonus = -5   # 价格远离均线束, 粘合意义打折扣

        # --- 均线排列方向 ---
        # 多头排列(短>中>长) = 粘合后向上突破概率更大
        if 5 in ma_values and 10 in ma_values and 20 in ma_values:
            if ma_values[5] > ma_values[10] > ma_values[20]:
                align_bonus = 10   # 标准多头排列
            elif ma_values[5] > ma_values[10]:
                align_bonus = 5    # 短均在上, 部分偏多
            elif ma_values[5] < ma_values[10] < ma_values[20]:
                align_bonus = -5   # 空头排列, 粘合后可能向下
            else:
                align_bonus = 0
        else:
            align_bonus = 0

        # --- 粘合趋势: 是否在进一步收紧 ---
        tight_bonus = 0
        if len(d) >= 6:
            past_mas = []
            for n in [5, 10, 20, 60]:
                pv = d[f'ma{n}'].iloc[-6]
                if pd.notna(pv) and pv > 0:
                    past_mas.append(pv)
            if len(past_mas) >= 3:
                past_conv = 1.0 - (max(past_mas) - min(past_mas)) / np.mean(past_mas)
                if conv_ratio > past_conv + 0.003:
                    tight_bonus = 8   # 正在加速粘合
                elif conv_ratio > past_conv:
                    tight_bonus = 4   # 缓慢粘合中
                # 发散中不加分不扣分

        return max(0, min(100, conv_score * 0.55 + pos_bonus + align_bonus + tight_bonus))

    # --------------------------------------------------------
    # 维度5: 资金流向
    # --------------------------------------------------------
    def score_fund_flow(self, recent_days=15):
        """资金流向评分 (0-100)"""
        d = self.df.tail(recent_days)

        # OBV趋势
        obv_slope, _, r2, _, _ = stats.linregress(
            np.arange(len(d)), d['obv'].values
        )
        obv_trend = 60 if obv_slope > 0 else 20
        obv_strength = min(100, r2 * 100)

        # VWAP位置
        vwap_premium = (d['close'].iloc[-1] / d['vwap'].iloc[-1] - 1)
        vwap_score = 50 + max(-30, min(30, vwap_premium * 500))

        # 上涨日量比 vs 下跌日量比 (资金偏斜)
        up_vol = d[d['ret'] > 0]['vol_ratio'].mean()
        down_vol = d[d['ret'] < 0]['vol_ratio'].mean()
        if pd.notna(up_vol) and pd.notna(down_vol) and down_vol > 0:
            bias = up_vol / down_vol
            bias_score = min(100, bias * 60)
        else:
            bias_score = 50

        return obv_trend * 0.30 + obv_strength * 0.20 + vwap_score * 0.25 + bias_score * 0.25

    # --------------------------------------------------------
    # 维度6: 量价健康度
    # --------------------------------------------------------
    def score_volume_health(self, recent_days=15):
        """
        量价健康度评分 (0-100)

        改进: 降低系数让分数自然分散, 引入同步率中性线(50%以下倒扣)
        预期分布: 大多数股票在35-75之间, 极端好/差在两端
        """
        d = self.df.tail(recent_days)
        n = len(d)

        healthy_pct = ((d['ret'] > 0) & (d['vol_ratio'] > 1.0)).sum() / n
        dist_pct = ((d['ret'] < 0) & (d['vol_ratio'] > 1.15)).sum() / n
        washout_pct = ((d['ret'] < 0) & (d['vol_ratio'] < 0.85)).sum() / n

        # 不加cap, 让分数自然分布; 仅末尾钳制[0,100]
        h_score = healthy_pct * 220                # 45%健康日才满分
        d_penalty = dist_pct * 350                  # 每次放量跌-23分
        w_bonus = washout_pct * 70                   # 每次缩量跌+4.7分

        # 量价同步率: 50%为中性线, 低于50%倒扣
        sync = (np.sign(d['close'].diff()) == np.sign(d['volume'].diff())).mean()
        sync_score = (sync - 0.50) * 100

        raw = h_score - d_penalty + w_bonus + sync_score * 0.2
        return max(0, min(100, raw))

    # --------------------------------------------------------
    # 综合评分
    # --------------------------------------------------------
    def compute_total_score(self):
        """六维加权 + 涨停惩罚"""
        scores = {
            'washout_quality': self.score_washout_quality(),
            'probe_test': self.score_probe_test(),
            'launch_readiness': self.score_launch_readiness(),
            'ma_convergence': self.score_ma_convergence(),
            'fund_flow': self.score_fund_flow(),
            'volume_health': self.score_volume_health(),
        }

        # 权重: 洗盘+试盘共46%, 均线粘合17%, 其余分散
        weights = {
            'washout_quality': 0.23,
            'probe_test': 0.23,
            'ma_convergence': 0.17,
            'launch_readiness': 0.13,
            'fund_flow': 0.12,
            'volume_health': 0.12,
        }

        total = sum(scores[k] * weights[k] for k in weights)
        self.scores = {**scores, 'total': total}
        return self.scores

    def get_summary_stats(self):
        """获取关键统计用于输出"""
        d = self.df.tail(15)
        full = self.df

        # 涨停统计
        recent_limit = d['is_limit_up'].sum()
        today_limit = d['is_limit_up'].iloc[-1] == 1

        # 试盘统计
        probe_mask = (
            (full['vol_ratio'] > 1.2) &
            (full['upper_shadow_pct'] > 0.03) &
            (full['is_limit_up'] == 0) &
            (full['ret'] < 0.08)
        )
        probe_count = probe_mask.sum()

        # 最近一次试盘的日期距离今天的天数
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
# 4. 主流程
# ============================================================
print("\n" + "=" * 80)
print("  阶段1: 预筛选")
print("=" * 80)

eligible_codes = eligible['code'].tolist()
print(f"初始候选: {len(eligible_codes)}只")

passed_codes = quick_filter(eligible_codes, START_DATE, TARGET_DATE)

print(f"\n" + "=" * 80)
print("  阶段2: 精选评分 (计算完整指标)")
print("=" * 80)

results = []
error_counts = {}
for code in tqdm(passed_codes, desc='精选评分'):
    df = load_stock_data(code, START_DATE, TARGET_DATE)
    if df is None or len(df) < 10:
        continue

    try:
        scorer = StockScorer(df)
        scores = scorer.compute_total_score()
        summary = scorer.get_summary_stats()

        results.append({
            'code': code,
            **scores,
            **summary
        })
    except Exception as e:
        err_type = type(e).__name__
        error_counts[err_type] = error_counts.get(err_type, 0) + 1
        if error_counts.get(err_type, 0) <= 3:  # print first 3 of each type
            print(f'  [ERR] {code}: {err_type}: {str(e)[:100]}')
        continue

if error_counts:
    print(f'\n评分错误统计: {error_counts}')

# 排序
def main():
    global results_df, name_map
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('total', ascending=False).reset_index(drop=True)
    results_df['rank'] = range(1, len(results_df) + 1)

    print(f"\n评分完成! 共 {len(results_df)} 只股票参与排名")

    # ============================================================
    # 5. 输出
    # ============================================================
    print("\n" + "=" * 80)
    print(f"  TOP {TOP_N} 精选候选 (2026-06-22)")
    print("=" * 80)

    # 获取股票名称
    conn = sqlite3.connect(DB_PATH)
    name_map = pd.read_sql_query(
        f"SELECT code, code_name FROM stock_basic WHERE code IN ({','.join(['?']*len(results_df))})",
        conn, params=results_df['code'].tolist()
    ).set_index('code')['code_name'].to_dict()
    conn.close()

    top = results_df.head(TOP_N)

    print(f"\n{'排名':<4s} {'代码':<12s} {'名称':<8s} {'总分':>5s} {'洗盘':>5s} {'试盘':>5s} {'均粘':>5s} {'启动':>5s} {'资金':>5s} {'健康':>5s} {'收盘':>8s} {'涨跌%':>7s} {'涨停':>4s} {'试盘次':>5s}")
    print(f"{'─' * 118}")

    for _, row in top.iterrows():
        name = name_map.get(row['code'], '?')[:6]
        limit_flag = '!!' if row.get('is_limit_up_today', False) else ''
        print(f"{int(row['rank']):<4d} {row['code']:<12s} {name:<8s} "
              f"{row['total']:>5.1f} {row['washout_quality']:>5.1f} {row['probe_test']:>5.1f} "
              f"{row['ma_convergence']:>5.1f} "
              f"{row['launch_readiness']:>5.1f} {row['fund_flow']:>5.1f} {row['volume_health']:>5.1f} "
              f"{row['latest_close']:>8.2f} {row['latest_pctChg']:>+6.2f}% {limit_flag:>4s} "
              f"{row.get('probe_count', 0):>5.0f}")

    # 分类展示
    print(f"\n{'=' * 80}")
    print(f"  分类分析")
    print(f"{'=' * 80}")

    # 得分分布
    print(f"\n得分分布:")
    print(f"  Top10均值: {top.head(10)['total'].mean():.1f}")
    print(f"  Top20均值: {top.head(20)['total'].mean():.1f}")
    print(f"  Top30均值: {top['total'].mean():.1f}")
    print(f"  全部均值: {results_df['total'].mean():.1f}")
    print(f"  最高分: {results_df['total'].max():.1f}")
    print(f"  最低分: {results_df['total'].min():.1f}")

    # 高洗盘质量股票
    print(f"\n洗盘质量最高 (Top5):")
    top_washout = results_df.nlargest(5, 'washout_quality')
    for _, row in top_washout.iterrows():
        name = name_map.get(row['code'], '?')[:8]
        print(f"  {row['code']} {name:<10s} 洗盘={row['washout_quality']:.1f} 总={row['total']:.1f}")

    # 高试盘信号
    print(f"\n试盘信号最强 (Top5):")
    top_probe = results_df.nlargest(5, 'probe_test')
    for _, row in top_probe.iterrows():
        name = name_map.get(row['code'], '?')[:8]
        print(f"  {row['code']} {name:<10s} 试盘={row['probe_test']:.1f} 涨停日={row.get('recent_limit_days',0):.0f} 总={row['total']:.1f}")

    # 均线粘合最高
    print(f"\n均线粘合最高 (Top5):")
    top_ma = results_df.nlargest(5, 'ma_convergence')
    for _, row in top_ma.iterrows():
        name = name_map.get(row['code'], '?')[:8]
        print(f"  {row['code']} {name:<10s} 均粘={row['ma_convergence']:.1f} 洗盘={row['washout_quality']:.1f} 试盘={row['probe_test']:.1f} 总={row['total']:.1f}")

    # 非涨停高启动
    print(f"\n启动准备度最高 (非涨停, Top5):")
    top_launch = results_df[results_df['is_limit_up_today'] == False].nlargest(5, 'launch_readiness')
    for _, row in top_launch.iterrows():
        name = name_map.get(row['code'], '?')[:8]
        print(f"  {row['code']} {name:<10s} 启动准备={row['launch_readiness']:.1f} 试盘={row['probe_test']:.1f} 总={row['total']:.1f}")

    # 资金流入最明显
    print(f"\n资金流入最佳 (Top5):")
    top_fund = results_df.nlargest(5, 'fund_flow')
    for _, row in top_fund.iterrows():
        name = name_map.get(row['code'], '?')[:8]
        print(f"  {row['code']} {name:<10s} 资金={row['fund_flow']:.1f} 总={row['total']:.1f}")

    # 导出
    results_df.to_csv(r'c:\Users\32299\Desktop\新建文件夹\screening_results.csv',
                      encoding='utf-8-sig', index=False)
    print(f"\n\n完整结果已导出: screening_results.csv ({len(results_df)}只)")

    # 验证: 开勒和太辰光在候选中的位置
    print(f"\n{'=' * 80}")
    print(f"  验证: 已知标的在候选中的位置")
    print(f"{'=' * 80}")
    for code in ['sz.301070', 'sz.300570']:
        match = results_df[results_df['code'] == code]
        if len(match) > 0:
            name = name_map.get(code, '?')
            rank = match.iloc[0]['rank']
            score = match.iloc[0]['total']
            print(f"  {code} ({name}): 排名 {int(rank)}/{len(results_df)}, 得分 {score:.1f}")
        else:
            print(f"  {code}: 未通过筛选/不在候选池")

main()
