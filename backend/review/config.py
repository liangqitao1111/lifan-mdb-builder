"""理反 — 配置常量与工具函数"""
import os, json, re, math
IL_EPSILON = 1e-9

LEGACY_IL_PLASTICITY_A = [
    (float('-inf'), 0,    '坚硬'), (0,    0.25, '硬塑'),
    (0.25, 0.75, '可塑'), (0.75, 1.0,  '软塑'), (1.0,  float('inf'), '流塑'),
]
LEGACY_IL_PLASTICITY_B = [
    (float('-inf'), 0,    '坚硬'), (0,    0.50, '硬塑'),
    (0.50, 1.0,  '软塑'), (1.0,  float('inf'), '流塑'),
]
LEGACY_SPT_DENSITY_MAP = [
    (float('-inf'), 10, '松散'), (10, 15, '稍密'),
    (15, 30, '中密'), (30, float('inf'), '密实'),
]
LEGACY_DPT_DENSITY_MAP = [
    (float('-inf'), 5, '松散'), (5, 10, '稍密'),
    (10, 20, '中密'), (20, float('inf'), '密实'),
]
LEGACY_SPT_PLASTICITY_A = [
    (float('-inf'), 3, '流塑'), (3, 5, '软塑'), (5, 15, '可塑'),
    (15, 20, '硬塑'), (20, float('inf'), '坚硬'),
]  # 与 工程配置.toml「A类.标贯N_可塑性」对齐（≤3/4~5/6~15/16~20/≥21，半开区间）
LEGACY_SPT_PLASTICITY_B = [
    (float('-inf'), 2, '流塑'), (2, 8, '软塑'),
    (8, 32, '硬塑'), (32, float('inf'), '坚硬'),
]
LEGACY_SPT_WEATHERING_A = [
    (float('-inf'), 30, '残积土'), (30, 50, '全风化'), (50, float('inf'), '强风化'),
]  # 与 工程配置.toml「A类.标贯N_风化程度」及手动修正范围对齐（≤30/31~50/≥51）
LEGACY_SPT_WEATHERING_B = [
    (float('-inf'), 30, '残积土'), (30, 50, '全风化'), (50, float('inf'), '强风化'),
]
LEGACY_DENSITY_ORDER = {'松散': 1, '稍密': 2, '中密': 3, '密实': 4}
LEGACY_PLASTICITY_ORDER = {'流塑': 1, '软塑': 2, '可塑': 3, '硬塑': 4, '坚硬': 5}
LEGACY_VERTICAL_THIN_LAYER_DENSE = 1.0

# =============================================================================
# TOML → 内部格式 解析工具
# =============================================================================
import re

def _parse_toml_range(toml_dict):
    """把 TOML 的 {≤10:松散, 11~15:稍密, ≥31:密实} 转成区间列表 [(lo,hi,state)]

    严格按 TOML 键自身语义解析，不做间隙吸收、不改写键语义（复评 P3 修复）：
      - `≤10`/`<=10`  → (-∞, 10]（下同，右闭）
      - `11~15`       → [11, 15]（闭区间）
      - `≥31`/`>=31`  → [31, +∞)
      - `0<IL≤0.25`   → (0, 0.25]（IL 键专用形式，按两侧不等式边界解析）
    "与上一区间上界的衔接"由 TOML 键本身保证（如 `≤10` + `10~15` + `≥30`）；
    相邻键之间的间隙不再被静默吸收：间隙内的值不命中任何区间（调用方按 None 安全跳过），
    避免把显式 `≥31` 静默改写成 >上一区间上界（否则 N=16~30 会被误判）。
    解析失败的行跳过（防御式）。
    """
    result = []
    for k, v in toml_dict.items():
        if k == '用途':
            continue
        k = k.strip()
        lo, hi = None, None
        try:
            if k.startswith('≤') or k.startswith('<='):
                lo = float('-inf')
                hi = float(k[2:]) if k.startswith('<=') else float(k[1:])
            elif k.startswith('≥') or k.startswith('>='):
                lo = float(k[2:]) if k.startswith('>=') else float(k[1:])
                hi = float('inf')
            elif '~' in k:
                parts = k.split('~')
                lo = float(parts[0])
                hi = float(parts[1])
            elif 'IL' in k:
                # 液性指数键形式：IL≤0 / 0<IL≤0.25 / IL>1.0（无 '~'）
                hi_m = re.search(r'[≤<]([\d.]+)$', k)
                lo_m = re.search(r'^([\d.]+)', k)
                gt_m = re.search(r'[>]([\d.]+)$', k)
                if hi_m:
                    hi = float(hi_m.group(1))
                    lo = float(lo_m.group(1)) if lo_m else float('-inf')
                elif gt_m:
                    # "IL>1.0"：> 下限 → (1.0, inf)（原实现漏解析该键）
                    lo = float(gt_m.group(1))
                    hi = float('inf')
                elif lo_m:
                    # 形如 "1.0<IL"（防御写法）
                    lo = float(lo_m.group(1))
                    hi = float('inf')
            elif k.startswith('>'):
                lo = float(k[1:])
                hi = float('inf')
        except (ValueError, TypeError):
            continue  # 解析失败的行跳过（防御式）
        if lo is not None and hi is not None:
            result.append((lo, hi, v))
    # 仅按左界排序，不再把左界改写为上一区间上界（间隙保留，严格按键语义）
    result.sort(key=lambda x: x[0])
    return result


def _parse_toml_range_to_single(toml_dict):
    """把 TOML 的 {松散=1, 稍密=2, ...} 转成 {松散:1, ...}"""
    return {k: int(v) for k, v in toml_dict.items() if k != '用途'}


def parse_domain_condition(cond):
    """解析试验指标域规则判断条件（<1.0 / >1.0 / >=1.0 / <=1.0），返回 (op, 阈值)

    V2.2.4（H4）：'>=1.0'/'<=1.0' 前缀必须先于 '>'/'<' 判定——原实现先匹配
    '>'/'<' 再取 float(cond[1:]) 会得到 float('=1.0') 抛 ValueError，且该解析在
    soil_stats 导入期执行，一条 '>=' 配置即可使整个模块导入崩溃（latent，配置触发）。
    V2.2.5（E2）：中文 '≥1.0'/'≤1.0' 同样支持（与 _parse_toml_range 双写法口径一致），
    '≥'/'≤' 分别归一为 ('>=', t)/('<=', t)；TOML 阈值键为中文符号书写，用户照抄
    "判断" = "≥1.0" 不再静默跳过。
    解析失败返回 None（调用方跳过该行，防御式，与 _parse_toml_range 口径一致）。
    """
    if not cond:
        return None
    s = str(cond).strip()
    for op in ('>=', '<=', '>', '<'):
        if s.startswith(op):
            try:
                th = float(s[len(op):])
            except (ValueError, TypeError):
                return None
            return op, th
    # 中文 ≥/≤（单字符，与 ASCII 前缀无冲突；float 自动容忍 '≥ 1.0' 空格）
    for ch, op in (('≥', '>='), ('≤', '<=')):
        if s.startswith(ch):
            try:
                th = float(s[len(ch):])
            except (ValueError, TypeError):
                return None
            return op, th
    return None


