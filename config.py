"""
全局配置文件 — CA 主升浪模式识别 + 数据获取
------------------------------------------
所有子模块（数据获取、CA 模型、筛选）的单一配置源。
修改此文件中的参数即可控制整个系统。
"""

import os

# ============================================================
# 1. 路径配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "stock_data.db")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SRC_DIR = os.path.join(BASE_DIR, "src")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# 2. 数据获取配置（data_fetcher.py / db_manager.py）
# ============================================================
START_DATE = '2024-09-18'          # baostock 数据起始日期

# baostock 日K线获取字段（前复权）
DAILY_FIELDS = ('date,code,open,high,low,close,preclose,volume,amount,'
                'adjustflag,turn,tradestatus,pctChg,'
                'peTTM,pbMRQ,psTTM,pcfNcfTTM')
ADJUST_FLAG = '2'                  # '2' = 前复权

# ============================================================
# 3. 数据预处理
# ============================================================
WINSOR_LOW = 0.01                  # 缩尾下分位数
WINSOR_HIGH = 0.99                 # 缩尾上分位数
MIN_STOCKS_PER_DAY = 100           # 每天至少需要的股票数

# ============================================================
# 4. 主升浪定义参数
# ============================================================
RALLY_RETURN_THRESHOLD = 0.20      # 区间涨幅阈值（默认20%）
RALLY_MAX_DAYS = 10                # 最大持续交易日
RALLY_MIN_DAYS = 2                 # 最短持续交易日（排除一日游）

# ============================================================
# 5. 拉升前观察窗口
# ============================================================
PRE_RALLY_DAYS = 10                # 拉升起点前观察的交易日数
PRE_RALLY_MIN_VOLUME = 0           # 拉升前窗口内成交额>0的天数下限

# ============================================================
# 6. 数据过滤
# ============================================================
MIN_LISTING_DAYS = 60              # 最少上市天数
EXCLUDE_ST = True                  # 排除ST/*ST

# ============================================================
# 7. CATT模型参数（v3 CATT）
# ============================================================
TT_EMBEDDING_K = 16                # 嵌入维度（共享潜在空间维度）
TT_BETA_HIDDEN1 = 64               # β-Network 隐藏层1
TT_BETA_HIDDEN2 = 32               # β-Network 隐藏层2
TT_F_HIDDEN1 = 16                  # f-Network 隐藏层1
TT_F_HIDDEN2 = 16                  # f-Network 隐藏层2
TT_DROPOUT1 = 0.2                  # β-Network 第一层 dropout
TT_DROPOUT2 = 0.1                  # β-Network 第二层 / f-Network dropout
TT_TAU_INIT = 0.07                 # 温度参数初始值（可学习）
TT_LR = 0.001                      # 学习率
TT_WEIGHT_DECAY = 1e-4             # L2 正则化
TT_EPOCHS = 300                    # 训练轮次
TT_PATIENCE = 40                   # 早停耐心值
TT_BATCH_SIZE = 256                # 训练批次大小
TT_TRAIN_RATIO = 0.85              # 训练/验证划分比例
TT_GRAD_CLIP = 1.0                 # 梯度裁剪阈值
TT_LAMBDA_REG = 0.3                # y回归辅助损失权重（v3.3多任务学习）
TT_POS_WEIGHT_STRONG = 2.0         # 强主升浪前夜(quality_label=2)样本权重
TT_MAX_PRE_RETURN = {              # 调整窗口最大涨幅限制（按板块，防止标签泄漏）
    'main':    0.05,               # 主板：[-5%, +5%]
    'chinext': 0.10,               # 创业板：[-10%, +10%]
    'star':    0.10,               # 科创板：[-10%, +10%]
}
RANDOM_SEED = 42                   # 随机种子（确保可复现）

# ============================================================
# 7b. 上涨画像 y 的 6 个维度
# ============================================================
Y_DIM_NAMES = [
    'y_max_return',       # 区间最高涨幅
    'y_speed',            # 爆发力 = max_return / days_to_peak
    'y_persistence',      # 持续性 = 1 - max_drawdown_during_rally
    'y_volume_quality',   # 量能配合 = 上涨期均量 / 调整期均量
    'y_continuity',       # 阳线连贯性 = 阳线天数 / 总天数
]
# 注：y_post_peak_decay 经 A/B 测试验证为负贡献（Spearman r: 5D 0.195 vs 6D 0.169），已移除
N_FEATURES_Y = len(Y_DIM_NAMES)   # = 5
N_FEATURES_X = 42                  # 调整期因子维度（42因子/6组）

