# HZTwhipsaw 评分引擎架构说明

> `batch_screen.py` — 全连续、无硬阈值的六维量价评分系统

---

## 1. 整体架构

```
stock_daily 数据
      │
      ▼
┌─────────────────────────────────────┐
│  StockScorer(df, index_returns)     │
│                                     │
│  1. _find_swing_points()            │  ← 动态 Y/L 检测
│  2. _classify_trend(strength)       │  ← 趋势/震荡分类
│  3. score_stock_strength()          │  ← 六维评分
│     score_washout_quality()         │
│     score_probe_test()              │
│     score_ma_convergence()          │
│     score_launch_readiness()        │
│     score_volume_price_health()     │
│  4. compute_total_score()           │  ← 路由合成
└─────────────────────────────────────┘
      │
      ▼
  {total, stock_strength, washout_quality, ...}
```

---

## 2. 数据预处理

### 2.1 数据获取
- `get_eligible_stocks(target, start)`: 从 stock_daily 筛选换手率≥2%、股价≥5元的股票
- `quick_filter(codes, start, end)`: 淘汰振幅<3%、出货日≥3、无缩量回调的股票
- `load_stock_data(code, start, end)`: 加载单只股票60天日线数据

### 2.2 派生字段
StockScorer 初始化时自动计算：
- `ret`: 日收益率 = close.pct_change()
- `vol_ma5`: 5日均量
- `vol_ratio`: 量比 = volume / vol_ma5
- `amplitude`: 振幅 = (high - low) / preclose
- `upper_shadow_pct`: 上影线占比
- `is_limit_up`: 涨停标记 (涨跌幅>9.5%)

---

## 3. 动态 Y/L 摆动点检测

> `_find_swing_points(M=6, L_lookback=45)`

### 3.1 目标
把60天数据自适应分成两段：
- **上涨段 [L, Y]**：从局部低点到局部高点，用于计算股票强度
- **调整段 [Y, T]**：从局部高点到今天，用于计算洗盘质量、试盘信号等

### 3.2 Y 检测（局部高点）
1. 自适应 M：近20日均振幅 >5.5% → M+2（高波动滤噪），<2.2% → M-2（低波动保信号）
2. 从 T-1 向前搜索，找到第一个满足条件的局部高点：
   - **条件1**：在 ±M 窗口内是唯一最高收盘价（右边界不足时允许放宽）
   - **条件2**：之后到 T-1 无更高收盘价
3. 递归降级：M 找不到 → M-2 重试，最小到 2
4. 兜底：用 [0, T-1] 区间最高点；若之后有更高则前推
5. 软约束：调整段至少1天，上涨段至少2天

### 3.3 L 检测（局部低点）
1. 从 Y-1 向前搜索第一个 M-window 局部低点
2. 递归降级同 Y
3. 兜底：用 Y 前 L_lookback(45) 天内最低点
4. 最小上涨段：至少12天，但涨幅≥25%豁免（爆发式行情）
5. **最终验证**：L 必须是 [L, Y] 区间内绝对最低点

### 3.4 关键参数
| 参数 | 默认值 | 说明 |
|------|--------|------|
| M | 6 | 摆动点检测窗口（自适应 ±2） |
| L_lookback | 45 | L 兜底搜索窗口（≈2个月） |
| min_up_len | 12 | 最小上涨段天数（涨幅≥25%豁免） |

---

## 4. 趋势/震荡分类

> `_classify_trend(strength_df)` → (class_label, class_score)

仅使用**上涨段 [L, Y]** 的数据进行分类。

### 4.1 判定维度 (4个子维度, 权重 35/15/25/25)
| 维度 | 权重 | 计算方法 |
|------|------|---------|
| MA60方向 | 35% | 上涨段 MA60 的年化对数线性回归斜率, sigmoid(center=15%, k=8) |
| MA5动量验证 | 15% | MA5 vs MA60 斜率同号加分, 分歧则按幅度惩罚; MA60向下直接给15分 |
| Peak-DD回撤 | 25% | 上涨段从最高点到末尾的回撤, 100 - sigmoid(center=15%, k=20) |
| R²趋势质量 | 25% | 上涨段收盘价对数线性回归的 R²×100; 若MA60向上但MA5向下且分歧>80, R²减半 |

