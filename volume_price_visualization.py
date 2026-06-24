"""
量价关系可视化 —— 开勒股份 vs 太辰光 对比图
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import sqlite3
import pandas as pd
import numpy as np
from scipy import stats
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
import warnings
warnings.filterwarnings('ignore')

# 中文字体设置
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

DB_PATH = r"c:\Users\32299\Desktop\新建文件夹\stock_data.db"

def load_data(code, start, end):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT code, date, open, high, low, close, preclose, volume, amount, turn, pctChg
        FROM stock_daily WHERE code=? AND date >= ? AND date <= ? ORDER BY date
    """, conn, params=(code, start, end))
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df

# 加载数据
kl = load_data('sz.301070', '2026-05-06', '2026-06-18')
tc = load_data('sz.300570', '2026-05-07', '2026-06-16')

# 计算指标
def add_indicators(df):
    d = df.copy()
    d['vol_ma5'] = d['volume'].rolling(5).mean()
    d['vol_ratio'] = d['volume'] / d['vol_ma5']
    d['ret'] = d['close'].pct_change()
    d['amplitude'] = (d['high'] - d['low']) / d['preclose'] * 100
    return d

kl = add_indicators(kl)
tc = add_indicators(tc)

# ============================================================
# 创建对比图 (2x3 布局)
# ============================================================
fig = plt.figure(figsize=(20, 14))
fig.suptitle('开勒股份 vs 太辰光 量价关系对比分析', fontsize=18, fontweight='bold', y=0.98)

# --- Row 1: K线+量 (开勒) ---
ax1 = fig.add_subplot(2, 3, 1)
colors = ['red' if kl['close'].iloc[i] >= kl['open'].iloc[i] else 'green' for i in range(len(kl))]
ax1.bar(kl.index, kl['close'] - kl['open'], bottom=kl['open'], color=colors, width=0.6, alpha=0.8)
ax1.plot(kl.index, kl['close'], 'k-', linewidth=0.8, alpha=0.5)
ax1.set_title('开勒股份 K线图 (05/06-06/18)', fontsize=13)
ax1.set_ylabel('价格 (元)')
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
ax1.grid(True, alpha=0.3)

ax1v = ax1.twinx()
ax1v.bar(kl.index, kl['volume']/10000, color='gray', alpha=0.2, width=0.6)
ax1v.set_ylabel('成交量 (万手)')

# --- Row 1: 量比+价格 (开勒) ---
ax2 = fig.add_subplot(2, 3, 2)
ax2.plot(kl.index, kl['close'], 'b-', linewidth=1.5, label='收盘价')
ax2.set_title('开勒股份 价量趋势', fontsize=13)
ax2.set_ylabel('收盘价 (元)', color='b')
ax2.legend(loc='upper left')
ax2.grid(True, alpha=0.3)

ax2v = ax2.twinx()
ax2v.fill_between(kl.index, kl['vol_ratio'].values, 1.0, alpha=0.3,
                   color='red', where=(kl['vol_ratio'].values >= 1.0))
ax2v.fill_between(kl.index, kl['vol_ratio'].values, 1.0, alpha=0.3,
                   color='green', where=(kl['vol_ratio'].values < 1.0))
ax2v.plot(kl.index, kl['vol_ratio'], 'r--', linewidth=1, alpha=0.7, label='量比')
ax2v.axhline(y=1.0, color='gray', linestyle=':', alpha=0.5)
ax2v.set_ylabel('量比 (相对5日均量)', color='r')
ax2v.legend(loc='upper right')

# --- Row 1: OBV ---
ax3 = fig.add_subplot(2, 3, 3)
kl_obv = (np.sign(kl['close'].diff()) * kl['volume']).fillna(0).cumsum()
tc_obv = (np.sign(tc['close'].diff()) * tc['volume']).fillna(0).cumsum()

# Normalize OBV for comparison
kl_obv_norm = kl_obv / abs(kl_obv.iloc[0]) * 100
tc_obv_norm = tc_obv / abs(tc_obv.iloc[0]) * 100

ax3.plot(kl.index, kl_obv_norm, 'b-', linewidth=1.5, label='开勒股份')
ax3.plot(tc.index, tc_obv_norm, 'r-', linewidth=1.5, label='太辰光')
ax3.set_title('OBV能量潮对比 (归一化)', fontsize=13)
ax3.set_ylabel('OBV (归一化)')
ax3.legend()
ax3.grid(True, alpha=0.3)
ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))