# =============================================================================
# 加载 TOML 配置，构建运行时常量
# =============================================================================
def _load_toml(path):
    """尝试用 tomllib(Python3.11+) 或 tomli(Python3.8+) 读取 TOML"""
    try:
        import tomllib
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        import tomli
        with open(path, 'rb') as f:
            return tomli.load(f)
    except ImportError:
        pass
    return None


_PROJECT_CONFIG = None  # 配置缓存（load_project_config 唯一加载器使用）


def load_project_config():
    """工程配置唯一加载器：优先 TOML，其次 JSON 回退；均失败返回空 dict

    统一了此前的双加载器（_load_project_config_raw / load_project_config）：
      - TOML 存在且解析成功 → 记录"已加载"
      - TOML 存在但解析失败 → 明确记录"解析失败"后尝试 JSON 回退
      - JSON 加载/解析失败 → 分别记录日志
    所有模块（rule_engine/dao/bearing_capacity/karst 等）都经此函数读取配置。
    """
    global _PROJECT_CONFIG, _CONFIG_LOAD_LOG
    if _PROJECT_CONFIG is not None:
        return _PROJECT_CONFIG
    base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    toml_path = os.path.join(base, '参数', '工程配置.toml')
    json_path = os.path.join(base, '参数', '工程配置.json')

    toml_status = '未加载'
    if os.path.exists(toml_path):
        data = _load_toml(toml_path)
        if data is not None:
            _CONFIG_LOAD_LOG['TOML路径'] = toml_path
            _CONFIG_LOAD_LOG['TOML状态'] = '已加载'
            _CONFIG_LOAD_LOG['TOML段数'] = len(data)
            _CONFIG_LOAD_LOG['TOML段列表'] = list(data.keys())
            _PROJECT_CONFIG = data
            return _PROJECT_CONFIG
        # TOML 存在但解析失败：明确记录（区别于"不存在"），再尝试 JSON 回退
        toml_status = '存在但解析失败'
        _CONFIG_LOAD_LOG['加载详情'].append({
            '段': 'TOML', '状态': '默认',
            '详情': '工程配置.toml 解析失败，尝试 JSON 回退'})
    else:
        toml_status = '不存在'

    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            _CONFIG_LOAD_LOG['TOML路径'] = json_path
            _CONFIG_LOAD_LOG['TOML状态'] = f'{toml_status}，已用 JSON 回退'
            _CONFIG_LOAD_LOG['TOML段数'] = len(data)
            _CONFIG_LOAD_LOG['TOML段列表'] = list(data.keys())
            _PROJECT_CONFIG = data
            return _PROJECT_CONFIG
        except Exception as e:
            _CONFIG_LOAD_LOG['加载详情'].append({
                '段': 'JSON', '状态': '默认',
                '详情': f'工程配置.json 解析失败: {e}'})

    _CONFIG_LOAD_LOG['TOML状态'] = '不存在，使用内置默认值'
    _PROJECT_CONFIG = {}
    return _PROJECT_CONFIG


# 配置加载日志（全局，供 GUI 启动时读取）
_CONFIG_LOAD_LOG = {
    'TOML路径': '',
    'TOML状态': '未加载',
    'TOML段数': 0,
    'TOML段列表': [],
    '加载详情': [],  # 各段加载情况
}

def log_config_load(section, status, detail=''):
    """各模块加载配置时记录日志"""
    _CONFIG_LOAD_LOG['加载详情'].append({
        '段': section,
        '状态': status,  # 'TOML' 或 '默认'
        '详情': detail
    })

def get_config_summary():
    """返回配置加载摘要（供 GUI 启动时显示）"""
    log = _CONFIG_LOAD_LOG
    if not log['加载详情']:
        return log['TOML状态']
    toml_count = sum(1 for x in log['加载详情'] if x['状态'] == 'TOML')
    default_count = sum(1 for x in log['加载详情'] if x['状态'] == '默认')
    missing = [x['段'] for x in log['加载详情'] if x['状态'] == '默认']
    summary = f"配置: 已加载 {log['TOML段数']} 个段, {toml_count}项TOML/{default_count}项默认值"
    if missing and len(missing) <= 5:
        summary += f" | 默认值段: {', '.join(missing)}"
    return summary

_cfg_data = load_project_config()

# ---- 标贯/动探→密实度 ----
_tmp = _cfg_data.get('公用', {}).get('标贯N_密实度', {})
if _tmp:
    SPT_DENSITY_MAP = _parse_toml_range(_tmp)
    log_config_load('公用.标贯N_密实度', 'TOML')
else:
    SPT_DENSITY_MAP = list(LEGACY_SPT_DENSITY_MAP)
    log_config_load('公用.标贯N_密实度', '默认')

_tmp = _cfg_data.get('公用', {}).get('动探N63_5_密实度', {})
if _tmp:
    DPT_DENSITY_MAP = _parse_toml_range(_tmp)
    log_config_load('公用.动探N63_5_密实度', 'TOML')
else:
    DPT_DENSITY_MAP = list(LEGACY_DPT_DENSITY_MAP)
    log_config_load('公用.动探N63_5_密实度', '默认')

# ---- IL→可塑性 ----
_tmp = _cfg_data.get('A类', {}).get('液性指数IL_可塑性', {})
if _tmp:
    IL_PLASTICITY_A = _parse_toml_range(_tmp)
    log_config_load('A类.液性指数IL_可塑性', 'TOML')
else:
    IL_PLASTICITY_A = list(LEGACY_IL_PLASTICITY_A)
    log_config_load('A类.液性指数IL_可塑性', '默认')

_tmp = _cfg_data.get('B类', {}).get('液性指数IL_可塑性', {})
if _tmp:
    IL_PLASTICITY_B = _parse_toml_range(_tmp)
    log_config_load('B类.液性指数IL_可塑性', 'TOML')
else:
    IL_PLASTICITY_B = list(LEGACY_IL_PLASTICITY_B)
    log_config_load('B类.液性指数IL_可塑性', '默认')

# ---- N→可塑性 ----
_tmp = _cfg_data.get('A类', {}).get('标贯N_可塑性', {})
if _tmp:
    SPT_PLASTICITY_A = _parse_toml_range(_tmp)
    log_config_load('A类.标贯N_可塑性', 'TOML')
else:
    SPT_PLASTICITY_A = list(LEGACY_SPT_PLASTICITY_A)
    log_config_load('A类.标贯N_可塑性', '默认')

