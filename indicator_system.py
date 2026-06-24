"""
量价关系指标体系 —— 6大类 30+指标
====================================
对所有指标逐一计算、输出、对比
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sqlite3
import pandas as pd
import numpy as np
from scipy import stats
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 0. 数据加载
# ============================================================
DB_PATH = r"c:\Users\32299\Desktop\新建文件夹\stock_data.db"

def load_stock(code, start, end):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT date, open, high, low, close, preclose, volume, amount, turn, pctChg
        FROM stock_daily WHERE code=? AND date>=? AND date<=? ORDER BY date
    """, conn, params=(code, start, end))
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df

print("=" * 100)
print("  量价关系指标体系 —— 开勒股份 vs 太辰光")
print("=" * 100)

kl = load_stock('sz.301070', '2026-05-06', '2026-06-18')
tc = load_stock('sz.300570', '2026-05-07', '2026-06-16')
print(f"\n开勒股份: {len(kl)}天 | 太辰光: {len(tc)}天")

# ============================================================
# 1. 指标计算引擎
# ============================================================
@dataclass
class IndicatorDef:
    """指标定义"""
    name: str           # 指标简称
    category: str       # 所属大类
    description: str    # 中文说明
    compute: callable   # 计算函数 (df) -> Series
    summary: callable   # 汇总函数 (series) -> float (区间汇总成一个数)
    higher_is_better: bool = True  # 是否数值越大越好