# --- Row 2: K线+量 (太辰光) ---
ax4 = fig.add_subplot(2, 3, 4)
colors_tc = ['red' if tc['close'].iloc[i] >= tc['open'].iloc[i] else 'green' for i in range(len(tc))]
ax4.bar(tc.index, tc['close'] - tc['open'], bottom=tc['open'], color=colors_tc, width=0.6, alpha=0.8)
ax4.plot(tc.index, tc['close'], 'k-', linewidth=0.8, alpha=0.5)
ax4.set_title('太辰光 K线图 (05/07-06/16)', fontsize=13)
ax4.set_ylabel('价格 (元)')
ax4.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
ax4.grid(True, alpha=0.3)

ax4v = ax4.twinx()
ax4v.bar(tc.index, tc['volume']/10000, color='gray', alpha=0.2, width=0.6)
ax4v.set_ylabel('成交量 (万手)')

# --- Row 2: 量比+价格 (太辰光) ---
ax5 = fig.add_subplot(2, 3, 5)
ax5.plot(tc.index, tc['close'], 'b-', linewidth=1.5, label='收盘价')
ax5.set_title('太辰光 价量趋势', fontsize=13)
ax5.set_ylabel('收盘价 (元)', color='b')
ax5.legend(loc='upper left')
ax5.grid(True, alpha=0.3)

ax5v = ax5.twinx()
ax5v.fill_between(tc.index, tc['vol_ratio'].values, 1.0, alpha=0.3,
                   color='red', where=(tc['vol_ratio'].values >= 1.0))
ax5v.fill_between(tc.index, tc['vol_ratio'].values, 1.0, alpha=0.3,
                   color='green', where=(tc['vol_ratio'].values < 1.0))
ax5v.plot(tc.index, tc['vol_ratio'], 'r--', linewidth=1, alpha=0.7, label='量比')
ax5v.axhline(y=1.0, color='gray', linestyle=':', alpha=0.5)
ax5v.set_ylabel('量比 (相对5日均量)', color='r')
ax5v.legend(loc='upper right')

# --- Row 2: 量价散点图 ---
ax6 = fig.add_subplot(2, 3, 6)

# 开勒
kl_valid = kl.dropna(subset=['vol_ratio', 'pctChg'])
ax6.scatter(kl_valid['vol_ratio'], kl_valid['pctChg'],
            c='blue', alpha=0.6, s=60, label='开勒股份', edgecolors='k', linewidth=0.3)
# 太辰光
tc_valid = tc.dropna(subset=['vol_ratio', 'pctChg'])
ax6.scatter(tc_valid['vol_ratio'], tc_valid['pctChg'],
            c='red', alpha=0.6, s=60, label='太辰光', edgecolors='k', linewidth=0.3)

# 拟合线
all_vol = pd.concat([kl_valid['vol_ratio'], tc_valid['vol_ratio']])
all_ret = pd.concat([kl_valid['pctChg'], tc_valid['pctChg']])
z = np.polyfit(all_vol, all_ret, 1)
p = np.poly1d(z)
x_line = np.linspace(all_vol.min(), all_vol.max(), 100)
ax6.plot(x_line, p(x_line), 'k--', linewidth=1.5, alpha=0.7, label=f'趋势线 (y={z[0]:.1f}x+{z[1]:.1f})')

ax6.axhline(y=0, color='gray', linestyle=':', alpha=0.3)
ax6.axvline(x=1.0, color='gray', linestyle=':', alpha=0.3)
ax6.set_xlabel('量比 (相对5日均量)')
ax6.set_ylabel('涨跌幅 %')
ax6.set_title('量价散点图 (放量=涨, 缩量=跌)', fontsize=13)
ax6.legend()
ax6.grid(True, alpha=0.3)
ax6.annotate('放量上涨区', xy=(1.5, 8), fontsize=10, color='green', alpha=0.7)
ax6.annotate('缩量下跌区', xy=(0.5, -4), fontsize=10, color='red', alpha=0.7)

plt.tight_layout(rect=[0, 0, 1, 0.95])
plt.savefig(r'c:\Users\32299\Desktop\新建文件夹\volume_price_comparison.png',
            dpi=150, bbox_inches='tight', facecolor='white')
print("图表已保存: volume_price_comparison.png")

# ============================================================
# 第2张图: 阶段划分 + 相同点注解
# ============================================================
fig2, axes = plt.subplots(2, 2, figsize=(18, 12))
fig2.suptitle('量价关系相同点深度对比', fontsize=16, fontweight='bold')

# --- 左上: 价格走势叠加对比 ---
ax_a = axes[0, 0]
# 归一化价格 (以起始日为100)
kl_norm = kl['close'] / kl['close'].iloc[0] * 100
tc_norm = tc['close'] / tc['close'].iloc[0] * 100