_tmp = _cfg_data.get('B类', {}).get('标贯N_可塑性', {})
if _tmp:
    SPT_PLASTICITY_B = _parse_toml_range(_tmp)
    log_config_load('B类.标贯N_可塑性', 'TOML')
else:
    SPT_PLASTICITY_B = list(LEGACY_SPT_PLASTICITY_B)
    log_config_load('B类.标贯N_可塑性', '默认')

# ---- N→风化程度 ----
_tmp = _cfg_data.get('A类', {}).get('标贯N_风化程度', {})
if _tmp:
    SPT_WEATHERING_A = _parse_toml_range(_tmp)
    log_config_load('A类.标贯N_风化程度', 'TOML')
else:
    SPT_WEATHERING_A = list(LEGACY_SPT_WEATHERING_A)
    log_config_load('A类.标贯N_风化程度', '默认')

_tmp = _cfg_data.get('B类', {}).get('标贯N_风化程度', {})
if _tmp:
    SPT_WEATHERING_B = _parse_toml_range(_tmp)
    log_config_load('B类.标贯N_风化程度', 'TOML')
else:
    SPT_WEATHERING_B = list(LEGACY_SPT_WEATHERING_B)
    log_config_load('B类.标贯N_风化程度', '默认')

# ---- 顺序映射 ----
_tmp = _cfg_data.get('公用', {}).get('密实度顺序', {})
if _tmp:
    DENSITY_ORDER = _parse_toml_range_to_single(_tmp)
    log_config_load('公用.密实度顺序', 'TOML')
else:
    DENSITY_ORDER = dict(LEGACY_DENSITY_ORDER)
    log_config_load('公用.密实度顺序', '默认')

_tmp = _cfg_data.get('公用', {}).get('塑性顺序', {})
if _tmp:
    PLASTICITY_ORDER = _parse_toml_range_to_single(_tmp)
    log_config_load('公用.塑性顺序', 'TOML')
else:
    PLASTICITY_ORDER = dict(LEGACY_PLASTICITY_ORDER)
    log_config_load('公用.塑性顺序', '默认')

# ---- 纵向检查参数 ----
_tmp = _cfg_data.get('公用', {}).get('纵向检查', {})
if _tmp:
    VERTICAL_THIN_LAYER_DENSE = float(_tmp.get('砂土纵向检查最小厚度', LEGACY_VERTICAL_THIN_LAYER_DENSE))
    log_config_load('公用.纵向检查', 'TOML')
else:
    VERTICAL_THIN_LAYER_DENSE = LEGACY_VERTICAL_THIN_LAYER_DENSE
    log_config_load('公用.纵向检查', '默认')

# ---- 岩土分类集 ----
_CAVE_KEYS = {'溶洞/洞穴', '溶洞', '洞穴'}
_MUCK_KEYS = {'淤泥/软土'}
_CLAY_KEYS = {'黏性土'}
_SILT_KEYS = {'粉土'}
_SAND_KEYS = {'砂土'}
_GRAVEL_KEYS = {'碎石土'}
_FILL_KEYS = {'填土'}
_ROCK_KEYS = {'岩石'}
_SALINE_KEYS = {'盐渍土'}
_FROZEN_KEYS = {'冻土/冰', '冻土'}
_LOESS_KEYS = {'黄土', '湿陷性黄土', '新黄土', '老黄土'}  # S4 内置默认词（TOML 未含黄土键时兜底）

_tmp_lith = _cfg_data.get('岩土分类', {})
if _tmp_lith:
    CAVITY_TYPES = set()
    for k, v in _tmp_lith.items():
        if any(x in k for x in ('溶洞', '洞穴')):
            CAVITY_TYPES.update(v)
    MUCK_TYPES = set(_tmp_lith.get('淤泥/软土', []))
    CLAY_TYPES = set(_tmp_lith.get('黏性土', []))
    SILT_TYPES = set(_tmp_lith.get('粉土', []))
    SAND_TYPES = set(_tmp_lith.get('砂土', []))
    GRAVEL_TYPES = set(_tmp_lith.get('碎石土', []))
    FILL_TYPES = set(_tmp_lith.get('填土', []))
    ROCK_TYPES = set(_tmp_lith.get('岩石', []))
    SALINE_TYPES = set(_tmp_lith.get('盐渍土', []))
    # V2.2.5（E1）：TOML 岩土分类键为 '冻土'（历史 V2.1.3 引入的键名失配——代码只读
    # '冻土/冰' 导致 FROZEN_TYPES 为空、冻土被归 'other'、R-CRS-011 成死规则）。
    # 代码兼容双键（'冻土/冰' 与 '冻土' 均读取并取并集），不动用户 TOML。
    FROZEN_TYPES = set()
    for _fk in ('冻土/冰', '冻土'):
        FROZEN_TYPES.update(_tmp_lith.get(_fk, []))
    # V3.0.3（S4）：黄土纳入岩土分类识别 → 'loess'（修复前 '黄土' 落 'other'）。
    # 依据：黄土为特殊土（GB50021-2001 第6章 湿陷性土；GB50025-2018 湿陷性黄土地区
    # 建筑规范；手册\岩土工程勘察手册 第1章 湿陷性土），湿陷性评价需专项数据
    # （δs/δzs），暂不参与密实度/可塑性规则——rule_engine 各分支均不含 loess，
    # 黄土层不触发 sand/clay 规则，行为与修复前 'other' 完全一致（合规审查 Agent3-S4）。
    # TOML 本轮仅允许新增「动探杆长偏移」，未加"黄土"键，故内置默认词兜底保证识别，
    # 后续可在 [岩土分类] 增加 "黄土" 键覆盖扩充（取并集，不冲突）。
    # 取并集：TOML 若有 "黄土" 键可扩充，内置默认词保证 湿陷性黄土/新黄土/老黄土 也识别
    LOESS_TYPES = set(_tmp_lith.get('黄土', [])) | _LOESS_KEYS
