"""理反 V1.8.0 — 土工试验统计模块 (soil_stats.py)

按地层编号统计（不按状态拆分），样本合并后更可靠：
  1. 仅按 (地层编号, 时代成因, 岩土名称) 分组
  2. IQR 法一次清离群点（Q1-1.5IQR ~ Q3+1.5IQR）
  3. 若 CV 仍 > 0.30 → 中位数+MAD 双向删到 CV≤0.30
  4. 标贯 N 值按状态一致性过滤（内置默认）
  5. 输出到 Excel 模板
"""

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from config import (classify_lithology, PLASTICITY_ORDER, DENSITY_ORDER,
                    spt_to_plasticity, spt_to_density, il_to_plasticity,
                    load_project_config, parse_domain_condition)


# =============================================================================
# 旧规则备份（硬编码，TOML 不存在时兜底）
# =============================================================================

LEGACY_INDICATORS = {
    'hsl':       ('天然含水量 ω(%)', 'low_good'),
    'gmd':       ('重力密度 γ(kN/m3)', 'high_good'),
    'Gs':        ('土粒比重 Gs', 'high_good'),
    'kxb':       ('天然孔隙比 e', 'low_good'),
    'yx':        ('液限 ωL(%)', 'low_good'),
    'sx':        ('塑限 ωp(%)', 'low_good'),
    'sxzs':      ('塑性指数 IP', 'low_good'),
    'yxzs':      ('液性指数 IL', 'low_good'),
    'alpha':     ('压缩系数 α 0.1-0.2(1/MPa)', 'low_good'),
    'Es':        ('压缩模量 Es 0.1-0.2(MPa)', 'high_good'),
    'phi':       ('内摩擦角 φq(度)', 'high_good'),
    'cohesion':  ('粘聚力 Cq(kPa)', 'high_good'),
    'N':         ('标贯击数 N(击/30cm)', 'high_good'),
    'N_corr':    ('标贯修正击数 N(击/30cm)', 'high_good'),
}

LEGACY_INDICATOR_GROUPS = [
    (['hsl', 'gmd', 'Gs', 'kxb'],          'hsl'),
    (['yx', 'sx', 'sxzs', 'yxzs'],         'yx'),
    (['alpha', 'Es'],                       'alpha'),
    (['phi', 'cohesion'],                   'phi'),
    (['N', 'N_corr'],                       'N'),
]

LEGACY_DOMAIN_RULES = [
    ('软黏性土', 'kxb', lambda v: v < 1.0),
    ('淤泥质土', 'kxb', lambda v: v < 1.0),
    ('淤泥质黏土', 'kxb', lambda v: v < 1.0),
    ('淤泥质粉质黏土', 'kxb', lambda v: v < 1.0),
    ('淤泥', 'kxb', lambda v: v < 1.0),
    ('泥炭', 'kxb', lambda v: v < 1.0),
    ('泥炭质土', 'kxb', lambda v: v < 1.0),
    ('软土', 'kxb', lambda v: v < 1.0),
]

LEGACY_REASONABLE_RANGES = {
    'hsl':    (0,  300),
    'gmd':    (10,  30),
    'Gs':     (2.50, 3.00),
    'kxb':    (0.05, 5.0),
    'yx':     (10,  150),
    'sx':     (5,  100),
    'sxzs':   (0,  100),
    'yxzs':   (-2.0, 5.0),
    'alpha':  (0.01, 5.0),
    'Es':     (1,  100),
    'phi':    (0,  50),
    'cohesion': (0, 500),
    'N':      (0,  200),
    'N_corr': (0,  200),
}

# =============================================================================
def _build_config():
    """重建 土工统计指标配置（INDICATORS/分组/域规则/统计参数）（import 时与配置保存后各调用一次）"""
    global _cfg, _std_cfg, _ti, _tg, _tdr, _cfg_stats, STANDARD_VALUE_DIRECTION
    global INDICATORS, INDICATOR_GROUPS, DOMAIN_RULES, REASONABLE_RANGES
    global CV_THRESHOLD, MAX_ITER, OUTLIER_SIGMA, MIN_STD_SAMPLE

    # 新规则：从 TOML 加载，TOML 不存在则回归旧规则
    # =============================================================================
    _cfg = load_project_config()

    # 0. 标准值方向开关（TOML「试验指标_统计.标准值方向」）
    #    "模板"（默认）：与 铁三物理力学表公式.xlsx 一致，全部指标取 1−ψ
    #    "规范"：GB50021 附录E 方向口径（low_good 取 1+ψ，high_good 取 1−ψ）
    _std_cfg = _cfg.get('试验指标_统计', {})
    STANDARD_VALUE_DIRECTION = str(_std_cfg.get('标准值方向', '模板') or '模板')

    # 1. INDICATORS
    _ti = _cfg.get('试验指标', {})
    if _ti:
        INDICATORS = {k: (v['名称'], v['方向']) for k, v in _ti.items() if v.get('名称')}
    else:
        INDICATORS = dict(LEGACY_INDICATORS)

    # 2. INDICATOR_GROUPS：每组第一个 key 为领头指标
    _tg = _cfg.get('试验指标_分组', {})
    if _tg:
        INDICATOR_GROUPS = [(v, v[0]) for v in _tg.values()]
    else:
        INDICATOR_GROUPS = list(LEGACY_INDICATOR_GROUPS)

    # 3. DOMAIN_RULES（从 TOML 解析 "<1.0" 格式的判断条件）
    # V2.2.4（H4）：统一走 config.parse_domain_condition 严格语义解析——
    # '>=1.0'/'<=1.0' 前缀必须先于 '>'/'<' 判定（原顺序使 float('=1.0') 抛
    # ValueError，模块导入期即崩溃）；解析失败的行跳过（防御式）。
    _DOMAIN_OP_LAMBDA = {
        '<':  lambda v, t: v < t,
        '>':  lambda v, t: v > t,
        '>=': lambda v, t: v >= t,
        '<=': lambda v, t: v <= t,
    }
    _tdr = _cfg.get('试验指标_域规则', [])
    if _tdr:
        DOMAIN_RULES = []
        for r in _tdr:
            name = r.get('岩土', '')
            key = r.get('指标', '')
            cond = r.get('判断', '')
            if name and key and cond:
                parsed = parse_domain_condition(cond)
                if parsed is not None:
                    op, th = parsed
                    DOMAIN_RULES.append((name, key, lambda v, t=th, o=op: _DOMAIN_OP_LAMBDA[o](v, t)))
    else:
        DOMAIN_RULES = list(LEGACY_DOMAIN_RULES)

    # 4. 物理合理性范围
    REASONABLE_RANGES = {}
    if _ti:
        for k, v in _ti.items():
            lo = v.get('最小')
            hi = v.get('最大')
            if lo is not None and hi is not None:
                REASONABLE_RANGES[k] = (float(lo), float(hi))
    if not REASONABLE_RANGES:
        REASONABLE_RANGES = dict(LEGACY_REASONABLE_RANGES)

    # 5. 土工统计参数（CV阈值/迭代次数/σ因子/最小样本数）
    _cfg_stats = _cfg.get('试验指标_统计', {})
    CV_THRESHOLD = float(_cfg_stats.get('CV阈值', 0.30))
    MAX_ITER = int(_cfg_stats.get('最大迭代次数', 50))
    OUTLIER_SIGMA = float(_cfg_stats.get('离群阈值因子', 3.0))
    MIN_STD_SAMPLE = int(_cfg_stats.get('标准值最小样本数', 6))


