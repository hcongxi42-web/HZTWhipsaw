"""
全局配置文件 — HZTwhipsaw 量价洗盘评分系统
"""
import os

# ============================================================
# 路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "stock_data.db")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# baostock 数据获取
# ============================================================
START_DATE = '2024-09-18'          # 数据起始日期

# 日K线获取字段（前复权）
DAILY_FIELDS = ('date,code,open,high,low,close,preclose,volume,amount,'
                'adjustflag,turn,tradestatus,pctChg,'
                'peTTM,pbMRQ,psTTM,pcfNcfTTM')
ADJUST_FLAG = '2'                  # '2' = 前复权

# ============================================================
# akshare 补充数据开关（可选, 需要额外安装 akshare）
# ============================================================
AKSHARE_CONCEPT_FETCH = False
AKSHARE_CYQ_FETCH = False
AKSHARE_FLOAT_MV_FETCH = False