else:
    CAVITY_TYPES = {'溶洞', '土洞', '溶洞1', '溶洞2', '溶洞3', '土洞1', '土洞2', '土洞3',
                    '溶洞无填充', '溶洞无充填', '溶洞半填充', '溶洞半充填',
                    '溶洞全填充', '溶洞全充填', '土洞无填充', '土洞无充填',
                    '土洞半填充', '土洞半充填', '土洞全填充', '土洞全充填',
                    '岩溶化灰岩'}
    log_config_load('岩土分类', '默认')
    MUCK_TYPES = {'淤泥', '淤泥质土', '淤泥质黏土', '淤泥质粉质黏土', '软土', '软黏性土', '泥炭', '泥炭质土'}
    CLAY_TYPES = {'黏土', '粉质黏土', '黏土夹粉土', '粉质黏土夹粉土', '红黏土'}
    SILT_TYPES = {'粉土', '砂质粉土', '黏质粉土'}
    SAND_TYPES = {'细砂', '中砂', '粗砂', '砾砂', '粉砂'}
    GRAVEL_TYPES = {'圆砾', '角砾', '卵石', '碎石', '漂石', '块石'}
    FILL_TYPES = {'杂填土', '素填土', '冲填土', '压实填土'}
    ROCK_TYPES = {'花岗岩', '石灰岩', '砂岩', '大理岩', '玄武岩', '片麻岩', '石英岩', '泥岩', '页岩', '粉砂岩', '黏土岩', '全风化岩', '强风化岩', '中风化岩', '微风化岩'}
    SALINE_TYPES = {'盐渍土', '氯盐渍土', '硫酸盐渍土', '碳酸盐渍土'}
    FROZEN_TYPES = {'冻土', '多年冻土', '季节冻土'}
    # V3.0.3（S4）：默认集兜底（无 TOML 岩土分类时），与 if 分支内置默认词一致
    LOESS_TYPES = set(_LOESS_KEYS)

# 子类按 ①/② 半开区间 [min,max) 归属（边界归上档，V3.0.3 Agent1-7）
_soft_soil_raw = _cfg_data.get('B类', {}).get('软土分类', {})
if _soft_soil_raw:
    SOFT_SOIL_CLASSIFICATION = {}
    for name, criteria in _soft_soil_raw.items():
        SOFT_SOIL_CLASSIFICATION[name] = {
            'wu_min': criteria.get('wu最小', 0),
            'wu_max': criteria.get('wu最大', 100),
            'e_min': criteria.get('e最小', 0),
            'e_max': criteria.get('e最大', 999),
            'hsl_condition': criteria.get('hsl条件', False),
            'required_count': criteria.get('需要满足条件数', 2),
        }
    log_config_load('B类.软土分类', 'TOML', f'{len(SOFT_SOIL_CLASSIFICATION)}个子类')
else:
    SOFT_SOIL_CLASSIFICATION = {}
    log_config_load('B类.软土分类', '默认', '未配置')


def classify_soft_soil(wu, e, hsl, yx, proj_type='B'):
    """按 TB10012 判定软土子类（B 类）

    参数:
        wu: 有机质含量 (%)
        e: 天然孔隙比
        hsl: 天然含水率 (%)
        yx: 液限 (%)
        proj_type: 工程类型（仅 B类生效）

    返回:
        匹配的软土子类名称（如 '淤泥质土'、'淤泥'）；不满足软土必要条件返回 None

    历史修复：
    V2.2.2（C2）：不再"首中即返"——首项软黏性土的 e最大=999 是 TOML 兜底占位
    （语义"e 无上界"），原逻辑下它使第②条件几乎恒真、后 4 个子类不可达。
    V3.0.3（Agent1-6/7）：软土【必要条件】改为 e≥1.0 且 天然含水率>液限——
    GB 50021-2001 第6.3.1条 / 知识库《岩土指标测试、统计与图表》："软土指天然孔隙比
    大于等于1.0，且天然含水量大于液限的细粒土，包含淤泥、淤泥质土、泥炭、泥炭质土等"。
    二者缺一直接返回 None（w<wL 时即使 wu/e 满足也不得归入软土子类，原"三选二"
    判定作废）；子类仅按 TOML 的 wu/e 区间细分（有机质含量分档）。
    e/wu 区间按【半开语义 [min,max)】归属（边界归上档：wu=3→淤泥质土、e=1.5→淤泥），
    消除原闭区间重叠"平局取靠后"的歧义；最后一个子类（泥炭）上限为自然上界含端点。
    """
    if proj_type != 'B' or not SOFT_SOIL_CLASSIFICATION:
        return None
    wu, e, hsl, yx = float(wu), float(e), float(hsl), float(yx)
    # 软土必要条件：e≥1.0 且 w>wL（GB 50021 6.3.1；不满足直接返回 None，
    # 不再按 TOML"需要满足条件数"三选二——该键保留仅为历史配置兼容，不再参与判定）
    if e < 1.0 or hsl <= yx:
        return None
    names = list(SOFT_SOIL_CLASSIFICATION.keys())
    for i, name in enumerate(names):
        c = SOFT_SOIL_CLASSIFICATION[name]
        # 半开语义 [min,max)：上界归下一档（wu=3→淤泥质土、e=1.5→淤泥）；
        # 最后一个子类（泥炭）的 wu/e 上界为自然上限（wu=100 / e 无上界占位），含端点
        last = (i == len(names) - 1)
        wu_ok = c['wu_min'] <= wu and (wu <= c['wu_max'] if last else wu < c['wu_max'])
        e_ok = c['e_min'] <= e and (e <= c['e_max'] if last else e < c['e_max'])
        if wu_ok and e_ok:
            return name
    return None



# ---- A类标贯杆长修正系数（GB50021-2001）：TOML 可配置，无配置回退硬编码表 ----
_tmp_spt_a = _cfg_data.get('公用', {}).get('标贯杆长修正_A类', {})
SPT_ROD_CORRECTION_A = []
if _tmp_spt_a:
    _items = []
    for _k, _v in _tmp_spt_a.items():
        if _k == '用途':
            continue
        try:
            _items.append((float(str(_k).replace('m', '')), float(_v)))
        except (ValueError, TypeError):
            continue
    _items.sort()
    if len(_items) >= 2:
        SPT_ROD_CORRECTION_A = _items
        log_config_load('公用.标贯杆长修正_A类', 'TOML', f'{len(_items)}档')
    else:
        log_config_load('公用.标贯杆长修正_A类', '默认', 'TOML档数不足')
if not SPT_ROD_CORRECTION_A:
    SPT_ROD_CORRECTION_A = [(3, 1.00), (6, 0.92), (9, 0.86),
                            (12, 0.81), (15, 0.77), (18, 0.73), (21, 0.70)]
    if not _tmp_spt_a:
        log_config_load('公用.标贯杆长修正_A类', '默认')


def spt_correction_gb50021(L: float) -> float:
    """A类工程（GB50021-2001）标贯杆长修正系数，线性插值

    表来自 TOML「公用.标贯杆长修正_A类」（无配置时用内置默认表）。
    工民建规范，与 B类（铁建 TB10012）不同，B类查 dao.py _spt_correction_coefficient。
    """
    table = SPT_ROD_CORRECTION_A
    if not table:
        return 1.0
    if L <= table[0][0]: return table[0][1]
    if L >= table[-1][0]: return table[-1][1]
    for (l1, a1), (l2, a2) in zip(table, table[1:]):
        if l1 <= L <= l2:
            return a1 + (a2 - a1) * (L - l1) / (l2 - l1)
    return table[-1][1]