def reload_from_config():
    """配置保存后重建本模块派生常量（review_api._reload_config_modules 调用）"""
    _build_config()


_build_config()


# 塑性状态对应的 IL 区间（TB 10012 表 4.2.7-3）
PLASTICITY_RANGES = {
    '坚硬': (float('-inf'), 0.0),
    '硬塑': (0.0, 0.5),
    '软塑': (0.5, 1.0),
    '流塑': (1.0, float('inf')),
}


@dataclass
class IndicatorStats:
    """单个指标的一整组统计结果"""
    count: int = 0
    max_val: Optional[float] = None
    min_val: Optional[float] = None
    avg: Optional[float] = None
    std: Optional[float] = None
    cv: Optional[float] = None
    standard_value: Optional[float] = None
    large_avg: Optional[float] = None  # (平均+最大)/2
    small_avg: Optional[float] = None  # (平均+最小)/2
    decimals: int = 0                 # 小数位数（与原始数据同步）


@dataclass
class StratumStats:
    """单个地层的统计结果"""
    layer_label: str          # 地层编号，如 "2-31"
    era_genesis: str          # 时代成因
    name: str                 # 岩土名称
    plasticity: str           # 塑性状态
    sample_count_before: int  # 剔除前样本数
    sample_count_after: int   # 塑性剔除后样本数
    test_sample_count: int    # 土工试验试样数（不含标贯）
    indicators: Dict[str, IndicatorStats] = field(default_factory=dict)


def _is_reasonable_value(key: str, v) -> bool:
    """检查单值是否在合理物理范围内（TOML 驱动，兜底旧规则）

    宽限设计（覆盖超泥炭/高塑性极端土，合规审查 Agent3-S8）：hsl≤300
    （超泥炭 w>300 为极端土）、yxzs(IL)∈[-2,5]（坚硬组 IL 可为负）、
    sxzs(IP)≤100、kxb(e)≤5.0 等边界均为宽限值，结合项目土质确认；
    域规则软土 e<1.0 剔除合理（软土定义 e≥1.0，见本文件 _DOMAIN_RULES）。
    """
    if v is None:
        return True
    try:
        v = float(v)
    except (ValueError, TypeError):
        return False
    lo, hi = REASONABLE_RANGES.get(key, (-1e15, 1e15))
    return lo <= v <= hi


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _median(vals):
    """中位数"""
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _quantile(sorted_vals, k, parts=4):
    """线性插值分位数（exclusive 口径：位置 (n+1)*k/parts，1 基插值）

    V2.2.3 T1：替换旧的 n//4 索引近似（非标准四分位），与 Python 3.8+
    statistics.quantiles(method='exclusive') 四分位定义一致；
    IQR 剔除边界随标准分位微调，剔除结果与旧行为保持接近。
    """
    n = len(sorted_vals)
    if n == 0:
        return None
    # V2.2.4 H6：n<3 的小样本不做插值分位——Q1 直接返回最小值、Q3 返回最大值
    # （端点值），不越界不异常。IQR 路径有 n<4 守卫不使用，此处为防御口径；
    # 与 statistics.quantiles(exclusive) 在 n=2 时的插值行为不同属刻意选择。
    if n < 3:
        return sorted_vals[0] if k <= parts // 2 else sorted_vals[-1]
    pos = (n + 1) * k / parts - 1   # 0 基位置
    if pos <= 0:
        return sorted_vals[0]
    if pos >= n - 1:
        return sorted_vals[-1]
    lo = int(math.floor(pos))
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[lo + 1] * frac


def _remove_outliers_iqr(vals):
    """IQR 法一次清除离群点（双向，不区分有利/不利方向）

    离群范围：< Q1 - 1.5×IQR 或 > Q3 + 1.5×IQR
    返回过滤后的列表。
    """
    n = len(vals)
    if n < 4:
        return vals
    s = sorted(vals)
    q1 = _quantile(s, 1)
    q3 = _quantile(s, 3)
    iqr = q3 - q1
    if iqr == 0:
        return vals
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    return [v for v in vals if lo <= v <= hi]