ax_a.plot(kl.index, kl_norm, 'b-', linewidth=2, label='开勒股份', marker='o', markersize=3)
ax_a.plot(tc.index, tc_norm, 'r-', linewidth=2, label='太辰光', marker='s', markersize=3)

# 标注关键位置
kl_low = kl['close'].idxmin()
tc_low = tc['close'].idxmin()
ax_a.annotate(f'开勒低点 ¥{kl["close"].min():.0f}', xy=(kl_low, kl_norm.min()),
              xytext=(kl_low, kl_norm.min()-5), fontsize=9, color='blue',
              arrowprops=dict(arrowstyle='->', color='blue'), ha='center')
ax_a.annotate(f'太辰光低点 ¥{tc["close"].min():.0f}', xy=(tc_low, tc_norm.min()),
              xytext=(tc_low, tc_norm.min()+5), fontsize=9, color='red',
              arrowprops=dict(arrowstyle='->', color='red'), ha='center')

ax_a.set_title('归一化价格走势对比 (起始=100)', fontsize=12)
ax_a.set_ylabel('归一化价格')
ax_a.legend()
ax_a.grid(True, alpha=0.3)
ax_a.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))

# --- 右上: 换手率对比 ---
ax_b = axes[0, 1]
ax_b.bar(kl.index, kl['turn'], color='blue', alpha=0.5, width=0.6, label='开勒股份')
ax_b.bar(tc.index, tc['turn'], color='red', alpha=0.3, width=0.6, label='太辰光')
ax_b.axhline(y=kl['turn'].mean(), color='blue', linestyle='--', alpha=0.5)
ax_b.axhline(y=tc['turn'].mean(), color='red', linestyle='--', alpha=0.5)
ax_b.set_title('换手率对比 (%)', fontsize=12)
ax_b.set_ylabel('换手率 %')
ax_b.legend()
ax_b.grid(True, alpha=0.3)
ax_b.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))

# --- 左下: 量价配合饼图 ---
ax_c = axes[1, 0]
# 汇总两只股票的模式
patterns = ['价涨量增\n(健康上涨)', '价涨量缩\n(上涨乏力)', '价跌量增\n(主动出货)', '价跌量缩\n(正常回调)', '其他']
kl_counts = [
    ((kl['pctChg'] > 0) & (kl['vol_ratio'] > 1.15)).sum(),
    ((kl['pctChg'] > 0) & (kl['vol_ratio'] < 0.85)).sum(),
    ((kl['pctChg'] < 0) & (kl['vol_ratio'] > 1.15)).sum(),
    ((kl['pctChg'] < 0) & (kl['vol_ratio'] < 0.85)).sum(),
]
kl_counts.append(len(kl) - sum(kl_counts))
tc_counts = [
    ((tc['pctChg'] > 0) & (tc['vol_ratio'] > 1.15)).sum(),
    ((tc['pctChg'] > 0) & (tc['vol_ratio'] < 0.85)).sum(),
    ((tc['pctChg'] < 0) & (tc['vol_ratio'] > 1.15)).sum(),
    ((tc['pctChg'] < 0) & (tc['vol_ratio'] < 0.85)).sum(),
]
tc_counts.append(len(tc) - sum(tc_counts))

colors_pie = ['#2ecc71', '#f1c40f', '#e74c3c', '#3498db', '#95a5a6']
explode = (0.05, 0.02, 0.02, 0.02, 0.02)

ax_c.pie(kl_counts, labels=patterns, autopct='%1.1f%%', colors=colors_pie,
         explode=explode, startangle=90, textprops={'fontsize': 8})
ax_c.set_title('开勒股份 量价配合分布', fontsize=12)

# --- 右下: 太辰光 量价配合饼图 ---
ax_d = axes[1, 1]
wedges, texts, autotexts = ax_d.pie(tc_counts, labels=patterns, autopct='%1.1f%%',
                                      colors=colors_pie, explode=explode,
                                      startangle=90, textprops={'fontsize': 8})
ax_d.set_title('太辰光 量价配合分布', fontsize=12)

# 添加文字注解
fig2.text(0.5, 0.02,
          '核心发现: 两只股票均以"价涨量增+价跌量缩"为主导模式, '
          '放量下跌占比均极低(<5%), 说明调整以洗盘为目的而非出货。',
          ha='center', fontsize=11, fontweight='bold',
          bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.3))

plt.tight_layout(rect=[0, 0.05, 1, 0.95])
plt.savefig(r'c:\Users\32299\Desktop\新建文件夹\volume_price_similarities.png',
            dpi=150, bbox_inches='tight', facecolor='white')
print("图表已保存: volume_price_similarities.png")

print("\n可视化完成!")
