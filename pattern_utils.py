"""
形态识别共享工具函数
====================
_sigmoid / _bell / _geom_match / _annual_slope / _ma_slope
"""
import numpy as np
from scipy import stats


def _sigmoid(x, center, steepness):
    """Sigmoid 函数 → [0, 100]"""
    return 100.0 / (1.0 + np.exp(-steepness * (x - center)))


def _bell(x, mu, sigma):
    """Bell 曲线 → [0, 100]"""
    return 100.0 * np.exp(-((x - mu) / sigma) ** 2)


def _geom_match(features, weights):
    """几何加权平均匹配度 → [0, 100].

    乘积形式天然惩罚结构性缺失: 任一特征接近0则整体接近0.
    """
    score = 1.0
    for key, w in weights.items():
        v = max(features.get(key, 0.1), 0.1)
        score *= (v / 100.0) ** w
    return score * 100.0


def _annual_slope(closes):
    """对数OLS年化斜率(%)"""
    if len(closes) < 5:
        return 0.0
    x = np.arange(len(closes))
    log_y = np.log(np.maximum(closes, 0.01))
    slope, _, _, _, _ = stats.linregress(x, log_y)
    return slope * 250 * 100


def _ma_slope(closes, window):
    """MA年化对数OLS斜率(%)"""
    n = len(closes)
    if n < window + 5:
        return 0.0
    ma = np.array([closes[max(0, i - window + 1):i + 1].mean() for i in range(n)])
    ma_seg = ma[-window:]
    x = np.arange(len(ma_seg))
    log_ma = np.log(np.maximum(ma_seg, 0.01))
    slope, _, _, _, _ = stats.linregress(x, log_ma)
    return slope * 250 * 100