# V2.2.4 R1：剔除决策 CV 的分母保护阈值。均值趋零（如 IL≈0 坚硬组）时
# |CV|=std/|mean| 无界，阶段2"逐个剔除到 CV≤阈值"永不收敛 → 样本被系统性
# 削到 <6（V2.2.3 T6 回归）。|mean| 低于该阈值时按阈值作分母，使决策 CV 有界；
# 策略与"保留样本"目标一致：仅保留 IQR + 3σ(MAD) 绝对剔除，阶段2 不再逐个删。
CV_MIN_ABS_MEAN = 0.5


def _abs_cv(st) -> Optional[float]:
    """剔除决策用变异系数（分母保护版）：std / max(|mean|, CV_MIN_ABS_MEAN)

    输出口径（写表 CV）仍由 _statistics 钳制：负均值组 CV 写 0（V2.2.2 S1 不变）。
    V2.2.3 T6 改用 std/|mean| 后负均值组 |CV| 无界 → 过度剔除（R1）；
    V2.2.4 R1 分母保护后决策 CV 有界；|mean|<CV_MIN_ABS_MEAN 时阶段2 另有
    直接停止守卫（见 _remove_outliers_by_median / _compute_group_stats）。
    """
    if st is None or st.std is None or st.avg is None:
        return None
    denom = max(abs(st.avg), CV_MIN_ABS_MEAN)
    if denom <= 1e-9:
        return None
    return st.std / denom


def _remove_outliers_by_median(vals, cv_threshold=None, max_iter=None):
    """用中位数 + MAD 双向剔除离群点（不区分有利/不利方向）

    两阶段：
      阶段1 — 3σ 离群点批量剔除（双向同时删）
      阶段2 — 若 CV 仍 > 阈值，逐个剔除离中位数最远的值（双向，不区分有利/不利）
              直到 CV ≤ 阈值或样本 ≤ 2
    """
    if cv_threshold is None: cv_threshold = CV_THRESHOLD
    if max_iter is None: max_iter = MAX_ITER
    vals = [v for v in vals if v is not None]

    # ---- 阶段1：3σ 离群点批量剔除 ----
    for _ in range(max_iter):
        if len(vals) <= 2:
            break
        st = _statistics(vals)
        # T6：剔除决策用 |CV|（负均值组不提前 break，极端离群仍走 MAD 流程）
        c = _abs_cv(st)
        if c is None or c <= cv_threshold:
            break
        med = _median(vals)
        if med is None or med == 0:
            break
        mad = _median([abs(v - med) for v in vals])
        if mad == 0:
            break
        sigma_eq = 1.4826 * mad          # MAD → 正态 σ 等价值
        threshold = OUTLIER_SIGMA * sigma_eq       # ≈ 3σ
        filtered = [v for v in vals if abs(v - med) <= threshold]
        if len(filtered) == len(vals):
            break                        # 本轮无离群点
        vals = filtered

    # ---- 阶段2：CV 仍高时逐个剔除离中位数最远的值（双向）----
    for _ in range(max_iter):
        if len(vals) <= 2:
            break
        st = _statistics(vals)
        # V2.2.4 R1：均值趋零（|mean|<CV_MIN_ABS_MEAN）时 CV 判据不可收敛，
        # 逐个剔除会持续删正常样本 → 直接停止（仅保留 IQR + 3σ(MAD) 绝对剔除）。
        # V2.2.5 R1 残余：负均值组（含 |mean|≥0.5）同样直接停止——V2.2.2 口径下
        # 负均值组 CV 为负恒 ≤ 阈值、阶段2 不剔；改为 |CV| 判据后若仅靠
        # |mean|<CV_MIN_ABS_MEAN 守卫，|mean|≥0.5 的负均值组会被逐样本误删
        # （合成 9→7）。统一以 mean < CV_MIN_ABS_MEAN 停止（覆盖全部负均值），
        # 阶段1 3σ(MAD) 极端离群剔除能力不受影响（与 V2.2.2 行为差分最小）。
        if st.avg is not None and st.avg < CV_MIN_ABS_MEAN:
            break
        # T6：剔除决策用 |CV|（负均值组不提前 break，极端离群仍走 MAD 流程）
        c = _abs_cv(st)
        if c is None or c <= cv_threshold:
            break
        med = _median(vals)
        if med is None:
            break
        # 找离中位数最远的值，移除之（不区分方向）
        worst = max(vals, key=lambda v: abs(v - med))
        vals.remove(worst)

    return vals


def _count_decimals(vals: List[float]) -> int:
    """返回一组原始数值中小数位数最多者（用于与数据精度同步，上限 8 位）

    V2.2.3 T10：上限由 4 提高到 8；先 round(v, 8) 消除浮点噪音
    （如 0.30000000000000004 这类假精度），再按 repr 最短表示统计小数位。
    """
    max_d = 0
    for v in vals:
        if v is None:
            continue
        f = round(float(v), 8)
        s = repr(f)
        if '.' in s and 'e' not in s and 'E' not in s:
            d = len(s.split('.')[1])
            if d > max_d:
                max_d = d
    return min(max_d, 8)


def _psi_deviation(n: int, cv: float) -> float:
    """GB50021 统计修正偏差系数 ψ' = (1.704/√n + 4.678/n²) * Cv（恒为非负）"""
    if n < 2 or cv is None or cv <= 0:
        return 0.0
    return (1.704 / math.sqrt(n) + 4.678 / (n * n)) * cv


def _standard_value(avg: float, n: int, cv: float, direction: str) -> Optional[float]:
    """按方向计算标准值

    STANDARD_VALUE_DIRECTION='模板'（默认，与 铁三物理力学表公式.xlsx 一致）：
        全部指标取 平均值 × (1 − ψ')（模板修正系数行恒为 1-(1.704/√n+4.678/n²)*CV）
    STANDARD_VALUE_DIRECTION='规范'（GB50021 附录E 方向口径）：
        low_good（含水率/孔隙比/液限/塑限/压缩系数等越小越有利）→ 1+ψ'；
        high_good（密度/黏聚力/摩擦角/压缩模量/比重等越大越有利）→ 1−ψ'
    """
    if avg is None or cv is None or n < MIN_STD_SAMPLE:
        return None
    dev = _psi_deviation(n, cv)
    if STANDARD_VALUE_DIRECTION == '规范':
        if direction == 'low_good':
            return avg * (1.0 + dev)
        return avg * (1.0 - dev)
    # '模板'：与设计院模板 铁三物理力学表公式.xlsx 完全一致，恒取 1−ψ
    return avg * (1.0 - dev)