# 动探 N63.5 杆长修正系数（GB50021-2001）
# 杆长 L 与击数 N 轴可由 TOML「公用.动探杆长修正_A类」覆盖（无配置回退硬编码）；
# 系数矩阵 α[N_idx][L_idx] 为 GB50021-2001 附录B 表B.0.1 固定值（TOML 不含矩阵），
# 覆盖 L/N 时须保持与矩阵维度一致（10×10），否则回退默认轴。
DPT_ROD_L = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
DPT_ROD_N = [1, 5, 10, 15, 20, 25, 30, 35, 40, 50]
DPT_ROD_ALPHA = [
    [1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00,1.00],
    [1.00,0.96,0.93,0.90,0.88,0.85,0.82,0.79,0.77,0.75],
    [1.00,0.95,0.90,0.86,0.83,0.79,0.76,0.73,0.70,0.67],
    [1.00,0.93,0.88,0.83,0.79,0.75,0.71,0.67,0.63,0.59],
    [1.00,0.92,0.85,0.80,0.75,0.70,0.66,0.62,0.57,0.53],
    [1.00,0.90,0.83,0.77,0.72,0.67,0.62,0.57,0.53,0.48],
    [1.00,0.89,0.81,0.75,0.69,0.64,0.58,0.54,0.49,0.44],
    [1.00,0.87,0.79,0.73,0.67,0.61,0.56,0.51,0.46,0.41],
    [1.00,0.86,0.78,0.71,0.64,0.59,0.53,0.48,0.43,0.39],
    [1.00,0.84,0.75,0.67,0.61,0.55,0.50,0.45,0.40,0.36],
]

# 动探杆长/击数轴：TOML「公用.动探杆长修正_A类」覆盖（维度须与系数矩阵一致，否则回退）
_tmp_dpt_a = _cfg_data.get('公用', {}).get('动探杆长修正_A类', {})
if isinstance(_tmp_dpt_a, dict):
    try:
        _dpt_l = [float(x) for x in _tmp_dpt_a.get('杆长', [])]
        _dpt_n = [float(x) for x in _tmp_dpt_a.get('击数', [])]
        if len(_dpt_l) == len(DPT_ROD_ALPHA[0]) and len(_dpt_n) == len(DPT_ROD_ALPHA):
            DPT_ROD_L, DPT_ROD_N = _dpt_l, _dpt_n
            log_config_load('公用.动探杆长修正_A类', 'TOML',
                            f'L{len(_dpt_l)}档/N{len(_dpt_n)}档')
        else:
            log_config_load('公用.动探杆长修正_A类', '默认', '维度与系数矩阵不匹配')
    except (TypeError, ValueError):
        log_config_load('公用.动探杆长修正_A类', '默认', 'TOML 解析失败')
else:
    log_config_load('公用.动探杆长修正_A类', '默认')


def dpt_rod_correction_a(rod_length: float, raw_n: float) -> float:
    """A类工程（GB50021-2001）动探 N63.5 杆长修正：双线性插值，返回修正系数 α

    N≥50（含 >50）时按当前杆长取最后一行（N=50）插值，不再固定返回第 0 列
    （杆长=2m）的系数——修复原实现 N≥50 时完全不修正的问题。
    V3.0.2（A5/D）：B类铁建 TB10041-2003 重型动探修正表与 GB50021 表B.0.1 同值
    （合规审查确认），B类同样复用本表计算修正值（调用方传 project_type='B' 时触发）。
    """
    if raw_n <= 0:
        return 1.0
    L, N = DPT_ROD_L, DPT_ROD_N
    # 杆长≤最短杆：整列系数均为 1.00
    if rod_length <= L[0]:
        return 1.0

    # N 行索引（N≥50 封顶到最后一行）
    if raw_n <= N[0]:
        n_idx = 0
    elif raw_n >= N[-1]:
        n_idx = len(N) - 1
    else:
        n_idx = next(i for i, v in enumerate(N) if v > raw_n) - 1
    n_lo, n_hi = N[n_idx], N[min(n_idx + 1, len(N) - 1)]

    # 杆长≥最长杆：取该 N 行最末列（不做外推）
    if rod_length >= L[-1]:
        return DPT_ROD_ALPHA[n_idx][-1]

    # 找 L 区间
    li = next(i for i, v in enumerate(L) if v > rod_length) - 1
    l_lo, l_hi = L[li], L[li + 1]

    # 四个角的值（N 已封顶时行间权重为 0，退化为行内插值）
    a00 = DPT_ROD_ALPHA[n_idx][li]
    a01 = DPT_ROD_ALPHA[n_idx][li + 1]
    a10 = DPT_ROD_ALPHA[n_idx + 1][li] if n_idx + 1 < len(N) else a00
    a11 = DPT_ROD_ALPHA[n_idx + 1][li + 1] if n_idx + 1 < len(N) else a01

    # 沿 L 插值
    t_l = (rod_length - l_lo) / (l_hi - l_lo) if l_hi != l_lo else 0
    r0 = a00 + (a01 - a00) * t_l
    r1 = a10 + (a11 - a10) * t_l
    # 沿 N 插值
    t_n = (raw_n - n_lo) / (n_hi - n_lo) if n_hi != n_lo else 0
    return r0 + (r1 - r0) * t_n


def dpt_rod_length_offset() -> float:
    """动探杆长偏移(m)：理正动探表无杆长字段，按 测试深度+偏移 近似杆长（经验假设）

    依据（合规审查 Agent3-S1 / Agent1-10）：杆长每差 2m α 差约 0.03~0.08，
    直接影响修正值与密实度边界，须项目实测标定；TOML「公用.动探杆长偏移」可配置，
    缺失或非法回退 2.0（历史行为，保持默认不变）。dao/actions 三处调用点共用。
    """
    try:
        v = float(_cfg_data.get('公用', {}).get('动探杆长偏移', 2.0))
    except (TypeError, ValueError):
        return 2.0
    return v if v >= 0 else 2.0

# ---- 以下常量由 TOML 驱动（文件顶部自动加载），此处无硬编码 ----


# ---- 岩土分类已经在文件顶部 TOML 加载段定义 ----
# ---- 工程配置唯一加载器 load_project_config 定义在文件顶部 TOML 加载段 ----

ALLOWED_FIELDS = {'TCZCBH', 'TCYCBH', 'TCCDSD', 'TCHD', 'TCYMC', 'TCMC',  # G1: TCMC=理正库岩土名称列变体
                  'TCYS', 'TCKSX', 'TCMSD', 'TCFHCD', 'TCMS'}

DTLX_MAP = {'1': '轻型', '2': '重型', '3': '超重型'}
SWLX_MAP = {'0': '初见', '1': '稳定', '2': '混合', '3': '恢复'}
SWXZ_MAP = {'1': '上层滞水', '2': '潜水', '3': '承压水'}