class VolumePriceIndicators:
    """量价指标体系计算引擎"""

    def __init__(self, df: pd.DataFrame, name: str):
        self.df = df.copy()
        self.name = name
        self._precompute()   # 计算所有公共中间变量
        self._indicators: Dict[str, IndicatorDef] = {}
        self._results: Dict[str, pd.Series] = {}     # 每日值
        self._summaries: Dict[str, float] = {}       # 区间汇总值
        self._build_all()

    def _precompute(self):
        """预计算所有中间变量，后续指标直接使用"""
        d = self.df
        n = len(d)

        # 价格衍生物
        d['ret'] = d['close'].pct_change()
        d['log_ret'] = np.log(d['close'] / d['close'].shift(1))
        d['ret_sign'] = np.sign(d['ret'])
        d['amplitude'] = (d['high'] - d['low']) / d['preclose']
        d['gap'] = d['open'] / d['preclose'] - 1     # 跳空幅度

        # 均线
        d['ma5'] = d['close'].rolling(5).mean()
        d['ma10'] = d['close'].rolling(10).mean()
        d['ma20'] = d['close'].rolling(20).mean()

        # 量衍生物
        d['vol_diff'] = d['volume'].diff()
        d['vol_sign'] = np.sign(d['vol_diff'])
        d['vol_ma5'] = d['volume'].rolling(5).mean()
        d['vol_ma10'] = d['volume'].rolling(10).mean()
        d['vol_ma20'] = d['volume'].rolling(20).mean()
        d['amount_ma5'] = d['amount'].rolling(5).mean()
        d['amount_ma10'] = d['amount'].rolling(10).mean()

        # 换手率衍生物
        d['turn_ma5'] = d['turn'].rolling(5).mean()

        # OBV
        d['obv'] = (d['ret_sign'] * d['volume']).fillna(0).cumsum()

        # 典型价格
        d['typical_price'] = (d['high'] + d['low'] + d['close']) / 3
        d['raw_money_flow'] = d['typical_price'] * d['volume']

        # VWAP
        d['vwap'] = (d['amount'] / d['volume']).fillna(d['close'])

        # 方向一致性序列
        d['price_dir'] = np.sign(d['close'].diff())
        d['vol_dir'] = np.sign(d['volume'].diff())
        d['pv_same_dir'] = (d['price_dir'] == d['vol_dir']).astype(float)

        self.df = d

    def _reg(self, ind: IndicatorDef):
        self._indicators[ind.name] = ind

    # ---------- 汇总函数库 ----------
    @staticmethod
    def _s_last(s): return s.dropna().iloc[-1] if len(s.dropna()) > 0 else np.nan
    @staticmethod
    def _s_mean(s): return s.dropna().mean()
    @staticmethod
    def _s_median(s): return s.dropna().median()
    @staticmethod
    def _s_slope(s):
        s = s.dropna()
        if len(s) < 3: return np.nan
        x = np.arange(len(s))
        slope, _, _, _, _ = stats.linregress(x, s.values)
        return slope
    @staticmethod
    def _s_proportion(s): return s.dropna().mean()  # 适用于0/1序列
    @staticmethod
    def _s_sum(s): return s.dropna().sum()
    @staticmethod
    def _s_std(s): return s.dropna().std()
    @staticmethod
    def _s_max(s): return s.dropna().max()
    @staticmethod
    def _s_min(s): return s.dropna().min()

    # ================================================
    # 构建全部指标
    # ================================================
    def _build_all(self):
        # ──────────── 类别1: 量能状态类 ────────────
        self._reg(IndicatorDef('vol_ratio_5', '量能状态', '相对5日均量的倍数',
            lambda d: d['volume'] / d['vol_ma5'],
            self._s_median, higher_is_better=True))
        self._reg(IndicatorDef('vol_ratio_10', '量能状态', '相对10日均量的倍数',
            lambda d: d['volume'] / d['vol_ma10'],
            self._s_median, higher_is_better=True))
        self._reg(IndicatorDef('vol_ratio_20', '量能状态', '相对20日均量的倍数',
            lambda d: d['volume'] / d['vol_ma20'],
            self._s_median, higher_is_better=True))
        self._reg(IndicatorDef('amount_ratio_5', '量能状态', '成交额量比(相对5日均额)',
            lambda d: d['amount'] / d['amount_ma5'],
            self._s_median, higher_is_better=True))
        self._reg(IndicatorDef('vol_percentile_20', '量能状态', '20日内量的分位数位置(0~1)',
            lambda d: d['volume'].rolling(20, min_periods=5).apply(
                lambda x: stats.percentileofscore(x, x.iloc[-1])/100 if len(x)>0 else np.nan),
            self._s_median, higher_is_better=True))
        self._reg(IndicatorDef('vol_cv_20', '量能状态', '20日量变异系数(越高=量越不稳定)',
            lambda d: d['volume'].rolling(20, min_periods=5).std() / d['volume'].rolling(20, min_periods=5).mean(),
            self._s_mean, higher_is_better=False))

        # ──────────── 类别2: 方向一致性类 ────────────
        self._reg(IndicatorDef('pv_dir_sync_5', '方向一致性', '5日价量同向概率(1=完全同步)',
            lambda d: d['pv_same_dir'].rolling(5, min_periods=3).mean(),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('pv_confirm_score', '方向一致性', '价量确认得分(正=健康, 负=背离)',
            lambda d: d['ret_sign'] * (d['volume'] / d['vol_ma5'] - 1),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('ret_vol_dir_corr_10', '方向一致性', '10日滚动 ret与vol变化的相关性',
            lambda d: d['ret'].rolling(10, min_periods=5).corr(d['volume'].diff()),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('close_vol_corr_10', '方向一致性', '10日滚动 收盘价与成交量相关性',
            lambda d: d['close'].rolling(10, min_periods=5).corr(d['volume']),
            self._s_mean, higher_is_better=True))

        # ──────────── 类别3: 量价配合模式类 ────────────
        # 这四个是二值标记，汇总时统计占比
        self._reg(IndicatorDef('is_healthy_up', '配合模式', '是否价涨量增(健康上涨)',
            lambda d: ((d['ret']>0) & (d['volume']/d['vol_ma5'] > 1.15)).astype(float),
            self._s_proportion, higher_is_better=True))
        self._reg(IndicatorDef('is_weak_up', '配合模式', '是否价涨量缩(上涨乏力)',
            lambda d: ((d['ret']>0) & (d['volume']/d['vol_ma5'] < 0.85)).astype(float),
            self._s_proportion, higher_is_better=False))
        self._reg(IndicatorDef('is_distribution', '配合模式', '是否价跌量增(主动出货)',
            lambda d: ((d['ret']<0) & (d['volume']/d['vol_ma5'] > 1.15)).astype(float),
            self._s_proportion, higher_is_better=False))
        self._reg(IndicatorDef('is_washout', '配合模式', '是否价跌量缩(洗盘回调)',
            lambda d: ((d['ret']<0) & (d['volume']/d['vol_ma5'] < 0.85)).astype(float),
            self._s_proportion, higher_is_better=True))
        self._reg(IndicatorDef('up_day_vol_bias', '配合模式', '上涨日量比中位数',
            lambda d: d['volume'] / d['vol_ma5'],
            lambda s: s.dropna().median() if len(s.dropna()) > 0 else np.nan, higher_is_better=True))

        # ──────────── 类别4: 量价弹性类 ────────────
        self._reg(IndicatorDef('amihud_illiq', '量价弹性', 'Amihud非流动性(越小=流动性越好)',
            lambda d: abs(d['ret']) / (d['amount'] / 1e8),
            self._s_median, higher_is_better=False))
        self._reg(IndicatorDef('effort_vs_result', '量价弹性', '投入量 vs 产出涨跌(高=费力不讨好)',
            lambda d: (d['volume']/d['vol_ma5']) / (abs(d['ret'])*100 + 0.001),
            self._s_median, higher_is_better=False))
        self._reg(IndicatorDef('turn_efficiency', '量价弹性', '单位换手产生的价格变动(%)',
            lambda d: abs(d['ret'])*100 / (d['turn'] + 0.01),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('vol_per_point', '量价弹性', '每1%涨跌消耗的成交量(万手)',
            lambda d: (d['volume']/10000) / (abs(d['ret'])*100 + 0.001),
            self._s_median, higher_is_better=False))
        self._reg(IndicatorDef('amplitude_efficiency', '量价弹性', '振幅效率(振幅/量比)',
            lambda d: d['amplitude']*100 / (d['volume']/d['vol_ma5'] + 0.1),
            self._s_mean, higher_is_better=True))

        # ──────────── 类别5: 资金流向类 ────────────
        self._reg(IndicatorDef('obv_slope_10', '资金流向', 'OBV 10日趋势斜率(正=流入)',
            lambda d: d['obv'].rolling(10, min_periods=5).apply(
                lambda x: stats.linregress(np.arange(len(x)), x.values)[0] if len(x)>2 else np.nan),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('obv_price_r', '资金流向', 'OBV与收盘价的相关系数',
            lambda d: pd.Series([d['obv'].corr(d['close'])] * len(d), index=d.index),
            self._s_last, higher_is_better=True))
        self._reg(IndicatorDef('vwap_premium', '资金流向', '收盘价相对VWAP的溢价(正=资金推高)',
            lambda d: d['close'] / d['vwap'] - 1,
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('cum_money_flow_sign', '资金流向', '累计资金流向趋势',
            lambda d: (d['ret_sign'] * d['amount']).fillna(0).cumsum(),
            self._s_slope, higher_is_better=True))
        self._reg(IndicatorDef('mfi_14', '资金流向', '资金流量指标MFI(14日)',
            lambda d: self._calc_mfi(d),
            self._s_last, higher_is_better=True))

        # ──────────── 类别6: 结构特征类 ────────────
        self._reg(IndicatorDef('price_slope_5', '结构特征', '5日价格趋势斜率',
            lambda d: d['close'].rolling(5, min_periods=3).apply(
                lambda x: stats.linregress(np.arange(len(x)), x.values)[0] if len(x)>2 else np.nan),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('vol_trend_5', '结构特征', '5日量的趋势斜率',
            lambda d: d['volume'].rolling(5, min_periods=3).apply(
                lambda x: stats.linregress(np.arange(len(x)), x.values)[0] if len(x)>2 else np.nan),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('vol_cluster_streak', '结构特征', '量能连续方向天数(放量=正, 缩量=负)',
            lambda d: self._calc_streak(d),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('retreat_vol_shrink', '结构特征', '回调期缩量程度(<0.5=强力洗盘)',
            lambda d: self._calc_retreat_shrink(d),
            self._s_median, higher_is_better=False))
        self._reg(IndicatorDef('launch_power', '结构特征', '启动日爆发力(当日量/前5日均量)',
            lambda d: self._calc_launch_power(d),
            self._s_max, higher_is_better=True))
        self._reg(IndicatorDef('gap_quality', '结构特征', '跳空质量(跳空日量比)',
            lambda d: (abs(d['gap'])>0.02).astype(float) * (d['volume']/d['vol_ma5']),
            self._s_mean, higher_is_better=True))
        self._reg(IndicatorDef('amplitude_vol_ratio', '结构特征', '波动效率(振幅×量比)',
            lambda d: d['amplitude']*100 * (d['volume']/d['vol_ma5']),
            self._s_mean, higher_is_better=True))

        # ──────────── 类别7: 综合评分 ────────────
        self._reg(IndicatorDef('health_score', '综合评分', '量价健康度综合得分(0~100)',
            lambda d: self._calc_health_score(d),
            self._s_mean, higher_is_better=True))

    # ========== 辅助计算函数 ==========
    @staticmethod
    def _calc_mfi(d, period=14):
        tp = d['typical_price']
        mf = tp * d['volume']
        pos_flow = mf.where(tp.diff() > 0, 0).rolling(period).sum()
        neg_flow = mf.where(tp.diff() < 0, 0).rolling(period).sum()
        mfi = 100 - 100 / (1 + pos_flow / (neg_flow + 1e-10))
        return mfi

    @staticmethod
    def _calc_streak(d):
        """计算量能连续方向"""
        streak = []
        current = 0
        for i in range(len(d)):
            vr = d['volume'].iloc[i] / d['vol_ma5'].iloc[i] if not pd.isna(d['vol_ma5'].iloc[i]) else 1.0
            if vr > 1.1:
                current = max(1, current + 1)
            elif vr < 0.9:
                current = min(-1, current - 1)
            else:
                current = 0 if abs(current) <= 1 else np.sign(current) * (abs(current) - 1)
            streak.append(current)
        return pd.Series(streak, index=d.index)

    @staticmethod
    def _calc_retreat_shrink(d):
        """计算每个下跌段的缩量程度"""
        result = pd.Series(np.nan, index=d.index)
        ret = d['ret'].values
        vr = (d['volume'] / d['vol_ma5']).values
        in_retreat = False
        retreat_vrs = []
        retreat_start = None

        for i in range(len(d)):
            if ret[i] < 0 or (in_retreat and ret[i] < 0.005):
                if not in_retreat:
                    in_retreat = True
                    retreat_vrs = []
                if not pd.isna(vr[i]):
                    retreat_vrs.append(vr[i])
            else:
                if in_retreat and retreat_vrs:
                    avg_vr = np.mean(retreat_vrs)
                    for j in range(retreat_start if retreat_start else 0, i):
                        result.iloc[j] = avg_vr
                in_retreat = False
                retreat_vrs = []
        return result

    @staticmethod
    def _calc_launch_power(d):
        """检测放量启动信号"""
        result = pd.Series(np.nan, index=d.index)
        vol_ma5 = d['vol_ma5'].values
        vol = d['volume'].values
        ret = d['ret'].values
        for i in range(5, len(d)):
            if ret[i] > 0.03 and vol[i] > vol_ma5[i] * 1.2:
                result.iloc[i] = vol[i] / vol_ma5[i]
        return result

    @staticmethod
    def _calc_health_score(d):
        """综合量价健康度评分(标准化到0-100)"""
        scores = pd.DataFrame(index=d.index)

        # 1. 方向同步 (25%)
        scores['sync'] = d['pv_same_dir'].rolling(5, min_periods=3).mean()

        # 2. 缺乏出货信号 (25%)
        dist = ((d['ret']<0) & (d['volume']/d['vol_ma5']>1.15)).astype(float).rolling(5,min_periods=3).mean()
        scores['no_dist'] = 1 - dist

        # 3. 缩量洗盘程度 (20%)
        retreat = ((d['ret']<0) & (d['volume']/d['vol_ma5']<0.85)).astype(float).rolling(5,min_periods=3).mean()
        scores['washout'] = retreat

        # 4. 资金趋势 (15%)
        obv_slope = d['obv'].rolling(10, min_periods=5).apply(
            lambda x: stats.linregress(np.arange(len(x)), x.values)[0] if len(x)>2 else 0)
        scores['fund_flow'] = pd.Series(obv_slope, index=d.index).rank(pct=True)

        # 5. 健康上涨占比 (15%)
        healthy = ((d['ret']>0) & (d['volume']/d['vol_ma5']>1.0)).astype(float).rolling(5,min_periods=3).mean()
        scores['healthy'] = healthy

        # 加权合成
        composite = (0.25 * scores['sync'].rank(pct=True) +
                     0.25 * scores['no_dist'].rank(pct=True) +
                     0.20 * scores['washout'].rank(pct=True) +
                     0.15 * scores['fund_flow'] +
                     0.15 * scores['healthy'].rank(pct=True)) * 100
        return composite

    # ================================================
    # 执行计算
    # ================================================
    def compute_all(self):
        """计算全部指标"""
        d = self.df
        for name, ind in self._indicators.items():
            try:
                raw = ind.compute(d)
                if isinstance(raw, pd.Series):
                    self._results[name] = raw
                else:
                    self._results[name] = pd.Series(raw, index=d.index)
                self._summaries[name] = ind.summary(self._results[name].dropna() if len(self._results[name].dropna()) > 0 else pd.Series([np.nan]))
            except Exception as e:
                self._results[name] = pd.Series(np.nan, index=d.index)
                self._summaries[name] = np.nan
        return self

    def get_summary_table(self) -> pd.DataFrame:
        """生成汇总表"""
        rows = []
        for name, ind in self._indicators.items():
            val = self._summaries.get(name, np.nan)
            rows.append({
                '大类': ind.category,
                '指标': name,
                '说明': ind.description,
                '方向': '↑好' if ind.higher_is_better else '↓好',
                '汇总值': round(val, 4) if not np.isnan(val) else 'N/A'
            })
        return pd.DataFrame(rows)

    def get_daily_table(self) -> pd.DataFrame:
        """生成每日指标表"""
        df = pd.DataFrame(self._results)
        df.insert(0, 'close', self.df['close'])
        df.insert(1, 'ret', self.df['ret'])
        df.insert(2, 'volume', self.df['volume'])
        return df


# ============================================================
# 2. 执行计算
# ============================================================
print("\n计算开勒股份全部指标...")
kl_ind = VolumePriceIndicators(kl, "开勒股份").compute_all()

print("计算太辰光全部指标...")
tc_ind = VolumePriceIndicators(tc, "太辰光").compute_all()

# ============================================================
# 3. 生成对比报告
# ============================================================
kl_summary = kl_ind.get_summary_table()
tc_summary = tc_ind.get_summary_table()

# 合并对比
comparison = kl_summary.copy()
comparison.columns = ['大类', '指标', '说明', '方向', '开勒股份']
comparison['太辰光'] = tc_summary['汇总值'].values

print("\n\n" + "=" * 120)
print("  量价关系指标体系 —— 完整对比表")
print("=" * 120)

# 按类别分组打印
for cat in ['量能状态', '方向一致性', '配合模式', '量价弹性', '资金流向', '结构特征', '综合评分']:
    sub = comparison[comparison['大类'] == cat]
    print(f"\n{'─' * 110}")
    print(f"  【{cat}类】")
    print(f"{'─' * 110}")
    print(f"{'指标':<28s} {'说明':<38s} {'方向':>4s} {'开勒股份':>12s} {'太辰光':>12s} {'偏离方向':>8s}")
    print(f"{'─' * 110}")
    for _, row in sub.iterrows():
        try:
            kl_v = float(row['开勒股份'])
            tc_v = float(row['太辰光'])
            diff = tc_v - kl_v
            if abs(kl_v) < 1000:
                diff_str = f"{diff:+.4f}"
            else:
                diff_str = f"{diff:+.0f}"
            # 如果指标"越大越好"且太辰光更大，或者"越小越好"且太辰光更小
            if row['方向'] == '↑好':
                better = 'tc>' if diff > 0 else ('kl>' if diff < 0 else '=')
            else:
                better = 'kl>' if diff > 0 else ('tc>' if diff < 0 else '=')
            print(f"  {row['指标']:<26s} {row['说明']:<36s} {row['方向']:>4s} {kl_v:>12.4f} {tc_v:>12.4f} {diff_str:>8s} {better}")
        except:
            print(f"  {row['指标']:<26s} {row['说明']:<36s} {row['方向']:>4s} {str(row['开勒股份']):>12s} {str(row['太辰光']):>12s}")

# ============================================================
# 4. 单项指标深度对比
# ============================================================
print("\n\n" + "=" * 120)
print("  关键指标深度对比 (逐日)")
print("=" * 120)

# 选择核心指标做逐日对比
CORE_INDICATORS = [
    'vol_ratio_5', 'pv_confirm_score', 'is_healthy_up',
    'is_distribution', 'is_washout', 'amihud_illiq',
    'vwap_premium', 'health_score'
]

def compare_daily(kl_ind, tc_ind, ind_name):
    """对比两只股票的某个指标"""
    ind_def = kl_ind._indicators[ind_name]
    kl_vals = kl_ind._results[ind_name].dropna()
    tc_vals = tc_ind._results[ind_name].dropna()

    print(f"\n--- {ind_name}: {ind_def.description} ---")
    print(f"  开勒股份: mean={kl_vals.mean():.4f}, std={kl_vals.std():.4f}, "
          f"min={kl_vals.min():.4f}, max={kl_vals.max():.4f}")
    print(f"  太辰光:   mean={tc_vals.mean():.4f}, std={tc_vals.std():.4f}, "
          f"min={tc_vals.min():.4f}, max={tc_vals.max():.4f}")

    # t检验
    common_idx = kl_vals.index.intersection(tc_vals.index)
    if len(common_idx) > 5:
        t_stat, p_val = stats.ttest_ind(
            kl_vals.loc[common_idx].dropna(),
            tc_vals.loc[common_idx].dropna()
        )
        sig = '***' if p_val < 0.01 else ('**' if p_val < 0.05 else ('*' if p_val < 0.1 else ''))
        print(f"  t-test: t={t_stat:.3f}, p={p_val:.4f} {sig}")
        if p_val > 0.10:
            print(f"  >>> 结论: 两股在该指标上无显著差异 (p>{0.10:.2f}) <<<")
        elif p_val < 0.05:
            if ind_def.higher_is_better:
                better = '太辰光' if tc_vals.mean() > kl_vals.mean() else '开勒股份'
            else:
                better = '太辰光' if tc_vals.mean() < kl_vals.mean() else '开勒股份'
            print(f"  >>> {better} 在此指标上显著更优 <<<")

for ind_name in CORE_INDICATORS:
    compare_daily(kl_ind, tc_ind, ind_name)

# ============================================================
# 5. 找相同的指标 (差异不显著的指标)
# ============================================================
print("\n\n" + "=" * 120)
print("  两股无显著差异的指标 (量价关系相同点)")
print("=" * 120)

same_indicators = []
for name, ind in kl_ind._indicators.items():
    kl_vals = kl_ind._results[name].dropna()
    tc_vals = tc_ind._results[name].dropna()
    common_idx = kl_vals.index.intersection(tc_vals.index)
    if len(common_idx) < 5:
        continue
    try:
        t_stat, p_val = stats.ttest_ind(
            kl_vals.loc[common_idx].dropna(),
            tc_vals.loc[common_idx].dropna()
        )
        if p_val > 0.10:  # 无显著差异
            same_indicators.append((name, ind, kl_vals.mean(), tc_vals.mean(), p_val))
    except:
        pass

same_indicators.sort(key=lambda x: x[4], reverse=True)

print(f"\n{'指标':<25s} {'说明':<40s} {'开勒均值':>12s} {'太辰光均值':>12s} {'p值':>8s}")
print(f"{'─' * 105}")
for name, ind, kl_m, tc_m, p in same_indicators:
    print(f"  {name:<25s} {ind.description:<40s} {kl_m:>12.4f} {tc_m:>12.4f} {p:>8.4f}")

# ============================================================
# 6. 综合结论
# ============================================================
print("\n\n" + "=" * 120)
print("  指标体系分析结论")
print("=" * 120)

print(f"""
共计算 {len(kl_ind._indicators)} 个指标，其中 {len(same_indicators)} 个在两只股票间无显著差异(p>0.10)。

【量价关系相同维度的指标 (无显著差异)】:
""")
for name, ind, kl_m, tc_m, p in same_indicators[:10]:
    print(f"  - {name}: {ind.description} (开勒={kl_m:.4f}, 太辰光={tc_m:.4f}, p={p:.3f})")

# 导出
kl_ind.get_daily_table().to_csv(
    r'c:\Users\32299\Desktop\新建文件夹\indicators_kl_daily.csv', encoding='utf-8-sig')
tc_ind.get_daily_table().to_csv(
    r'c:\Users\32299\Desktop\新建文件夹\indicators_tc_daily.csv', encoding='utf-8-sig')
comparison.to_csv(
    r'c:\Users\32299\Desktop\新建文件夹\indicators_comparison.csv', encoding='utf-8-sig', index=False)

print(f"\n导出文件:")
print(f"  - indicators_kl_daily.csv   (开勒股份逐日全部指标)")
print(f"  - indicators_tc_daily.csv   (太辰光逐日全部指标)")
print(f"  - indicators_comparison.csv (两股汇总对比)")
print(f"\n分析完成!")