def _statistics(values: List[float]) -> IndicatorStats:
    """计算一组数值的统计指标"""
    stats = IndicatorStats()
    vals = [v for v in values if v is not None]
    if not vals:
        return stats

    n = len(vals)
    stats.count = n
    stats.max_val = max(vals)
    stats.min_val = min(vals)
    stats.avg = sum(vals) / n

    # 样本 ≥6 时才计算标准差、变异系数、中大/中小平均值
    if n >= 6:
        mean = stats.avg
        # GB50021 采用样本标准差（分母 n-1）
        variance = sum((v - mean) ** 2 for v in vals) / (n - 1)
        stats.std = math.sqrt(variance)
        # 均值接近 0 时 CV 无意义（如 IL≈0），不计算避免爆炸
        stats.cv = stats.std / mean if abs(mean) > 1e-9 else None
        # V2.2.2 S1：负均值组（如 IL<0 坚硬组）不输出负变异系数——物理上 δ≥0，
        # clamp 为 0（写表时 0 值输出空白；标准值路径 ψ=0 保持原口径）。
        # V2.2.5：v2 标准值行公式已同步该口径（CV 项 MAX(...,0)），负均值组
        # 标准值 = 平均值（ψ=0），v1/v2 不再分叉（第四轮新可疑项①）。
        if stats.cv is not None and stats.cv < 0:
            stats.cv = 0.0

        # 中大/中小平均值 = (平均 + 最大/最小) / 2
        if stats.max_val is not None:
            stats.large_avg = (stats.avg + stats.max_val) / 2
        if stats.min_val is not None:
            stats.small_avg = (stats.avg + stats.min_val) / 2

    stats.decimals = _count_decimals(vals)
    return stats


def _compute_with_direction(values: List[float], direction: str) -> IndicatorStats:
    """IQR 清离群 → 若 CV 仍 > 0.30 → 中位数+MAD 双向删到 CV≤0.30"""
    vals = [v for v in values if v is not None]
    if not vals:
        return IndicatorStats()

    # 阶段1：IQR 一次清
    vals = _remove_outliers_iqr(vals)
    # 阶段2：CV 还不达标 → 中位数+MAD
    vals = _remove_outliers_by_median(vals)

    stats = _statistics(vals)
    if stats.avg is not None and stats.cv is not None and stats.count >= MIN_STD_SAMPLE:
        stats.standard_value = _standard_value(stats.avg, stats.count, stats.cv, direction)
    return stats


def _compute_group_stats(samples: List[dict], group_keys: List[str],
                         lead_key: str) -> Dict[str, IndicatorStats]:
    """同组指标联动剔除：先 IQR 一次清离群 → 中位数+MAD 双向删到 CV≤0.30"""
    valid = []
    for s in samples:
        if any(s.get(k) is not None for k in group_keys):
            valid.append({k: s.get(k) for k in group_keys})
    if not valid:
        return {k: IndicatorStats() for k in group_keys}

    # ---- 阶段0：IQR 一次清（每个指标独立找离群，整条剔除联动）----
    for key in group_keys:
        vals = [s[key] for s in valid if s[key] is not None]
        if len(vals) < 4:
            continue
        s_vals = sorted(vals)
        q1 = _quantile(s_vals, 1)
        q3 = _quantile(s_vals, 3)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        valid = [s for s in valid
                 if s.get(key) is None or (lo <= s[key] <= hi)]

    # ---- 阶段1：3σ 离群样本批量剔除 ----
    for _ in range(MAX_ITER):
        if len(valid) <= 2:
            break
        max_cv = 0.0
        worst_key = None
        for key in group_keys:
            vals = [s[key] for s in valid if s[key] is not None]
            if not vals:
                continue
            st = _statistics(vals)
            # T6：剔除决策用 |CV|（负均值组不提前 break，极端离群仍走 MAD 流程）
            c = _abs_cv(st)
            if c is not None and c > max_cv:
                max_cv = c
                worst_key = key
        if max_cv <= CV_THRESHOLD or worst_key is None:
            break

        key_vals = [s[worst_key] for s in valid if s[worst_key] is not None]
        med = _median(key_vals)
        if med is None or med == 0:
            break
        mad = _median([abs(v - med) for v in key_vals])
        if mad == 0:
            break
        sigma_eq = 1.4826 * mad
        threshold = OUTLIER_SIGMA * sigma_eq       # ≈ 3σ

        outlier_idxs = set()
        for i, s in enumerate(valid):
            v = s.get(worst_key)
            if v is not None and abs(v - med) > threshold:
                outlier_idxs.add(i)
        if not outlier_idxs:
            break                        # 本轮无离群点
        valid = [s for i, s in enumerate(valid) if i not in outlier_idxs]

    # ---- 阶段2：CV 仍高时逐个剔除离中位数最远的样本（双向）----
    for _ in range(MAX_ITER):
        if len(valid) <= 2:
            break
        max_cv = 0.0
        worst_key = None
        worst_st = None
        for key in group_keys:
            vals = [s[key] for s in valid if s[key] is not None]
            if not vals:
                continue
            st = _statistics(vals)
            # T6：剔除决策用 |CV|（负均值组不提前 break，极端离群仍走 MAD 流程）
            c = _abs_cv(st)
            if c is not None and c > max_cv:
                max_cv = c
                worst_key = key
                worst_st = st
        if max_cv <= CV_THRESHOLD or worst_key is None:
            break
        # V2.2.4 R1：最差指标均值趋零（|mean|<CV_MIN_ABS_MEAN）时 CV 判据不可收敛，
        # 逐个剔除会持续删正常样本 → 直接停止（保留样本目标）。
        # V2.2.5 R1 残余：负均值组（含 |mean|≥0.5）同口径停止——V2.2.2 负 CV 恒
        # ≤ 阈值不剔除；与 _remove_outliers_by_median 阶段2 守卫一致。
        if worst_st is not None and worst_st.avg is not None and worst_st.avg < CV_MIN_ABS_MEAN:
            break

        key_vals = [s[worst_key] for s in valid if s[worst_key] is not None]
        med = _median(key_vals)
        if med is None:
            break
        # 找离中位数最远的样本，整条剔除（不区分方向）
        worst_idx = max(range(len(valid)),
                        key=lambda i: abs(valid[i].get(worst_key, med) - med)
                        if valid[i].get(worst_key) is not None else 0)
        valid.pop(worst_idx)

    result = {}
    for key in group_keys:
        vals = [s[key] for s in valid if s[key] is not None]
        st = _statistics(vals)
        if st.avg is not None and st.cv is not None and st.count >= MIN_STD_SAMPLE:
            st.standard_value = _standard_value(st.avg, st.count, st.cv, INDICATORS[key][1])
        result[key] = st
    return result