# 逆向映射（自动推导，与正查保持同步）
SWXZ_REVERSE = {v: k for k, v in SWXZ_MAP.items()}
SWLX_REVERSE = {v: k for k, v in SWLX_MAP.items()}
DTLX_REVERSE = {v: k for k, v in DTLX_MAP.items()}

# =============================================================================
# 更新配置
# =============================================================================
UPDATE_URL = 'https://gitee.com/liang-qitao/shenzhou666/raw/main/version.json'
                  # JSON格式: {"version":"V1.5.2","changelog":"...","files":{"Lifan_V1.5.2.pyw":"url",...}}

# =============================================================================
# 颜色常量
# =============================================================================
COLOR_H = '#FCEBEB'
COLOR_M = '#FAD7A0'
COLOR_EDITED = '#E1F5EE'
TEXT_H = '#A32D2D'
TEXT_M = '#854F0B'

# QColor 版本（需要 from PySide6.QtGui import QColor 后使用）
def make_colors():
    from PySide6.QtGui import QColor
    return {
        'QCOLOR_H': QColor(COLOR_H),
        'QCOLOR_M': QColor(COLOR_M),
        'QCOLOR_EDITED': QColor(COLOR_EDITED),
        'QTEXT_H': QColor(TEXT_H),
        'QTEXT_M': QColor(TEXT_M),
    }


# =============================================================================
# 工具函数
# =============================================================================
def normalize_state_word(v):
    """状态词归一化：'稍密状/中密状/密实状/松散状' → '稍密/中密/密实/松散'

    V2.2.3（S7）：理正库存在带"状"尾的写法（如 tcmsd='稍密状'），直接参与
    R-DEN-001 等规则比较会因 '稍密状' != '稍密' 误报。此处仅当去尾字"状"后的
    剩余部分为已知密实度/可塑性状态词时归一化（保留模糊匹配特色，不误伤
    '密实状' 以外的任意带"状"描述）。
    """
    s = str(v).strip() if v is not None else ''
    if s and s.endswith('状') and len(s) > 1:
        base = s[:-1]
        if base in DENSITY_ORDER or base in PLASTICITY_ORDER:
            return base
    return s


def classify_lithology(name: str) -> str:
    """根据岩土名称返回分类标签"""
    if name in CAVITY_TYPES: return 'cavity'
    # V3.0.3（S4）：黄土为特殊土（GB50021 第6章；GB50025-2018），湿陷性评价需
    # 专项数据（δs/δzs），暂不参与密实度/可塑性规则（rule_engine 无 loess 分支，
    # 行为与修复前 'other' 一致，不改变现有规则触发）
    if name in LOESS_TYPES: return 'loess'
    if name in MUCK_TYPES: return 'muck'
    if name in CLAY_TYPES: return 'clay'
    if name in SILT_TYPES: return 'silt'
    if name in SAND_TYPES: return 'sand'
    if name in GRAVEL_TYPES: return 'gravel'
    if name in FILL_TYPES: return 'fill'
    if name in ROCK_TYPES: return 'rock'
    if name in SALINE_TYPES: return 'saline'
    if name in FROZEN_TYPES: return 'frozen'
    return 'other'


def _classify_sand_detail(r2_05, r05_025, r025_0075, r20_2, r0075=None):
    """根据粒组占比确定砂土的具体名称（砾砂/粗砂/中砂/细砂/粉砂）

    V3.0.3（Agent1-12）：由"主粒组占优法"改为 GB 50021-2001 表3.3.3/表3.3.6
    【由大到小累计含量法】——按 砾砂(>2mm 25%~50%) → 粗砂(>0.5mm>50%) →
    中砂(>0.25mm>50%) → 细砂(>0.075mm>85%) → 粉砂(>0.075mm 50%~85%)
    依次判定，最先符合者定名。依据：知识库《岩土工程常用数据公式汇总》（《岩勘规》
    表3.3.3 注"定名时应根据颗粒级配由大到小以最先符合者确定"）；《岩土工程勘察手册》
    01"砂土：砾砂→粗砂→中砂→细砂→粉砂"。
    V3.0.3（Agent1-11）：粉砂不再要求"细粒（0.25~0.075mm）主导"附加条件——
    GB 50021 表3.3.6 仅按 >0.075mm 颗粒含量 50%~85% 界定，无细粒主导要求
    （依据：《地基处理手册》CH01"粒径大于0.075mm 的颗粒不超过全重的85%，但超过50%
    称为粉砂土"）。

    分母说明：颗分表 r20_2/r2_05/r05_025/r025_0075/r0075 均为占总质量百分比
    （合计 100），累计含量按占总质量比例判定；r0075 缺失时按 0 处理（分母取
    >0.075mm 粒组合计，与既有测试口径一致）。>2mm 含量≥50% 属碎石土，由调用方
    classify_soil 提前拦截，本函数只处理砂土细分（砾砂上界 50% 由调用方保证）。
    """
    gravel = r20_2 or 0
    coarse = r2_05 or 0
    medium = r05_025 or 0
    fine = r025_0075 or 0
    r0075 = r0075 if r0075 is not None else 0
    total = gravel + coarse + medium + fine + r0075
    if total <= 0:
        return '砂土'
    # 砾砂：>2mm 颗粒 25%~50%（GB/T 50145；恰为 25% 亦属砾砂——复评 P3 边界修正）
    if gravel / total >= 0.25:
        return '砾砂'
    # 粗砂：>0.5mm 累计 >50%（严格大于，恰 50% 落入下一档）
    if (gravel + coarse) / total > 0.5:
        return '粗砂'
    # 中砂：>0.25mm 累计 >50%
    if (gravel + coarse + medium) / total > 0.5:
        return '中砂'
    # 细砂：>0.075mm 累计 >85%（即 r0075 < 15%）
    over_0075 = 100.0 - r0075 if r0075 > 0 else (gravel + coarse + medium + fine)
    if over_0075 > 85:
        return '细砂'
    # 粉砂：>0.075mm 累计 50%~85%
    if 50 < over_0075 <= 85:  # 细砂须 >85%（严格），故 85% 恰属粉砂上限
        return '粉砂'
    # 无任何档命中（如 >0.075mm<50% 且无 r0075 明细）：按主粒组兜底（保持既有行为）
    dominant = max((coarse, '粗砂'), (medium, '中砂'), (fine, '细砂'), key=lambda x: x[0])
    return dominant[1]