短数据回退 (n < 65): 用全区间 Efficiency Ratio = 净涨跌 / 路径总长度, sigmoid(center=0.12, k=30)

### 4.2 分类规则
- class_score ≥ 55 → **trend**（趋势类）
- class_score < 55 → **choppy**（震荡类）

---

## 5. 六大评分维度

### 5.1 股票强度 — `score_stock_strength(force_class=None)`
**权重**: 趋势35% + 量能25% + 回调25% + 相对优势15%

| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **趋势质量** | 35% | 年化收益率(sigmoid,中心40%) + 最大回撤(bell,中心15%) + R² |
| **量能强度** | 25% | 上涨段阳线量比 >1.15的天数占比 + 平均量比 |
| **回调健康** | 25% | 调整段回调深度(bell) + 角度(logistic) + 缩量程度 |
| **相对优势** | 15% | vs 沪深300超额收益 + 超额夏普 |

### 5.2 洗盘质量 — `score_washout_quality()`
**权重**: 缩量程度40% + 缩量占比35% + 回撤深度25%

| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **缩量程度** | 40% | 调整段最低量比(bell) + 平均量比(sigmoid) |
| **缩量占比** | 35% | 调整段 vol_ratio<0.85 的天数占比 |
| **回撤深度** | 25% | 从Y高点的最大跌幅(sigmoid, 中心12%) |

### 5.3 试盘信号 — `score_probe_test()`
**权重**: 上影线30% + 量能25% + 试盘后走势35% + 频率10%

识别调整段中 vol_ratio>1.2 + upper_shadow_pct>3% 的试盘日。

| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **上影线质量** | 30% | 上影线长度(bell/sigmoid) + 冲高回落幅度 |
| **量能特征** | 25% | 试盘日量比(sigmoid) + 相对前日均量 |
| **下影线质量** | 35% | 最近试盘日下影线占比 bell(center=5%, sigma=3%) — 锤子线支撑信号 |
| **试盘频率** | 10% | 最近30天试盘次数(bell, 峰值2-3次) |

### 5.4 均线粘合 — `score_ma_convergence()`
**权重**: 粘合度55% + 价格位置 + 均线排列 + 收敛加成

| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **均线粘合度** | 55% | MA5/10/20/30 两两偏离度的均值(bell, 中心3%) |
| **价格位置** | 15% | 收盘价相对均线束的位置(sigmoid, 中心0%) |
| **均线排列** | 15% | 短均>长均天数占比 |
| **收敛加成** | 15% | 最近5天粘合度改善幅度 |

### 5.5 启动准备 — `score_launch_readiness()`
**权重**: 质量阳线55% + 均线15% + 稳定性10% + 新鲜度20%

| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **质量阳线** | 55% | 最近放量阳线的量比+涨幅综合评分 |
| **均线支撑** | 15% | 收盘价 vs MA10/MA20 位置 |
| **价格稳定性** | 10% | 最近5日振幅均值(bell) |
| **信号新鲜度** | 20% | 最近质量阳线的距今时间倒数 |

### 5.6 量价健康 — `score_volume_price_health()`
**权重**: 资金流向50% + 量价健康50%（合并两个子评分）

#### 5.6a 资金流向 — `score_fund_flow()`
| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **OBV趋势** | 30% | OBV线性回归斜率(sigmoid) |
| **OBV强度** | 20% | OBV创新高天数占比 |
| **VWAP位置** | 25% | 收盘价 vs VWAP偏离度(sigmoid) |
| **量比偏斜** | 25% | 高量比日的涨跌不对称性(sigmoid) |

#### 5.6b 量价健康 — `score_volume_health()`
| 子维度 | 权重 | 计算方法 |
|--------|------|---------|
| **健康日占比** | 35% | (涨+放量)或(跌+缩量)的天数占比 |
| **出货惩罚** | 15% | 跌+放量天数 ≥3 → 扣分 |
| **洗盘加成** | 30% | 跌+缩量天数占比(bell) |
| **量价同步** | 20% | 涨跌日量比差异(sigmoid) |

---

## 6. 总分合成

> `compute_total_score()` → dict

### 6.1 流程

```
1. _find_swing_points()  →  Y, L
2. _classify_trend(上涨段)  →  trend_class, class_score
3. score_stock_strength(force_class=trend_class)
   └── 强度=0 → 直接出局
4. 路由:
   ├── class_score ≥ 60  →  _compute_trend_total()
   ├── class_score ≤ 50  →  _compute_choppy_total()
   └── 50 < score < 60   →  两个引擎加权混合
5. 返回 {total, 各维度分数, trend_class, trend_class_score}
```