def collect_stratum_statistics(
    da,
    filter_plasticity: bool = True,
    filter_cv: bool = True,
    project_type: str = 'A',
) -> List[StratumStats]:
    """收集并统计所有地层的土工试验数据（V1.8.0：仅按地层编号分组）

    Args:
        da: DataAccess 实例
        filter_plasticity: 是否按塑性状态 IL 区间剔除（V2.2.4 H7：参数接线，
            此前 IL-状态复核无条件执行、参数被忽略；默认 True 行为不变）
        filter_cv: CV>0.30 时是否按 IQR→MAD 双向剔除
        project_type: 工程类型 A/B（影响 N→塑性映射）
    """
    # 1. 加载全部数据
    all_bh = {b['zkbh']: b for b in da.get_all_boreholes()}
    all_strata = da.get_all_strata()
    all_test = da.get_all_test_full(project_type)
    all_spt = da.get_all_spt()

    # 2. 按地层编号聚合样本（不按状态拆分）
    # group_key: (layer_label, era_genesis, name)
    groups: Dict[Tuple, List[dict]] = {}
    groups_state: Dict[Tuple, set] = {}  # 记录每组的状态集合

    # 预扫描：建立 (地层编号) → 出现最多的状态（用于状态缺失兜底）
    state_count: Dict[str, Dict[str, int]] = {}  # cb-yb → {state: count}
    for zkbh, strata in all_strata.items():
        for s in strata:
            cb = str(s.get('tczcbh', '')).strip()
            yb = str(s.get('tcycbh', '')).strip()
            if not cb:
                continue
            key = f'{cb}-{yb}' if yb else cb
            st = str(s.get('tcksx', '')).strip() or \
                 str(s.get('tcmsd', '')).strip() or \
                 str(s.get('tcfhcd', '')).strip()
            if st:
                state_count.setdefault(key, {})
                state_count[key][st] = state_count[key].get(st, 0) + 1

    def _inferred_state(cb, yb):
        """根据同编号的所有孔状态推断最常见状态（用于状态缺失兜底）"""
        key = f'{cb}-{yb}' if yb else cb
        cnt = state_count.get(key, {})
        if not cnt:
            return ''
        # 选出现最多的状态
        return max(cnt.items(), key=lambda x: x[1])[0]

    for zkbh, strata in all_strata.items():
        bh = all_bh.get(zkbh)
        tests = all_test.get(zkbh, [])
        spt_list = all_spt.get(zkbh, [])
        if not bh or not strata:
            continue

        for idx, s in enumerate(strata):
            cb = str(s.get('tczcbh', '')).strip()
            yb = str(s.get('tcycbh', '')).strip()
            if not cb:
                continue
            layer_label = f'{cb}-{yb}' if yb else cb

            prev_depth = strata[idx - 1]['tccdsd'] if idx > 0 else 0
            top = prev_depth
            bottom = s['tccdsd']

            # 该层内的试验样本（按深度区间匹配）
            layer_tests = [t for t in tests if top < t['qysd'] <= bottom]

            # 该层内的标贯数据（按深度区间匹配）
            # 排除跨层标贯：试验段(顶~顶+0.45m)穿过层界才剔除
            TEST_LENGTH = 0.45
            layer_spt = []
            for sp in spt_list:
                spt_top = sp['bgdsd']
                spt_bot = spt_top + TEST_LENGTH
                if spt_top >= top and spt_bot <= bottom:
                    layer_spt.append(sp)

            # 弱风化岩等不参与统计（名称中包含"风化"即视为风化岩）
            raw_name = str(s.get('tcymc', '') or s.get('tcmc', '')).strip()
            if '风化' in raw_name or classify_lithology(raw_name) == 'rock':
                continue

            # 归一化岩土名称（黏/粘统一为"黏"）
            raw_name = str(s.get('tcymc', '') or s.get('tcmc', '')).strip()
            name = raw_name.replace('粘', '黏')

            # 状态字段（记录用于标贯过滤，不用于分组键）
            raw_state = str(s.get('tcksx', '')).strip()
            if not raw_state:
                raw_state = str(s.get('tcmsd', '')).strip()
            if not raw_state:
                raw_state = str(s.get('tcfhcd', '')).strip()

            key = (
                layer_label,
                str(s.get('tcdzsd', '')).strip(),
                name,
            )
            if raw_state:
                groups_state.setdefault(key, set()).add(raw_state)

            # ---- 数据收集：合理性剔除 + 规范复核 一次完成 ----
            # 阶段0：物理合理性剔除（最严格，超出规范范围置 None）
            # 阶段1：规范复核 — IL 必须与地层塑性状态一致（每条按本层状态判断）
            _ALL_IND = list(INDICATORS.keys())
            # 推断状态（用于空状态样本的 IL 复核）
            inferred = _inferred_state(cb, yb) if (raw_state == '' or not raw_state) else raw_state
            check_state = raw_state if raw_state else inferred
            for t in layer_tests:
                t2 = t.copy()
                # 阶段0：超出物理合理范围 → 置 None
                for k in _ALL_IND:
                    if t2.get(k) is not None and not _is_reasonable_value(k, t2[k]):
                        t2[k] = None
                # 阶段1（V2.2.4 H7：filter_plasticity 参数接线——此前无条件执行、
                # 参数被忽略；调用方仅传 project_type，默认 True 行为不变）：
                # IL 与地层状态不一致 → 清空塑性字段；状态缺失时用同编号最常见状态兜底
                il = t2.get('yxzs')
                if filter_plasticity and il is not None and check_state in PLASTICITY_ORDER:
                    il_state = il_to_plasticity(il, project_type)
                    if il_state != check_state:
                        t2['yx'] = None
                        t2['sx'] = None
                        t2['sxzs'] = None
                        t2['yxzs'] = None
                # 记录本条样本的来源地层状态（用于后续分组时追溯）
                t2['_src_state'] = raw_state
                groups.setdefault(key, []).append(t2)

            # 标贯样本：合理性剔除 + 状态一致性过滤
            for sp in layer_spt:
                n_val = sp['bgjs'] if sp['bgjs'] and sp['bgjs'] > 0 else sp.get('bgxzjs', 0)
                # 阶段0：标贯物理合理性
                if n_val is not None and n_val > 0 and not _is_reasonable_value('N', n_val):
                    continue
                # 阶段1：标贯状态一致性
                if n_val and n_val > 0 and raw_state:
                    if raw_state in PLASTICITY_ORDER:
                        # 塑性判定统一浮点直判（修正击数可为小数，与 rule_engine 同口径；
                        # N_corr∈(15,16)/(20,21) 不再被 int() 截断造成两模块状态不一致）
                        spt_state = spt_to_plasticity(n_val, project_type, '不限制')
                        if spt_state is None or spt_state != raw_state:
                            continue
                    elif raw_state in DENSITY_ORDER:
                        # 密实度：用 spt_to_density 判断（修正击数可为小数，区间连续无空隙）
                        spt_state = spt_to_density(n_val)
                        if spt_state != raw_state:
                            continue
                else:
                    # 状态缺失：用同地层编号的最常见状态兜底
                    inferred = _inferred_state(cb, yb)
                    if inferred and inferred in PLASTICITY_ORDER:
                        # 同上：浮点直判（不 int() 截断）
                        spt_state = spt_to_plasticity(n_val, project_type, '不限制')
                        if spt_state is None or spt_state != inferred:
                            continue
                    elif inferred and inferred in DENSITY_ORDER:
                        spt_state = spt_to_density(n_val)
                        if spt_state != inferred:
                            continue
                    else:
                        # 兜底也找不到合理状态：按岩土名限定，并排除明显的异常值
                        litho = classify_lithology(name)
                        if n_val and n_val > 0:
                            if litho == 'clay' and (n_val < 2 or n_val > 35):
                                continue
                            if litho == 'sand' and (n_val < 3 or n_val > 40):
                                continue
                            if litho in ('fill', 'cavity', 'muck') and (n_val < 1 or n_val > 15):
                                continue
                            if litho == 'gravel' and (n_val < 5 or n_val > 80):
                                continue
                            if litho == 'rock' and (n_val < 40 or n_val > 200):
                                continue
                        else:
                            # N<=0 的无效标贯不参与统计
                            continue
                # V2.2.2 V1：标贯口径统一——bgjs=0/缺失视为"未做试验"标记，
                # N=0 行不参与统计（与 v2 标贯原始表 _extract_spt_raw 一致），
                # 避免 0 值污染 N 统计（n<4 时 IQR 不剔除导致均值被拉低）。
                if not sp.get('bgjs'):
                    continue
                t = {
                    'qybh': '', 'qysd': sp['bgdsd'],
                    'hsl': None, 'gmd': None, 'Gs': None, 'kxb': None,
                    'yx': None, 'sx': None, 'sxzs': None, 'yxzs': None,
                    'alpha': None, 'Es': None, 'phi': None, 'cohesion': None,
                    'N': sp['bgjs'],
                    'N_corr': sp.get('bgxzjs'),
                }
                groups.setdefault(key, []).append(t)

    # 3. 统计每组
    results = []
    for (label, era, name), samples in groups.items():
        before = len(samples)
        plasticity = '、'.join(sorted(groups_state.get((label, era, name), []))) or ''

        # #3 修复：深拷贝样本，避免 DOMAIN_RULES 直接修改原始数据污染底层
        samples = [t.copy() for t in samples]

        # 负值过滤：除液性指数(IL/yxzs，可为负表示坚硬)外，其余指标负值置 None
        for t in samples:
            for k in INDICATORS:
                if k == 'yxzs':
                    continue
                v = t.get(k)
                if v is not None and v < 0:
                    t[k] = None

        # 土层合理性规则过滤（如淤泥 kxb<1.0 属不合理数据）
        for rule_name, rule_key, rule_fn in DOMAIN_RULES:
            if rule_name in name:
                for t in samples:
                    v = t.get(rule_key)
                    if v is not None and rule_fn(v):
                        t[rule_key] = None

        # #5 修复：sample_count_after 统计仍有有效塑性指标的样本数
        after = sum(1 for t in samples
                    if any(t.get(k) is not None for k in ('yx', 'sx', 'sxzs', 'yxzs')))

        stats = StratumStats(
            layer_label=label,
            era_genesis=era,
            name=name,
            plasticity=plasticity,
            sample_count_before=before,
            sample_count_after=after,
            test_sample_count=sum(1 for s in samples if s.get('qybh', '').strip()),
        )

        # 按试验分组统计（同组指标联动剔除）
        # 规则：样本>=6 时按组剔除，<6 时按单个数据剔除（避免小样本损失过大）
        for group_keys, lead_key in INDICATOR_GROUPS:
            # 统计本组有效样本数（至少有一个指标非None）
            group_n = sum(1 for s in samples if any(s.get(k) is not None for k in group_keys))
            if filter_cv and group_n >= 6:
                group_stats = _compute_group_stats(samples, group_keys, lead_key)
            else:
                group_stats = {}
                for k in group_keys:
                    vals = [t.get(k) for t in samples]
                    if filter_cv:
                        group_stats[k] = _compute_with_direction(vals, INDICATORS[k][1])
                    else:
                        group_stats[k] = _statistics(vals)
            stats.indicators.update(group_stats)

        results.append(stats)

    # 4. 按地层编号排序
    def sort_key(ss: StratumStats):
        import re
        nums = re.findall(r'\d+', ss.layer_label)
        return tuple(int(x) for x in nums) if nums else (999,)

    return sorted(results, key=sort_key)