def _classify_mixed_soil(sand_pct, fine_pct, ip, gravel_pct=0,
                        r2_05=None, r05_025=None, r025_0075=None, r20_2=None):
    """混合土定名（含X砂粉质黏土 / 含粉质黏土X砂等）
    仅当筛分数据和塑性指数(IP)同时存在时才进行组合定名。
    砂名根据粒组占比动态确定，非硬编码。
    """
    if ip is None:
        return None
    if sand_pct is None or fine_pct is None:
        return None

    # 动态确定砂名（按粒组占比）
    if r2_05 is not None or r05_025 is not None or r025_0075 is not None:
        sand_name = _classify_sand_detail(r2_05, r05_025, r025_0075, r20_2)
    else:
        # 无颗分细节时，用砾/砂比例推断（≥25% 判砾砂，与 GB/T 50145 一致）
        if gravel_pct and sand_pct and gravel_pct / (gravel_pct + sand_pct) >= 0.25:
            sand_name = '砾砂'
        else:
            sand_name = '砂'

    # 动态确定土名（按IP）
    # V3.0.3（Agent1-13）：塑性指数边界取严格大于——GB 50021 表3.3.5/表3.3.4：
    # 黏土 IP>17、粉质黏土 10<IP≤17、粉土 IP≤10（IP=10/17 恰属下档）
    if ip > 17:
        clay_name = '黏土'
    elif ip > 10:
        clay_name = '粉质黏土'
    else:
        clay_name = '粉土'
    if sand_pct > 50 and fine_pct >= 15:
        return f'含{clay_name}{sand_name}'
    # 粉黏粒为主（fine>50%），砂≥25% → 含X砂 X
    elif fine_pct > 50 and sand_pct >= 25:
        return f'含{sand_name}{clay_name}'
    return None


def classify_soil(sand_pct, silt_pct, clay_pct, ip=None, wl=None, proj_type='B',
                  r2_05=None, r05_025=None, r025_0075=None, r20_2=None, r0075=None):
    """按 TB10077 / TB10102 / DBJ15-31 综合定名（含砂土精确命名和混合土定名）

    统一输出"黏"字（黏土/粉质黏土），与数据库/TOML 岩土分类命名一致；
    >2mm 含量≥50% 判"碎石土"；黏粒>30% 但 IP<10 判"粉土"（不再返回黏土）。
    """
    # 无颗分数据 → 仅凭IP定名
    if sand_pct is None and silt_pct is None and clay_pct is None:
        if ip is not None:
            # V3.0.3（Agent1-13）：IP 边界严格大于——GB 50021 表3.3.5/表3.3.4：
            # 黏土 IP>17、粉质黏土 10<IP≤17、粉土 IP≤10（IP=10/17 恰属下档）
            if ip > 17: return '黏土'
            if ip > 10: return '粉质黏土'
            if ip > 0: return '粉土'
        return ''

    fine = (silt_pct or 0) + (clay_pct or 0)
    gravel = 100 - (sand_pct or 0) - fine if sand_pct is not None else 0
    coarse_total = (sand_pct or 0) + gravel

    # 细粒土为主（fine ≥ 50%）
    if fine >= 50:
        mixed = _classify_mixed_soil(sand_pct, fine, ip, gravel,
                                     r2_05, r05_025, r025_0075, r20_2)
        if mixed:
            return mixed

        if clay_pct is not None and clay_pct > 30:
            if ip is None:
                return '黏土'
            if ip > 17: return '黏土'
            if ip > 10: return '粉质黏土'
            return '粉土'  # IP≤10 且黏粒高 → 粉土（塑性指数不支持黏土定名）
        if silt_pct is not None and silt_pct > 50:
            return '粉土' if ip is None or ip <= 10 else '粉质黏土'
        if clay_pct is not None and clay_pct > 10:
            # V3.0.3（Agent1-13）：IP 边界严格 >（黏土 IP>17、粉质黏土 10<IP≤17、粉土 IP≤10）；
            # IP 缺失时保持既有默认（粉质黏土），不做推断
            if ip is None:
                return '粉质黏土'
            if ip > 17: return '黏土'
            if ip > 10: return '粉质黏土'
            return '粉土'
        return '粉土'

    # 粗粒土为主（coarse > fine）
    elif gravel >= 50:
        mixed = _classify_mixed_soil(sand_pct, fine, ip, gravel,
                                     r2_05, r05_025, r025_0075, r20_2)
        # >2mm 含量≥50% 属碎石土（GB/T 50145），非砾砂
        return mixed if mixed else '碎石土'
    elif coarse_total > fine:
        mixed = _classify_mixed_soil(sand_pct, fine, ip, gravel,
                                     r2_05, r05_025, r025_0075, r20_2)
        if mixed:
            return mixed
        # 砾砂（砾粒≥25%，GB/T 50145：>2mm 含量 25%~50% 判砾砂；≥50% 已在上方判碎石土）
        if gravel >= 25:
            return '砾砂'
        # 砂土精确命名
        if r2_05 is not None or r05_025 is not None or r025_0075 is not None:
            detail = _classify_sand_detail(r2_05, r05_025, r025_0075, r20_2, r0075)
            if detail != '砂土':
                return detail
        return '砂土'
    return '混合土'


def il_to_plasticity(il: float, proj_type: str) -> str:
    """根据液性指数 IL 返回可塑性状态"""
    mp = IL_PLASTICITY_A if proj_type == 'A' else IL_PLASTICITY_B
    eps = IL_EPSILON
    for lo, hi, state in mp:
        if lo - eps < il <= hi + eps:
            return state
    return '流塑' if il > 1 else '坚硬'


def spt_to_plasticity(n: float, proj_type: str = 'A', max_plasticity: str = None) -> str:
    """根据标贯 N 值返回黏性土可塑性状态，可选最大塑性状态封顶

    n 支持浮点（杆长修正击数可为小数，如 15.4），按闭区间浮点直判，
    与 rule_engine 的修正击数优先口径完全一致（复评 P3：不再 int() 截断，
    避免 N_corr∈(15,16)/(20,21) 时土工统计与规则复核状态不一致）。

    V3.0.3（Agent1-2）注释：max_plasticity 默认"硬塑"封顶（TOML「A类.塑性状态.
    最大塑性上限」）为【项目设定】——A 类最大塑性上限=硬塑（残积土等场景
    封顶，N 判出"坚硬"的黏土层按硬塑处理），非规范条款；B 类 4 档（无可塑）
    同样封顶硬塑（TOML 已标注"项目特色，勿改"），均勿按规范表"修正"回坚硬。
    """
    mp = SPT_PLASTICITY_A if proj_type == 'A' else SPT_PLASTICITY_B
    eps = IL_EPSILON
    state = '坚硬'
    for lo, hi, d in mp:
        if lo - eps < n <= hi + eps:
            state = d
            break
    if max_plasticity and PLASTICITY_ORDER.get(state, 0) > PLASTICITY_ORDER.get(max_plasticity, 99):
        return max_plasticity
    return state