### 6.2 趋势引擎 — `_compute_trend_total()`

**原始权重**: 强度 35% + 洗盘 25% + 量价健康 20% + 启动准备 10% + 试盘 5% + 均线粘合 5%
```
raw = ss×0.35 + wo×0.25 + vph×0.20 + lr×0.10 + pt×0.05 + mc×0.05
```

**三阶段 Gate 调整**:
1. **强度 Gate**: `sigmoid(ss, center=30, k=0.15)` — 强度<30 分时大幅压制总分
2. **MA5 健康检查**: MA5 斜率 > 0 → gate × (1 ~ 1.15), MA5 < 0 → gate × (0.5 ~ 1.0)。如果 class_score < 75 且 MA5 向下，额外 ×0.7（趋势可能已破）
3. **生命周期调整**: 加速期 ×1.05, 减速期 ×0.75

```
total = raw × strength_gate × ma5_health × phase_adj
```

### 6.3 震荡引擎 — `_compute_choppy_total()`

**原始权重**: 均粘 25% + 试盘 22% + 洗盘 22% + 量价健康 13% + 启动准备 13% + 强度 5%
```
raw = mc×0.25 + pt×0.22 + wo×0.22 + vph×0.13 + lr×0.13 + ss×0.05
```

**Gate**: `sigmoid(mc, center=40, k=0.1) × 0.55 + sigmoid(pt, center=30, k=0.1) × 0.45` — 无粘合且无试盘时 gate 压制总分

**方向偏置** (`_choppy_direction_bias`): 价格位置 40% + 量能方向 35% + 振幅收缩 25%, 最多 ±10%

```
total = raw × gate × direction_bias
```

### 6.4 混合过渡
```
blend = class_score / 100
total = trend_total × blend + choppy_total × (1 - blend)
各维度同理混合
```

---

## 7. 趋势生命周期检测

> `_detect_trend_phase(strength_df)` → (phase, late_slope, early_slope)

将上涨段按前 2/3 和后 1/3 的对数线性回归斜率比较：
- `late/early > 1.5` → `'accelerating'`（加速中）
- `late/early < 0.5` → `'decelerating'`（减速，有见顶迹象）
- 其他 → `'steady'`（稳步上行）

趋势引擎中：加速期 gate×1.05, 减速期 gate×0.75。

---

## 8. 工具函数

### 8.1 Sigmoid
```python
_sigmoid(x, center, steepness)
# 输出 [0, 100]
# 100 / (1 + exp(-k * (x - x0)))
```

### 8.2 Bell 曲线
```python
_bell(x, mu, sigma)
# 输出 [0, 100]
# 100 * exp(-0.5 * ((x - mu) / sigma)²)
```

---

## 9. 数据流完整链路

```
stock_daily (SQLite)
    │
    ▼
get_eligible_stocks()  ← 换手≥2%, 股价≥5
    │
    ▼
quick_filter()  ← 振幅≥3%, 无出货, 有缩量回调
    │
    ▼
StockScorer(df)
    │
    ├── _find_swing_points()     → Y_idx, L_idx
    ├── _classify_trend()        → trend_class, class_score
    ├── score_stock_strength()   → 0-100
    ├── score_washout_quality()  → 0-100
    ├── score_probe_test()       → 0-100
    ├── score_ma_convergence()   → 0-100
    ├── score_launch_readiness() → 0-100
    └── score_volume_price_health() → 0-100
            │
            ▼
    compute_total_score()
            │
            ▼
    {total, 各维度, trend_class, ...}
            │
            ▼
    screening_history (SQLite)
            │
            ▼
    generate_static.py → docs/data/*.json → GitHub Pages
```

---

## 10. 版本控制

```python
ALGO_VERSION = "V1"  # 修改评分公式后手动升级 → 自动触发全量重评
```

| 版本 | 变更 |
|------|------|
| V1 | 动态Y/L摆动点检测 + 趋势/震荡双引擎 (2026-07-02) |

哈希范围：从文件头到 `# 算法变更检测` 注释之间的评分逻辑。流水线代码改动不影响版本判定。