def _write_bearing_capacity(ws, ss, r, max_rows):
    """写入 S（基本承载力查表）和 T（基本承载力 σ0 推荐值）列

    S 列：查表文本，如 'e=0.951 IL=0.49 查表：σ0=191.1'
         仅对适用查表的岩土类型（黏性土/粉土/软土/黄土/残积土）有值
    T 列：推荐值，如 '推荐:σ0=100'（参数表建议值）
         **独立映射参数表**，与 S 列是否存在数据完全无关；
         只要参数表能匹配到 (地层编号, 岩性, 状态) 即写入，
         即使 S 列因土类不适用而留空，T 列仍按参数表正常填充。

    S/T 两列各自独立计算、独立写入，互不依赖。
    """
    from openpyxl.styles import Font, Alignment, Border, Side

    simsun_10 = Font(name='宋体', size=10)
    center = Alignment(horizontal='center', vertical='center')
    # S/T 列：居中 + 自动换行（列宽10，文本换行显示）
    wrap_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))

    # ---------- S 列：查表（仅适用土类，失败不影响 T） ----------
    lookup_val, lookup_text = 0.0, ''
    try:
        from bearing_capacity import compute_bearing_capacity_detail
        ind = ss.indicators
        indicator_avgs = {
            'e': ind.get('kxb').avg if ind.get('kxb') else 0,
            'IL': ind.get('yxzs').avg if ind.get('yxzs') else 0,
            'W': ind.get('hsl').avg if ind.get('hsl') else 0,
            'Es': ind.get('Es').avg if ind.get('Es') else 0,
            'WL': ind.get('yx').avg if ind.get('yx') else 0,
        }
        lookup_val, lookup_text = compute_bearing_capacity_detail(ss.name, indicator_avgs)
    except Exception:
        lookup_val, lookup_text = 0.0, ''

    if lookup_val and lookup_val > 0 and lookup_text:
        cell_s = ws.cell(row=r, column=19, value=lookup_text)
    else:
        cell_s = ws.cell(row=r, column=19, value='/')
    cell_s.font = simsun_10; cell_s.alignment = wrap_center; cell_s.border = thin_border
    if max_rows > 1:
        ws.merge_cells(start_row=r, start_column=19, end_row=r + max_rows - 1, end_column=19)

    # ---------- T 列：参数表独立映射（与 S 完全无关） ----------
    sigma0_param = 0
    try:
        from bearing_capacity import lookup_param_sigma
        sigma0_param = lookup_param_sigma(ss.layer_label, ss.name, ss.plasticity)
    except Exception:
        sigma0_param = 0

    if sigma0_param and sigma0_param > 0:
        cell_t = ws.cell(row=r, column=20, value=f'推荐:σ0={sigma0_param}')
    else:
        cell_t = ws.cell(row=r, column=20, value='/')
    cell_t.font = simsun_10; cell_t.alignment = wrap_center; cell_t.border = thin_border
    if max_rows > 1:
        ws.merge_cells(start_row=r, start_column=20, end_row=r + max_rows - 1, end_column=20)


