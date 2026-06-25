"""
批量多日选股打分引擎
==================
对多个目标日期分别运行完整筛选流程，结果存入 screening_history 表。
从 stock_screener.py 提取核心逻辑，参数化 TARGET_DATE。
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

DB_PATH = r"c:\Users\32299\Desktop\新建文件夹\stock_data.db"
LOOKBACK_DAYS = 30
MIN_TURN = 2.0
MIN_PRICE = 5.0
MAX_PRICE = 500.0

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
          AND a.avg_close <= {MAX_PRICE}
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

    def score_washout_quality(self, recent_days=15):
        d = self.df.tail(recent_days)
        down_days = d[d['ret'] < 0]
        if len(down_days) < 2:
            return 30
        avg_shrink = down_days['vol_ratio'].mean()
        shrink_score = max(0, min(100, (1.0 - avg_shrink) * 200))
        shrink_down_pct = (down_days['vol_ratio'] < 0.85).sum() / len(down_days) * 100
        cummax = d['close'].cummax()
        max_dd = ((cummax - d['close']) / cummax).max()
        if 0.05 <= max_dd <= 0.25:
            dd_score = 80
        elif max_dd < 0.05:
            dd_score = 40
        else:
            dd_score = max(0, (0.40 - max_dd) * 200)
        return shrink_score * 0.40 + shrink_down_pct * 0.35 + dd_score * 0.25

    def score_probe_test(self, recent_days=15):
        d = self.df.tail(recent_days)
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
        shadow = latest['upper_shadow_pct']
        if 0.03 <= shadow <= 0.07:
            shadow_score = 90
        elif shadow <= 0.12:
            shadow_score = 60
        else:
            shadow_score = 30
        vr = latest['vol_ratio']
        if 1.3 <= vr <= 2.5:
            vol_score = 90
        elif vr <= 3.5:
            vol_score = 65
        else:
            vol_score = 40
        probe_idx = probe_days.index[-1]
        after_probe = d.loc[probe_idx:]
        if len(after_probe) >= 2:
            after_ret = after_probe['close'].iloc[-1] / after_probe['close'].iloc[0] - 1
            after_vol = after_probe['vol_ratio'].iloc[1:].mean() if len(after_probe) > 1 else 1.0
            if -0.03 <= after_ret <= 0.05 and after_vol < 1.0:
                follow_score = 100
            elif after_ret > -0.05:
                follow_score = 70
            else:
                follow_score = 30
        else:
            follow_score = 50
        freq_bonus = min(15, (len(probe_days) - 1) * 8)
        return min(100, shadow_score * 0.30 + vol_score * 0.25 + follow_score * 0.35 + freq_bonus)

    def score_launch_readiness(self, recent_days=5):
        d = self.df.tail(recent_days)
        full = self.df
        recent_limit_days = d['is_limit_up'].sum()
        limit_penalty = recent_limit_days * 30
        if d['is_limit_up'].iloc[-1] == 1:
            limit_penalty += 35
        quality_up = d[(d['ret'] > 0.02) & (d['vol_ratio'] > 1.0) & (d['is_limit_up'] == 0)]
        if len(quality_up) == 0:
            base_score = 10
        else:
            latest = quality_up.iloc[-1]
            ret = latest['ret']
            if 0.02 <= ret <= 0.07:
                ret_score = 75
            elif ret <= 0.10:
                ret_score = 45
            else:
                ret_score = 20
            vr = latest['vol_ratio']
            if 1.0 <= vr <= 2.0:
                vol_score = 70
            elif vr <= 3.0:
                vol_score = 45
            else:
                vol_score = 25
            base_score = ret_score * 0.55 + vol_score * 0.45
        full_ma5 = full['close'].rolling(5).mean()
        full_ma10 = full['close'].rolling(10).mean()
        above_ma5 = full['close'].iloc[-1] > full_ma5.iloc[-1]
        above_ma10 = full['close'].iloc[-1] > full_ma10.iloc[-1]
        if above_ma5 and above_ma10:
            ma_bonus = 8
        elif above_ma5 or above_ma10:
            ma_bonus = 3
        else:
            ma_bonus = -8
        last3 = d.tail(3)
        if len(last3) >= 3:
            amp_narrow = last3['amplitude'].std() < 0.015
            price_flat = abs(last3['close'].iloc[-1] / last3['close'].iloc[0] - 1) < 0.02
            stable_bonus = 10 if (amp_narrow and price_flat) else 0
        else:
            stable_bonus = 0
        staleness_penalty = 0
        if len(quality_up) > 0:
            last_up_idx = quality_up.index[-1]
            days_since_up = len(d) - 1 - (last_up_idx - d.index[0])
            if days_since_up >= 4:
                staleness_penalty = 8
            elif days_since_up >= 2:
                staleness_penalty = 3
        else:
            staleness_penalty = 12
        raw = base_score + ma_bonus + stable_bonus - limit_penalty - staleness_penalty
        return max(0, min(100, raw))

    def score_ma_convergence(self):
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
        if conv_ratio >= 0.98:
            conv_score = 100
        elif conv_ratio >= 0.95:
            conv_score = 85 + (conv_ratio - 0.95) / 0.03 * 15
        elif conv_ratio >= 0.90:
            conv_score = 65 + (conv_ratio - 0.90) / 0.05 * 20
        elif conv_ratio >= 0.85:
            conv_score = 40 + (conv_ratio - 0.85) / 0.05 * 25
        elif conv_ratio >= 0.80:
            conv_score = 20 + (conv_ratio - 0.80) / 0.05 * 20
        else:
            conv_score = max(5, conv_ratio * 25)
        price_dev = abs(close - ma_mean) / ma_mean
        if price_dev <= 0.02:
            pos_bonus = 10
        elif price_dev <= 0.05:
            pos_bonus = 5
        elif price_dev <= 0.08:
            pos_bonus = 2
        else:
            pos_bonus = -5
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
                    tight_bonus = 8
                elif conv_ratio > past_conv:
                    tight_bonus = 4
        return max(0, min(100, conv_score * 0.55 + pos_bonus + align_bonus + tight_bonus))

    def score_fund_flow(self, recent_days=15):
        d = self.df.tail(recent_days)
        obv_slope, _, r2, _, _ = stats.linregress(np.arange(len(d)), d['obv'].values)
        obv_trend = 60 if obv_slope > 0 else 20
        obv_strength = min(100, r2 * 100)
        vwap_premium = (d['close'].iloc[-1] / d['vwap'].iloc[-1] - 1)
        vwap_score = 50 + max(-30, min(30, vwap_premium * 500))
        up_vol = d[d['ret'] > 0]['vol_ratio'].mean()
        down_vol = d[d['ret'] < 0]['vol_ratio'].mean()
        if pd.notna(up_vol) and pd.notna(down_vol) and down_vol > 0:
            bias = up_vol / down_vol
            bias_score = min(100, bias * 60)
        else:
            bias_score = 50
        return obv_trend * 0.30 + obv_strength * 0.20 + vwap_score * 0.25 + bias_score * 0.25

    def score_volume_health(self, recent_days=15):
        d = self.df.tail(recent_days)
        n = len(d)
        healthy_pct = ((d['ret'] > 0) & (d['vol_ratio'] > 1.0)).sum() / n
        dist_pct = ((d['ret'] < 0) & (d['vol_ratio'] > 1.15)).sum() / n
        washout_pct = ((d['ret'] < 0) & (d['vol_ratio'] < 0.85)).sum() / n
        h_score = healthy_pct * 220
        d_penalty = dist_pct * 350
        w_bonus = washout_pct * 70
        sync = (np.sign(d['close'].diff()) == np.sign(d['volume'].diff())).mean()
        sync_score = (sync - 0.50) * 100
        raw = h_score - d_penalty + w_bonus + sync_score * 0.2
        return max(0, min(100, raw))

    def compute_total_score(self):
        scores = {
            'washout_quality': self.score_washout_quality(),
            'probe_test': self.score_probe_test(),
            'launch_readiness': self.score_launch_readiness(),
            'ma_convergence': self.score_ma_convergence(),
            'fund_flow': self.score_fund_flow(),
            'volume_health': self.score_volume_health(),
        }
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

    # 3. 精选评分
    results = []
    for code in tqdm(passed_codes, desc='  精选评分', leave=False):
        df = load_stock_data(code, start_date, target_date)
        if df is None or len(df) < 10:
            continue
        try:
            scorer = StockScorer(df)
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


# ============================================================
# 批量主流程
# ============================================================
def get_missing_dates():
    """返回数据库中存在但 screening_history 中没有的日期"""
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
    all_dates = set(pd.read_sql_query(
        "SELECT DISTINCT date FROM stock_daily WHERE date >= '2026-06-08' ORDER BY date", conn
    )['date'].tolist())
    conn.close()
    return sorted(all_dates - existing)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--latest', action='store_true', help='Only process dates not yet in screening_history')
    parser.add_argument('--date', type=str, help='Process a specific date')
    args = parser.parse_args()

    if args.date:
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

    conn = sqlite3.connect(DB_PATH)

    # 建表 (不存在才建)
    conn.execute('''CREATE TABLE IF NOT EXISTS screening_history
        (target_date TEXT, code TEXT, rank INTEGER, total REAL,
         washout_quality REAL, probe_test REAL, launch_readiness REAL,
         ma_convergence REAL, fund_flow REAL, volume_health REAL,
         latest_close REAL, latest_pctChg REAL, avg_turn REAL,
         avg_amplitude REAL, max_dd_pct REAL, is_limit_up_today INTEGER,
         recent_limit_days INTEGER, probe_count INTEGER, days_since_probe INTEGER,
         up_days INTEGER, down_days INTEGER, avg_vol_ratio REAL, retreat_shrink REAL,
         PRIMARY KEY (target_date, code))''')
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