# ============================================================
# 7c. 量化因子体系（v3 因子工厂）
# ============================================================
# 因子分组定义（详见 src/factor_builder.py）
FACTOR_GROUP_DEFS = {
    'trend':          {'label': '趋势结构',   'n': 7,  'description': '价格动量与均线生态'},
    'volatility':     {'label': '波动率',     'n': 6,  'description': '波动压缩与扩张周期'},
    'liquidity':      {'label': '量能资金',   'n': 7,  'description': '资金参与度与筹码交换'},
    'price_volume':   {'label': '价量关系',   'n': 6,  'description': '价量互验与背离信号'},
    'rel_strength':   {'label': '相对强弱',   'n': 5,  'description': '大盘相对定位'},
    'washout':        {'label': '洗盘调整末期','n': 11, 'description': '调整末期识别（核心）'},
}
# 方向类因子（保留绝对信号，不参与个股标准化）：趋势 + 相对强弱 + 部分价量 + 部分洗盘
# 相对类因子（个股自适应标准化）：波动率 + 量能资金 + 部分价量 + 部分洗盘
# 具体分类见 factor_builder.py 的 DIRECTIONAL_FACTORS / RELATIVE_FACTORS

# ============================================================
# 8. 筛选输出
# ============================================================
TOP_N_CANDIDATES = 50              # 每日输出 Top-N 候选

# ============================================================
# 10. 回测配置（待接入）
# ============================================================
# 无风险利率（日度，1年期国债 ≈1.5%）
RF_DAILY = 0.015 / 252

TOP_N_STOCKS = 10                  # 每周持有数量
REBALANCE_FREQ = 'W'               # 调仓频率

# 交易成本（A 股 2024 年起适用）
STAMP_DUTY   = 0.0005              # 印花税（卖方单边 0.05%）
COMMISSION   = 0.00025             # 佣金（双向 0.025%）
TRANSFER_FEE = 0.00002             # 过户费（双向 0.002%）
ROUND_TRIP_FEE_WEEKLY = STAMP_DUTY + 2 * COMMISSION + 2 * TRANSFER_FEE

BACKTEST_INITIAL_CAP = 1_000_000   # 回测初始资金

# ============================================================
# 11. v3.0 三模式评分配置
# ============================================================

# 洗盘类型判别阈值
PRIOR_RETURN_LOW = 0.15            # 前期60日涨幅 < 此值 → type1 底部横盘
PRIOR_RETURN_HIGH = 0.60           # 前期60日涨幅 > 此值 → 可能不是中继而是顶部
PRIOR_RETURN_TYPE3_MAX = 0.50      # type3 的前期涨幅上限
MA_CONVERGENCE_TYPE1 = 0.93        # type1 均线粘合度阈值
MA_CONVERGENCE_TYPE2 = 0.88        # type2 均线粘合度阈值
PRICE_POS_TYPE2_MIN = 0.45         # type2 价格位置下限（箱体上半部分）
PRICE_POS_TYPE1_MAX = 0.60         # type1 价格位置上限（箱体中下部）
MA20_SLOPE_FLAT = 0.003            # |斜率| < 此值视为走平
MA20_SLOPE_UP = 0.001              # 斜率 > 此值视为向上
DRAWDOWN_TYPE2_MAX = -0.20         # type2 回调不深于-20%
DRAWDOWN_TYPE3_MAX = -0.15         # type3 回调不深于-15%
MA_CROSS_LOOKBACK = 5              # 5/10日线交叉检测回看天数

# 三路模型路径
THREE_CLASSIFIER_PATH = os.path.join(OUTPUT_DIR, "model", "three_classifiers.pkl")

# 三路 GBDT 超参数（可按类型微调）
TYPE1_GBDT_PARAMS = {
    'n_estimators': 150, 'max_depth': 4, 'learning_rate': 0.05,
    'subsample': 0.8, 'min_samples_leaf': 20, 'min_samples_split': 10,
}
TYPE2_GBDT_PARAMS = {
    'n_estimators': 150, 'max_depth': 4, 'learning_rate': 0.05,
    'subsample': 0.8, 'min_samples_leaf': 20, 'min_samples_split': 10,
}
TYPE3_GBDT_PARAMS = {
    'n_estimators': 150, 'max_depth': 5, 'learning_rate': 0.05,
    'subsample': 0.8, 'min_samples_leaf': 15, 'min_samples_split': 8,
}

# 综合得分权重（可调整偏好）
TYPE_WEIGHTS = {
    'type1': 0.333,    # 底部横盘
    'type2': 0.334,    # 中继横盘
    'type3': 0.333,    # 短期回调
}