def write_statistics_excel(
    stats_list: List[StratumStats],
    template_path: str,
    output_path: str,
):
    """将统计结果写入 Excel 模板"""
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    # 指标列映射（模板列 E=4 开始）
    indicator_cols = {
        'hsl': 4,      # E
        'gmd': 5,      # F
        'Gs': 6,       # G
        'kxb': 7,      # H
        'yx': 8,       # I
        'sx': 9,       # J
        'sxzs': 10,    # K
        'yxzs': 11,    # L
        'alpha': 12,   # M
        'Es': 13,      # N
        'phi': 14,     # O
        'cohesion': 15, # P
        'N': 16,       # Q
        'N_corr': 17,  # R
    }

    row_labels = ['统计个数', '最大值', '最小值', '平均值',
                  '标准差', '变异系数', '标准值', '中大平均值', '中小平均值']

    # 数字格式：
    #  - 标准差/变异系数：强制 3 位（所有指标）
    #  - 平均值/标准值/中大平均/中小平均：小数位与原始数据同步
    #  - 孔隙比：至少 3 位；标贯 N：至少 1 位
    #  - 统计个数：整数；最大值/最小值：原始数据
    DECIMAL_OVERRIDE = {
        'hsl': 1, 'gmd': 1,        # 含水率/重力密度 → 1位
        'yx': 1, 'sx': 1,           # 液限/塑限 → 1位
        'sxzs': 1, 'yxzs': 2,       # 塑性指数→1位 液性指数→2位
        'phi': 1, 'cohesion': 1,    # 剪切试验 → 1位
        'alpha': 2, 'Es': 2, 'Gs': 2,  # 压缩系数/压缩模量/比重 → 2位
        'kxb': 3,                   # 孔隙比 → 3位
        'N': 1, 'N_corr': 1,        # 标贯 → 1位
    }  # 小数位下限（标准差/变异系数不受影响）
    def _fmt_code(indicator_key, row_idx, decimals):
        if row_idx == 0:                       # 统计个数
            return '0'
        if row_idx in (4, 5):                  # 标准差、变异系数 → 3 位
            return '0.000'
        min_dec = DECIMAL_OVERRIDE.get(indicator_key)
        if min_dec is not None:                # 有覆盖 → 直接使用覆盖精度
            d = min_dec
        else:                                  # 无覆盖 → 按原始数据精度
            d = decimals if decimals else 0
        return '0' if d == 0 else ('0.' + '0' * d)

    # 模板从第 4 行开始为数据区；按各层实际需要的行数连续输出（不留空白行）
    start_row = 4
    current_row = start_row

    # #12 修复：清空模板数据区（从第4行到最后一行），不用过大的估算范围
    for rr in range(start_row, ws.max_row + 1):
        for c in range(1, 21):  # 扩展到列 T
            ws.cell(row=rr, column=c).value = None

    # #13 修复：先按 (label, era, name) 排序，确保去重时同组相邻
    stats_list = sorted(stats_list, key=lambda ss: (ss.layer_label, ss.era_genesis, ss.name))

    prev_abc = (None, None, None)  # 用于 A/B/C 列去重
    for ss in stats_list:
        r = current_row

        # 各指标实际样本数的最大值决定行数（<6 仅输出前4行）
        all_counts = [ind.count for ind in ss.indicators.values() if ind and ind.count > 0]
        max_indicator_n = max(all_counts) if all_counts else 0
        max_rows = 4 if max_indicator_n < 6 else 9

        # 同岩土编号的时代成因及岩土名称去重
        curr_abc = (ss.layer_label, ss.era_genesis, ss.name)
        if curr_abc == prev_abc:
            dedup_abc = True
        else:
            dedup_abc = False
            prev_abc = curr_abc

        # A-D 列公共信息
        if not dedup_abc:
            # 第一行写值，多行时合并单元格
            ws.cell(row=r, column=1, value=ss.layer_label)
            ws.cell(row=r, column=2, value=ss.era_genesis)
            ws.cell(row=r, column=3, value=ss.name)
            if max_rows > 1:
                ws.merge_cells(start_row=r, start_column=1, end_row=r+max_rows-1, end_column=1)
                ws.merge_cells(start_row=r, start_column=2, end_row=r+max_rows-1, end_column=2)
                ws.merge_cells(start_row=r, start_column=3, end_row=r+max_rows-1, end_column=3)
        # else: 留空（与上一组去重）

        for rr, label in zip(range(r, r + max_rows), row_labels[:max_rows]):
            ws.cell(row=rr, column=4, value=label)

        # E-R 列指标统计
        for key, col in indicator_cols.items():
            ind = ss.indicators.get(key)
            if not ind:
                continue
            # 有效小数位（有覆盖用覆盖，否则按原始数据）
            override = DECIMAL_OVERRIDE.get(key)
            eff_dec = override if override is not None else ind.decimals
            raw_rows = [
                ind.count,        # 0 统计个数
                ind.max_val,      # 1 最大值
                ind.min_val,      # 2 最小值
                ind.avg,          # 3 平均值
                ind.std,          # 4 标准差
                ind.cv,           # 5 变异系数
                ind.standard_value,  # 6 标准值
                ind.large_avg,    # 7 中大平均值
                ind.small_avg,    # 8 中小平均值
            ]
            for rr, v in zip(range(r, r + max_rows), raw_rows[:max_rows]):
                if v is None:
                    continue
                row_idx = rr - r
                # 数据为 0 的空着不输出（含统计个数）
                if v == 0:
                    continue
                # 平均值/标准值/中大/中小：按数据小数位四舍五入，保留精度
                if eff_dec and row_idx in (3, 6, 7, 8):
                    v = round(v, eff_dec)
                cell = ws.cell(row=rr, column=col + 1, value=v)
                fmt = _fmt_code(key, row_idx, ind.decimals)
                if fmt:
                    cell.number_format = fmt

        # ---- S/T 列：基本承载力 ----
        _write_bearing_capacity(ws, ss, r, max_rows)

        current_row += max_rows

    # 清除超出实际数据范围的多余行
    for rr in range(current_row, ws.max_row + 1):
        for c in range(1, 21):
            ws.cell(row=rr, column=c).value = None

    # 列宽 + 居中 + 宋体10号 + 默认框线
    from openpyxl.styles import Alignment, Border, Side, Font
    center = Alignment(horizontal='center', vertical='center')
    wrap_center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))
    simsun_10 = Font(name='宋体', size=10)
    for col_idx in range(1, 21):
        ws.column_dimensions[get_column_letter(col_idx)].width = 7.5
    ws.column_dimensions['C'].width = 12
    ws.column_dimensions['D'].width = 12  # 统计项目列
    ws.column_dimensions['S'].width = 10   # 列宽10 + 文本自动换行
    ws.column_dimensions['T'].width = 10   # 列宽10 + 文本自动换行
    data_end = current_row - 1
    for row in ws.iter_rows(min_row=4, max_row=data_end):
        for cell in row:
            # S/T 列（19/20）保持居中+自动换行，其余列常规居中
            if cell.column in (19, 20):
                cell.alignment = wrap_center
            else:
                cell.alignment = center
            cell.border = thin_border
            cell.font = simsun_10
    for rr in range(4, data_end + 1):
        ws.row_dimensions[rr].height = 19.5


    wb.save(output_path)
    return output_path