def spt_to_weathering(n: float, proj_type: str = 'A') -> str:
    """根据标贯 N 值返回岩石风化程度（读取 SPT_WEATHERING_A/B 常量表）

    n 支持浮点（杆长修正击数可为小数，如 30.9/50.5），按闭区间浮点直判，
    与 spt_to_plasticity 口径完全一致（V2.2.2 C1：不再 int() 截断，
    避免 N_corr∈(30,31)/(50,51) 时风化判定误报+漏报）。

    V3.0.3（Agent1-3）注释：当前边界为【经验表】——《广东省土层判定指标》
    4.1 花岗岩类（残积≤30 / 全风化 30~50 / 强风化 50~70 / 中风化≥70，
    N 为未经杆长修正的实测击数）；正式条文 DBJ15-31-2016 第4.2.3条注1
    （花岗岩类：残积 N'<40 / 全风化 40≤N'<70 / 强风化 N'≥70，其他岩石按
    国标 30/50 分界）可通过 TOML「A类.标贯N_风化程度」切换，勿直接改代码。
    """
    n = n if n is not None else 0
    eps = IL_EPSILON
    mp = SPT_WEATHERING_A if proj_type == 'A' else SPT_WEATHERING_B
    for lo, hi, d in mp:
        if lo - eps < n <= hi + eps:
            return d
    return '强风化'


def _coerce_spt_n(v):
    """击数入参数值化防御（供 spt_to_density/dpt_to_density 共用）

    V2.2.3（S6）：None/非数值/NaN → None（调用方按"无匹配/数据缺失"跳过，
    不再抛 TypeError）；数值字符串转 float（与 rule_engine._safe_spt 口径一致）。
    soil_stats / soil_stats_v2 直接调用路径同样受此内部防御保护。
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    return None if math.isnan(f) else f


def spt_to_density(n: float):
    """根据标贯 N 值返回密实度（支持小数修正击数）

    区间按 TOML 键闭区间语义解析（相邻键须由配置保证衔接，如 ≤10 + 10~15 + ≥30）；
    无任何区间命中（键之间存在间隙）返回 None（调用方应跳过/安全处理），
    不再返回兜底"密实"（避免修正击数落入空隙被误判为密实）。
    None/非数值/NaN 入参同样返回 None（V2.2.3 S6 防御，不抛 TypeError）。
    """
    n = _coerce_spt_n(n)
    if n is None:
        return None
    eps = IL_EPSILON
    for lo, hi, d in SPT_DENSITY_MAP:
        if lo - eps < n <= hi + eps:
            return d
    return None


def dpt_to_density(n: float):
    """根据动探 N63.5 返回密实度（支持小数修正值）

    同 spt_to_density：区间按 TOML 键闭区间语义，无匹配返回 None（不再兜底"密实"）；
    None/非数值/NaN 入参同样返回 None（V2.2.3 S6 防御，不抛 TypeError）。
    """
    n = _coerce_spt_n(n)
    if n is None:
        return None
    eps = IL_EPSILON
    for lo, hi, d in DPT_DENSITY_MAP:
        if lo - eps < n <= hi + eps:
            return d
    return None


def fmt_label(tczcbh, tcycbh):
    """格式化地层编号标签"""
    if not tczcbh: return ''
    if tcycbh: return f'{tczcbh}-{tcycbh}'
    return str(tczcbh)

# =============================================================================
# 规则配置覆盖自检（供 Agent A 启动自检调用）
# =============================================================================
# 规则默认值（与 rule_engine._build_rule_registry 的 base 表同源维护）。
# 若新增规则，请同步在 TOML ["规则"] 段登记，保证可启用/禁用以避免双源漂移。
_RULE_DEFAULTS = {
    'R-CHK-001': ('H', '颜色字段为空'),
    'R-CHK-002': ('H', '岩石地层风化程度为空'),
    'R-CHK-003': ('H', '描述含超出本层范围的深度'),
    'R-DEN-001': ('H', '标贯N值→密实度不符'),
    'R-DEN-002': ('H', '动探N63.5→密实度不符'),
    'R-DEN-003': ('L', '有密实度标注但该层无标贯/动探'),
    'R-DEN-004': ('H', '黏性土不应标注密实度'),
    'R-DEN-005': ('H', '砂土缺少密实度'),
    'R-DEN-006': ('H', '碎石土缺少密实度'),
    'R-DEN-009': ('H', '砂土/碎石土/填土缺少湿度描述'),
    'R-DEN-007': ('H', '标贯N→可塑性不符'),
    'R-DEN-008': ('H', '标贯N→风化程度不符'),
    'R-DEN-010': ('M', '深部密实度比上层更松（纵向顺序检查）'),
    'R-CRS-001': ('H', '淤泥不应标注密实度'),
    'R-CRS-010': ('M', '盐渍土应注明含盐量'),
    'R-CRS-011': ('M', '冻土应注明含冰量'),
    'R-CRS-012': ('M', '标准地层N值不达标'),
    'R-PLS-001': ('H', '参数表塑性状态与层位不符'),
    'R-PLS-002': ('M', 'IL试验结果与层位状态不符'),
    'R-CRS-002': ('M', '固结试验孔隙比异常偏高'),
    'R-CRS-003': ('M', '固结试验孔隙比反常(极高)'),
    'R-GRS-001': ('M', '颗分定名与地层岩土名称不符'),
}


def rule_config_coverage_report():
    """返回规则注册表与 TOML ["规则"] 段的覆盖差异清单（供启动自检）

    返回列表，每项为 dict：
      {'rule_id': str, 'type': 'missing'|'level_mismatch'|'desc_mismatch',
       'default': ..., 'toml': ...}
    - missing：代码注册表有该规则但 TOML 无条目（无法通过 TOML 启停/改等级）
    - level_mismatch / desc_mismatch：代码默认与 TOML 不一致（漂移告警）
    TOML 缺失/加载失败/解析异常时返回空列表（防御式兼容，不影响启动）。
    """
    try:
        cfg = load_project_config()
        if not isinstance(cfg, dict):
            return []
        rules_cfg = cfg.get('规则', {})
        if not isinstance(rules_cfg, dict):
            return []
        report = []
        for rid, (level, desc) in _RULE_DEFAULTS.items():
            rc = rules_cfg.get(rid)
            if not isinstance(rc, dict):
                report.append({'rule_id': rid, 'type': 'missing',
                               'default': level, 'toml': None})
                continue
            toml_level = rc.get('等级', level)
            if toml_level != level:
                report.append({'rule_id': rid, 'type': 'level_mismatch',
                               'default': level, 'toml': toml_level})
            # 说明以 TOML 为准（用户可自由改写）；仅当 TOML 未提供说明时告警
            toml_desc = rc.get('说明')
            if not toml_desc or not str(toml_desc).strip():
                report.append({'rule_id': rid, 'type': 'desc_mismatch',
                               'default': desc, 'toml': toml_desc})
        return report
    except Exception:
        return []
