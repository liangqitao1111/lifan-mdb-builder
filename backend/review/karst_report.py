"""理反 — 岩溶统计报告生成模块
从 MDB 数据直接生成附表7/8，复刻 V1.4.9.1 输出格式。
Lifan 直接 import 调用，无需 subprocess。

一致性清单 A2 同步（桌面 V3.1.11《9月新版表头脚本.py》，2026.09）：
generate_karst_report(da, out_dir, header_version='new')
  - 'new' → 新表头 岩溶发育统计表（15列 A-O，参数/溶洞统计模板（新）.xlsx），
            输出文件名"岩溶发育统计表（新表头）.xlsx"
  - 'old' → 旧表头 附表7 岩溶率统计表（20列，与原版完全一致）
附表8 溶洞统计表两种模式均照常输出。
"""
import os, re
from collections import OrderedDict
from config import load_project_config, CAVITY_TYPES

# ============================================================
# 常量（从 TOML + config 加载）
# ============================================================
CAVE_TYPES = CAVITY_TYPES
_kr_kw = load_project_config().get('岩溶_关键词', {})
CARBONATE_TYPES = set(_kr_kw.get('可溶岩', ['灰岩', '炭质灰岩', '岩溶化灰岩']))
SOLUBLE_ROCK_TYPES = CAVE_TYPES | CARBONATE_TYPES

CAVE_SKIP_WORDS = set(_kr_kw.get('填充物跳过词',
    ['串珠状','溶蚀裂隙','溶蚀','溶孔','溶隙','溶沟','溶槽','溶蚀面']))
CAVE_SKIP_PREFIX = set(_kr_kw.get('填充物跳过前缀', ['串珠状','溶蚀']))
INVALID_FD = set(_kr_kw.get('无效填充物',
    ['无','空洞','充填物','填充物','充填','填充','未充填','未填充','半填充','半充填']))
_fill_map = _kr_kw.get('岩土_充填', {'全填充':'全充填','半填充':'半充填','无填充':'无充填'})


def extract_pier(hole_id):
    """从孔号提取墩台号

    孔号格式：
      26-ZD-JCXT-2-1       → 工点=26-ZD-JCXT, 墩台=2, 孔号=1
      26-ZD-JCXT-150       → 工点=26-ZD-JCXT, 墩台=150 (无孔号)
      26-ZD-JCXT-70X       → 工点=26-ZD-JCXT, 墩台=70, 孔号=X(无分隔符)
    """
    if not hole_id: return None
    parts = str(hole_id).split('-')
    n = len(parts)

    # 找工点名（全字母段，如 JCXT）
    workpoint_idx = None
    for i, p in enumerate(parts):
        if p.isalpha() and len(p) >= 2:
            workpoint_idx = i
    if workpoint_idx is None:
        return hole_id  # 无法识别工点

    # 工点 = 前 workpoint_idx+1 段
    workpoint = '-'.join(parts[:workpoint_idx + 1])
    remaining = parts[workpoint_idx + 1:]

    if not remaining:
        return workpoint  # 仅工点，无墩台号

    # 第一个剩余段就是墩台号
    raw_pier = remaining[0]
    pier_num = None
    # 纯数字：2, 150, 0 → "2", "150", "0"
    m = re.match(r'^(\d+)$', raw_pier)
    if m:
        pier_num = m.group(1)
    # 字母+数字：Y84 → "Y84"
    if not pier_num:
        m = re.match(r'^([a-zA-Z]+\d+)$', raw_pier)
        if m:
            pier_num = m.group(1)
    # 数字+字母（无分隔符）：70X → "70"
    if not pier_num:
        m = re.match(r'^(\d+)', raw_pier)
        if m:
            pier_num = m.group(1)
    if pier_num:
        return f'{workpoint}-{pier_num}'
    return hole_id


def extract_filling_from_tcms(desc, rock_name='溶洞'):
    """从 tcms 描述智能提取填充物特征（V1.8.2 重写：放宽匹配，非硬编码）

    策略：先识别填充状态（全/半/无填充/未明确），再剥离"溶洞/土洞"和状态词前缀，
    取冒号（或中文冒号）后第一个逗号前的内容作为包含物描述。
    若提取到非充填物描述的干扰词（如'串珠状'），自动跳过取下一段。

    返回: (filling_str, has_fd, fs)
      - filling_str: 用于 S 列的完整字符串
      - has_fd: 是否包含具体包含物描述（用于决定 T 列是否填原始描述）
      - fs: 填充状态 ('全填充' / '半填充' / '无填充' / '未明确')
    """
    desc = (desc or '').strip()
    if not desc:
        return '', False, '未明确'

    # 根据 tcms 实际提到的洞类型决定 ct（防止 rock_name 与实际不符）
    if '土洞' in desc: ct = '土洞'
    elif '溶洞' in desc: ct = '溶洞'
    else: ct = '土洞' if '土洞' in rock_name else '溶洞'

    if '全填充' in desc or '全充填' in desc: fs = '全填充'
    elif '半填充' in desc or '半充填' in desc: fs = '半填充'
    elif '无填充' in desc or '无充填' in desc: fs = '无填充'
    else: fs = '未明确'

    if fs == '无填充':
        return f'{ct}无填充', False, fs
    if fs == '未明确':
        return f'{ct}（{fs}）', False, fs

    # 提取冒号后内容
    if '：' in desc:
        content = desc.split('：', 1)[1].strip()
    elif ':' in desc:
        content = desc.split(':', 1)[1].strip()
    else:
        content = desc

    # 剥离前导引导词（循环直到稳定）：
    # 1. '[全/半][充填]+'（如"全充填"/"半充填"/"全填充"/"半填充"）
    # 2. '溶洞'/'土洞' 前缀
    # 3. 前导标点
    for _ in range(3):
        prev = content
        content = re.sub(r'^[全半][充填]+\s*', '', content)
        content = re.sub(r'^[土溶]洞\s*', '', content)
        content = re.sub(r'^[，,。;；、\s]+', '', content)
        if content == prev:
            break

    # 找第一个 '，'/'。'/'；'/'、' 前的内容（跳过非充填物描述的干扰词）
    fd = ''
    remaining = content
    for _ in range(5):  # 最多尝试 5 段
        if not remaining:
            break
        # 先剥离前导标点和状态词（交替剥离，确保干净）
        remaining = re.sub(r'^[，,。;；、\s]+', '', remaining)
        remaining = re.sub(r'^[全半][充填]+\s*', '', remaining)
        remaining = re.sub(r'^[土溶]洞\s*', '', remaining)
        remaining = re.sub(r'^[，,。;；、\s]+', '', remaining)
        if not remaining:
            break
        # 取第一个段
        m = re.search(r'([^，,。;；、]+)', remaining)
        if not m:
            break
        segment = m.group(1).strip()
        # 检查是否为非充填物描述的干扰词
        if (segment in CAVE_SKIP_WORDS or
            any(segment.startswith(p) for p in CAVE_SKIP_PREFIX) or
            re.match(r'^\d+\.?\d*-?\d*\.?\d*m$', segment)):
            remaining = remaining[m.end():]
            continue
        fd = segment
        break

    if fd:
        # 清理常见无用前缀
        fd = re.sub(r'^(?:填充物|充填物)[为以是]?\s*', '', fd)
        fd = re.sub(r'^(?:为|以|是|含|夹)\s*', '', fd)
        # 清理块径/钻进等无用信息
        fd = re.sub(r'[，,;；]?\s*(?:漏水|渗水|涌水|钻进时|掉钻|块径|粒径).*', '', fd).strip()

    # 判定是否真正提取到了具体包含物描述
    # V2.2.4 H9：允许单字填充物（如'泥'/'砂'）——最小长度 1（此前 len>=2 会丢
    # '全充填砂'/'溶洞全填充：泥' 的填充物）；无效词（'无'等，含单字）仍由 INVALID_FD 过滤
    has_fd = bool(fd) and fd not in INVALID_FD

    if fs == '全填充':
        filling = f'{ct}全填充，填充物为{fd}' if has_fd else f'{ct}全填充'
    elif fs == '半填充':
        filling = f'{ct}半填充，填充物为{fd}' if has_fd else f'{ct}半填充'
    else:
        filling = f'{ct}（{fs}）'

    return filling, has_fd, fs


def _make_bin_fn(thresholds):
    """根据阈值列表构建分段函数（用于 STAT_VARS 的 fn）"""
    def fn(val):
        for i in range(1, len(thresholds)):
            if val <= thresholds[i]:
                return i - 1
        return len(thresholds) - 1
    fn.__name__ = 'bin_fn'
    return fn


def _make_bins(prefix_label, thresholds):
    """根据阈值列表构建显示标签列表"""
    bins = [f'{prefix_label}≤{thresholds[1]}']
    for i in range(2, len(thresholds)):
        bins.append(f'{thresholds[i-1]}<{prefix_label}≤{thresholds[i]}')
    bins.append(f'{prefix_label}>{thresholds[-1]}')
    return bins


# 附表8 统计维度常量（从 TOML 加载；P1-⑤：可经 reload_from_config 重建）
_kheight_thresholds = [0, 1, 5, 10]
_kdepth_thresholds = [0, 10, 30, 50]
_kline_weak = 5.0
_kline_med = 20.0
_karea_weak = 15.0
_karea_med = 45.0


def _build_karst_constants():
    """重建模块级岩溶统计派生常量（import 时与配置保存后各调用一次）"""
    global _kheight_thresholds, _kdepth_thresholds
    global _kline_weak, _kline_med, _karea_weak, _karea_med
    global _LINE_WEAK, _LINE_MEDIUM, _AREA_WEAK, _AREA_MEDIUM
    global STAT_VARS
    _karst_cfg = load_project_config().get('岩溶统计', {})
    _kheight_thresholds = _karst_cfg.get('洞高阈值', [0, 1, 5, 10])
    _kdepth_thresholds = _karst_cfg.get('埋深阈值', [0, 10, 30, 50])
    try:
        _kline_weak = float(_karst_cfg.get('线岩溶率_弱发育', 5))
        _kline_med = float(_karst_cfg.get('线岩溶率_中等发育', 20))
        _karea_weak = float(_karst_cfg.get('见洞隙率_弱发育', 15))
        _karea_med = float(_karst_cfg.get('见洞隙率_中等发育', 45))
    except (TypeError, ValueError):
        # P2-6（Codex 复核）：配置异常值显式回退默认，避免 _LINE_WEAK 等别名 NameError
        _kline_weak = 5.0
        _kline_med = 20.0
        _karea_weak = 15.0
        _karea_med = 45.0
    # 发育程度判定阈值别名（judge_development 引用）——必须随重建一起刷新，
    # 否则热重载后 judge_development 仍用旧阈值（Codex 复核 P1-3）
    _LINE_WEAK = _kline_weak
    _LINE_MEDIUM = _kline_med
    _AREA_WEAK = _karea_weak
    _AREA_MEDIUM = _karea_med
    STAT_VARS = {
        'height': {'label': '溶洞高度H（m）', 'bins': _make_bins('H', _kheight_thresholds),
                   'fn': _make_bin_fn(_kheight_thresholds)},
        'depth':  {'label': '发育深度L（m）', 'bins': _make_bins('L', _kdepth_thresholds),
                   'fn': _make_bin_fn(_kdepth_thresholds)},
        'fill':   {'label': '溶洞充填程度',    'bins': ['无充填', '半充填', '全充填'],
                   'fn': None},  # 特殊：按岩土名称推断
    }


def reload_from_config():
    """配置保存后重建本模块派生常量（review_api._reload_config_modules 调用）"""
    _build_karst_constants()


_build_karst_constants()

# 发育程度判定阈值（DBJ/T 15-136-2018 表3.1.4，从 TOML 加载）
#   线岩溶率(%)：弱<5、中5~20、强>20；钻孔见洞隙率(%)：弱<15、中15~45、强>45
#   判定规则：三个指标中从高到低有 1 个达标即定为该等级
#   （本项目仅用 线岩溶率 + 钻孔见洞率 两项，地表岩溶发育密度不参与）

def _classify_fill(name):
    """按岩土名称判断充填类型

    兼容两套命名：溶洞1/2/3、土洞1/2/3（理正旧命名）与
    溶洞无填充/半填充/全填充（含"充填"变体）——后一类此前整类落入"未明确"漏计。
    """
    if name in ('溶洞1', '土洞1'): return '无充填'
    if name in ('溶洞2', '土洞2'): return '半充填'
    if name in ('溶洞3', '土洞3'): return '全充填'
    for kw in ('无填充', '无充填'):
        if kw in name: return '无充填'
    for kw in ('半填充', '半充填'):
        if kw in name: return '半充填'
    for kw in ('全填充', '全充填'):
        if kw in name: return '全充填'
    return '未明确'


def judge_development(line_rate_pct, cave_hole_rate_pct):
    """判定岩溶发育程度（DBJ/T 15-136-2018 表3.1.4，阈值从 TOML 加载）

    参数：line_rate_pct=线岩溶率(%)，cave_hole_rate_pct=钻孔见洞隙率(%)。
    规则（三个指标从高到低有 1 个达标即定为该等级；本项目仅用这两项）：
      - 强烈发育：线岩溶率 >20 或 见洞隙率 >45
      - 中等发育：线岩溶率 ≥5 或 见洞隙率 ≥15
      - 弱发育：两者均低于弱发育下限（线 <5 且 见洞 <15）
    """
    # 阈值每次运行时读 TOML「岩溶统计」（参数中心修改即时生效，v42）
    _cfg = load_project_config().get('岩溶统计', {})
    _line_weak = float(_cfg.get('线岩溶率_弱发育', 5))
    _line_med = float(_cfg.get('线岩溶率_中等发育', 20))
    _area_weak = float(_cfg.get('见洞隙率_弱发育', 15))
    _area_med = float(_cfg.get('见洞隙率_中等发育', 45))
    if line_rate_pct > _line_med or cave_hole_rate_pct > _area_med:
        return '岩溶强烈发育'
    if line_rate_pct >= _line_weak or cave_hole_rate_pct >= _area_weak:
        return '岩溶中等发育'
    return '岩溶弱发育'


def _build_cave_stats(da):
    """构建溶洞统计表数据（与 V1.4.9.1 build_cave_stats 一致）
    返回 dict 或 None（无溶洞数据）
    """
    caves = []
    for b in da.get_all_boreholes():
        if b['zksd'] <= 0: continue
        prev_depth = 0.0
        for s in da.get_strata('', b['zkbh']):
            name = s.get('tcymc', '')
            bottom = s.get('tccdsd', 0)
            # V2.2.2 K1：厚度口径与附表7 统一——tchd 缺失/为 0 时回退 bottom-prev_depth
            # （以溶洞记录行存在为准），附表8 与附表7 条数不再不一致
            thick_raw = s.get('tchd')
            # P2-5（Codex 复核）：tchd 显式 0 = 零厚溶洞行 → 剔除（thick>0 过滤生效）；
            # 仅当 tchd 缺失（None）时才回退 bottom-prev 厚度口径
            thick = thick_raw if thick_raw is not None else (bottom - prev_depth)
            if name in CAVE_TYPES and thick > 0:
                top = bottom - thick
                caves.append({'name': name, 'height': thick, 'depth': top,
                              'fill': _classify_fill(name)})
            prev_depth = bottom
    if not caves:
        return None

    total = len(caves)

    # 溶洞高度分布
    h_counts = [0, 0, 0, 0]
    for c in caves:
        h_counts[STAT_VARS['height']['fn'](c['height'])] += 1

    # 发育深度分布
    d_counts = [0, 0, 0, 0]
    for c in caves:
        d_counts[STAT_VARS['depth']['fn'](c['depth'])] += 1

    # 充填程度分布（洞穴/空洞/岩溶化灰岩等无法判断者归入"未明确"，单列避免占比虚低）
    fill_order = ['无充填', '半充填', '全充填']
    f_counts = [sum(1 for c in caves if c['fill'] == ft) for ft in fill_order]
    unk_count = total - sum(f_counts)
    if unk_count > 0:
        fill_order = fill_order + ['未明确']
        f_counts = f_counts + [unk_count]

    return {
        'height_bins': STAT_VARS['height']['bins'],
        'height_counts': h_counts,
        'height_pcts': [round(c / total, 4) if total > 0 else 0 for c in h_counts],
        'depth_bins': STAT_VARS['depth']['bins'],
        'depth_counts': d_counts,
        'depth_pcts': [round(c / total, 4) if total > 0 else 0 for c in d_counts],
        'fill_bins': fill_order,
        'fill_counts': f_counts,
        'fill_pcts': [round(c / total, 4) if total > 0 else 0 for c in f_counts],
        'total_caves': total,
    }


def _pier_sort_key(name):
    """墩台号排序：按首个连续数字升序（同墩台按后续钻孔号继续排）"""
    s = str(name)
    nums = re.findall(r'\d+', s)
    if not nums:
        return (0, 0, s)
    primary = int(nums[0])
    secondary = int(nums[1]) if len(nums) > 1 else 0
    tertiary = int(nums[2]) if len(nums) > 2 else 0
    return (primary, secondary, tertiary)


# ============================================================
# 主处理流程
# ============================================================
def generate_karst_report(da, out_dir, header_version='new'):
    """生成岩溶统计表和附表8，返回 (table_path, table8_path)

    一致性清单 A2（桌面 V3.1.11 三选一弹窗的 Web 实现）：
    header_version='new'（默认）：新表头 岩溶发育统计表（15列，溶洞统计模板（新）.xlsx）
    header_version='old'：旧表头 附表7 岩溶率统计表（与原版完全一致）
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Side, Border

    # ---- 1. 加载数据 ----
    boreholes = [b for b in da.get_all_boreholes() if b['zksd'] > 0]
    all_strata = {}
    for b in boreholes:
        all_strata[b['zkbh']] = da.get_strata('', b['zkbh'])

    # ---- 2. 构建输出行 ----
    output_rows = []
    for b in boreholes:
        zkbh = b['zkbh']
        strata = all_strata[zkbh]
        zkbg = b['zkbg']
        zksd = b['zksd']
        zklc = b.get('zklc', 0) or 0
        zkpil = b.get('zkpil', 0) or 0
        pier = extract_pier(zkbh)

        prev_depth = 0.0
        soluble_thick = 0.0
        cave_thick = 0.0
        cave_rows = []
        bedrock_depth = None

        for s in strata:
            name = s.get('tcymc', '')
            bottom = s.get('tccdsd', 0)
            thick_raw = s.get('tchd')
            # P2-5（Codex 复核）：tchd 显式 0 = 零厚溶洞行 → 剔除（thick>0 过滤生效）；
            # 仅当 tchd 缺失（None）时才回退 bottom-prev 厚度口径
            thick = thick_raw if thick_raw is not None else (bottom - prev_depth)

            if name in SOLUBLE_ROCK_TYPES:
                soluble_thick += thick
                if bedrock_depth is None:
                    bedrock_depth = prev_depth

            # V2.2.2 K1：与附表8 同口径——仅有效溶洞记录（厚度>0）计入
            # V2.2.2 K2：高程 0 真值——地面标高 zkbg=0 时仍计算高程（is not None 判定）
            if name in CAVE_TYPES and thick > 0:
                top = bottom - thick
                top_elev = zkbg - top if zkbg is not None else None
                bottom_elev = zkbg - bottom if zkbg is not None else None
                desc = s.get('tcms', '')
                filling, has_fd, fs = extract_filling_from_tcms(desc, name)
                # 原始描述列：仅当 半/全填充 且无具体包含物描述时，填入该层 tcms
                raw_desc = desc if (not has_fd and fs in ('半填充', '全填充')) else None
                cave_rows.append({
                    'top': round(top, 2), 'bottom': round(bottom, 2),
                    'top_elev': round(top_elev, 2) if top_elev is not None else None,
                    'bottom_elev': round(bottom_elev, 2) if bottom_elev is not None else None,
                    'thick': round(thick, 2), 'filling': filling,
                    'desc': raw_desc,
                })
                cave_thick += thick

            prev_depth = bottom

        # 溶洞按顶板深度从小到大排序
        cave_rows.sort(key=lambda cr: cr['top'])

        # V3.0.3（S5）：线岩溶率输出单位统一为百分比数值（×100，一位小数），与表头
        # "线岩溶率(%)"、内部判定（DBJ/T 15-136-2018 表3.1.4，阈值 5/20）口径一致；
        # 此前输出小数（0.39=39%）易被误读为 0.39%（合规审查 Agent3-S5）。
        karst_rate_val = round(cave_thick / soluble_thick * 100, 1) if soluble_thick > 0 else 0

        # 无可溶岩的孔跳过（不输出、不参与统计）
        if soluble_thick <= 0:
            continue

        if cave_rows:
            for idx, cr in enumerate(cave_rows):
                is_first = (idx == 0)
                output_rows.append({
                    '墩台号': pier if is_first else None,
                    '钻孔编号': zkbh if is_first else None,
                    '里程': round(zklc, 2) if is_first and zklc else None,
                    '偏移量': round(zkpil, 2) if is_first and zkpil else None,
                    '地面标高': round(zkbg, 2) if is_first else None,
                    '孔深': round(zksd, 2) if is_first else None,
                    '基岩埋深': round(bedrock_depth, 2) if is_first and bedrock_depth is not None else None,
                    '可溶岩累计厚度': round(soluble_thick, 2) if is_first and soluble_thick > 0 else None,
                    '溶洞顶板深度': cr['top'],
                    '溶洞底板深度': cr['bottom'],
                    '溶洞顶板高程': cr['top_elev'],
                    '溶洞底板高程': cr['bottom_elev'],
                    '溶洞高度': cr['thick'],
                    '溶洞累计厚度': round(cave_thick, 2),
                    '线岩溶率': karst_rate_val,
                    '溶洞充填物特征': cr['filling'],
                    '原始描述': cr['desc'],
                })
        else:
            output_rows.append({
                '墩台号': pier, '钻孔编号': zkbh,
                '里程': round(zklc, 2) if zklc else None,
                '偏移量': round(zkpil, 2) if zkpil else None,
                '地面标高': round(zkbg, 2), '孔深': round(zksd, 2),
                '基岩埋深': round(bedrock_depth, 2) if bedrock_depth is not None else None,
                '可溶岩累计厚度': round(soluble_thick, 2) if soluble_thick > 0 else None,
                '溶洞顶板深度': None, '溶洞底板深度': None,
                '溶洞顶板高程': None, '溶洞底板高程': None,
                '溶洞高度': None, '溶洞累计厚度': 0.0, '线岩溶率': 0,
                '溶洞充填物特征': '无溶洞', '原始描述': None,
            })

    # ---- 2.5 墩台号简化（仅保留数字，去除工点前缀）----
    for r in output_rows:
        p = r.get('墩台号')
        if p:
            parts = str(p).split('-')
            r['墩台号'] = parts[-1]  # 最后一段就是墩台数字

    # ---- 3. 计算墩台统计 ----
    # P1-8 修复：同一钻孔的每一行溶洞记录都携带全孔"溶洞累计厚度"，
    # 聚合时按钻孔只计一次（该孔首行），否则多溶洞孔被累加 N 次（如 6 条→105m 而非 17.5m）。
    pier_data = {}
    for row in output_rows:
        p = row.get('墩台号')
        if p is None:
            continue
        hid = row.get('钻孔编号')
        ct = row.get('溶洞累计厚度') or 0
        st = row.get('可溶岩累计厚度') or 0
        if p not in pier_data:
            pier_data[p] = {'cave': 0.0, 'soluble': 0.0,
                            'total_holes': set(), 'cave_holes': set(),
                            'counted_holes': set()}
        if hid:
            pier_data[p]['total_holes'].add(hid)
            # 仅该孔首行（钻孔编号非空的行）才累加累计厚度，避免重复累加
            if hid not in pier_data[p]['counted_holes']:
                pier_data[p]['counted_holes'].add(hid)
                pier_data[p]['cave'] += ct
                pier_data[p]['soluble'] += st
        # V2.2.2 B1：见洞判定与 A 类口径一致——存在溶洞记录行（溶洞顶板深度非空）即视为见洞，
        # 不再依赖"溶洞充填物特征"文本真值（tcms 为空的溶洞孔填充物特征为 ''，旧逻辑漏计）。
        # 保留附表7"无溶洞"标记与填充物特征列特色（在输出行构造处，不受本判定影响）。
        if row.get('溶洞顶板深度') is not None and hid:
            pier_data[p]['cave_holes'].add(hid)

    pier_rates = {}
    pier_cave_hole_rates = {}
    pier_development = {}
    for pier, d in pier_data.items():
        # V3.0.3（S5）：墩台线岩溶率统一为百分比数值（×100，一位小数），与 Q 列
        # 见洞率（已为百分比）及 judge_development 的 % 入参同口径（合规审查 Agent3-S5）。
        pier_rates[pier] = round(d['cave'] / d['soluble'] * 100, 1) if d['soluble'] > 0 else 0
        total = len(d['total_holes'])
        cave_count = len(d['cave_holes'])
        pier_cave_hole_rates[pier] = round(cave_count / total * 100, 1) if total > 0 else 0
        pier_development[pier] = judge_development(pier_rates[pier], pier_cave_hole_rates[pier])

    # ---- 4. 写主表（新表头 / 旧表头 二选一；数据行构造与校验口径完全相同）----
    # A2：与桌面 V3.1.11 _ask_karst_header_version 三选一弹窗对齐（Web 经 API 参数传入）
    if header_version == 'new':
        table7_path = os.path.join(out_dir, '岩溶发育统计表（新表头）.xlsx')
        _write_table7_new(output_rows, table7_path)
    else:
        table7_path = os.path.join(out_dir, '附表7 岩溶率统计表.xlsx')
        _write_table7(output_rows, pier_rates, pier_cave_hole_rates, pier_development, table7_path)

    # ---- 5. 构建溶洞统计数据并写附表8 ----
    cave_stats = _build_cave_stats(da)
    table8_path = os.path.join(out_dir, '附表8 溶洞统计表.xlsx')
    _write_table8(cave_stats, table8_path)

    return table7_path, table8_path


def _write_table7(rows, pier_rates, pier_cave_hole_rates, pier_development, path):
    """写入附表7（使用参数文件夹内模板 岩溶率统计表.xlsx）"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, Side, Border

    # 加载模板（Web 适配：backend/review/ → 仓库根为三级 dirname）
    base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    template_path = os.path.join(base, '参数', '岩溶率统计表.xlsx')
    if not os.path.exists(template_path):
        _write_table7_fallback(rows, pier_rates, pier_cave_hole_rates, pier_development, path)
        return

    wb = openpyxl.load_workbook(template_path)
    ws = wb.active

    data_font = Font(name='宋体', size=11)
    center = Alignment(horizontal='center', vertical='center', wrap_text=False)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 列映射: 模板列号 → 数据key（匹配新模板 19列 A-S + 20列T原始描述）
    TPL_COL = {
        '墩台号': 1,             # A
        '钻孔编号': 2,           # B
        '里程': 3,               # C
        '偏移量': 4,             # D
        '地面标高': 5,           # E
        '孔深': 6,               # F
        '基岩埋深': 7,           # G
        '可溶岩累计厚度': 8,     # H
        '溶洞顶板深度': 9,       # I
        '溶洞底板深度': 10,      # J
        '溶洞顶板高程': 11,      # K
        '溶洞底板高程': 12,      # L
        '溶洞高度': 13,          # M
        '溶洞累计厚度': 14,      # N
        '线岩溶率': 15,          # O
        '溶洞充填物特征': 19,    # S
        '原始描述': 20,          # T（V1.8.2 新增：仅当半/全填充无包含物时填）
    }

    # 删除模板原有数据行，保留表头
    for mc in list(ws.merged_cells.ranges):
        if mc.min_row >= 4:
            ws.unmerge_cells(str(mc))
    for r in range(4, ws.max_row + 1):
        ws.delete_rows(4, ws.max_row - 3)  # 删除第4行及以下所有行
        break

    # 按墩台分组排序（同墩台下钻孔也按钻孔号数字升序）
    pier_groups = OrderedDict()
    current_pier = None
    for row_data in rows:
        p = row_data.get('墩台号')
        if p is not None: current_pier = p
        if current_pier not in pier_groups: pier_groups[current_pier] = []
        pier_groups[current_pier].append(row_data)
    # 组内排序：同钻孔的所有溶洞行必须紧邻。None行继承前一个非None行的钻孔号作为 effective_key
    for pier_name, group_rows in pier_groups.items():
        # 计算 effective_key：None行继承上一行的钻孔号
        prev_key = None
        for r in group_rows:
            if r.get('钻孔编号'):
                prev_key = r['钻孔编号']
        # 用 effective_key 排序
        indexed = list(enumerate(group_rows))
        # 二次遍历构建 effective_key（按原始 idx）
        effective = {}
        prev_k = None
        for orig_idx in range(len(group_rows)):
            r = group_rows[orig_idx]
            k = r.get('钻孔编号') or prev_k
            effective[orig_idx] = k
            if r.get('钻孔编号'):
                prev_k = r['钻孔编号']
        # 排序：按 effective_key 排，None 行的 effective_key 是前一个非 None 行的钻孔号
        indexed.sort(key=lambda t: (_pier_sort_key(effective[t[0]] or ''), t[0]))
        pier_groups[pier_name] = [r for _, r in indexed]
    sorted_rows = []
    for pier_name in sorted(pier_groups.keys(), key=_pier_sort_key):
        sorted_rows.extend(pier_groups[pier_name])

    erow = 4
    num2_fmt = '0.00'
    # V3.0.3（S5）：O/P/Q 列单元格值为百分比数值（如 39.0）而非小数分数（0.39），
    # 与表头 (%) 一致；数字格式用普通一位小数（'0.0'），不再用 '0.00%'（避免二次放大）。
    pct_val_fmt = '0.0'
    NUM2_KEYS = {'里程', '偏移量', '地面标高', '孔深', '基岩埋深',
                  '可溶岩累计厚度', '溶洞顶板深度', '溶洞底板深度',
                  '溶洞顶板高程', '溶洞底板高程', '溶洞高度', '溶洞累计厚度'}

    data_row_map = {}  # sorted_rows index → excel row
    for r_idx, row_data in enumerate(sorted_rows):
        data_row_map[r_idx] = erow
        for data_key, col_idx in TPL_COL.items():
            val = row_data.get(data_key)
            if val is None:
                continue
            c = ws.cell(row=erow, column=col_idx, value=val)
            c.font = data_font; c.alignment = center; c.border = border
            if data_key in NUM2_KEYS and isinstance(val, (int, float)):
                c.number_format = num2_fmt
            elif data_key == '线岩溶率' and isinstance(val, (int, float)):
                c.number_format = pct_val_fmt

        # P(16): 墩台线岩溶率, Q(17): 钻孔见洞率, R(18): 发育程度
        pier = row_data.get('墩台号')
        hid = row_data.get('钻孔编号')
        if pier and hid:
            rate = pier_rates.get(pier)
            if rate is not None:
                c = ws.cell(row=erow, column=16, value=rate)
                c.font = data_font; c.alignment = center; c.border = border; c.number_format = pct_val_fmt
            cave_rate = pier_cave_hole_rates.get(pier)
            if cave_rate is not None:
                # S5：见洞率已为百分比数值（round(...,1)），直接写入（此前 /100 后按 % 格式）
                c = ws.cell(row=erow, column=17, value=cave_rate)
                c.font = data_font; c.alignment = center; c.border = border; c.number_format = pct_val_fmt
            dev = pier_development.get(pier)
            if dev is not None:
                c = ws.cell(row=erow, column=18, value=dev)
                c.font = data_font; c.alignment = center; c.border = border

        erow += 1

    # 合并单元格: 墩台号(A) 和 钻孔编号(B) + 钻孔级信息
    total_rows = erow - 1

    def _merge(rows_list, key_fn, cols):
        prev_k, start = None, None
        for ri, rd in enumerate(rows_list):
            er = data_row_map[ri]; k = key_fn(rd)
            if k is not None and k != prev_k:
                if prev_k is not None and start is not None and er - 1 >= start:
                    for mc in cols: ws.merge_cells(start_row=start, start_column=mc, end_row=er - 1, end_column=mc)
                prev_k, start = k, er
        if prev_k is not None and start is not None and total_rows >= start:
            for mc in cols: ws.merge_cells(start_row=start, start_column=mc, end_row=total_rows, end_column=mc)

    _merge(sorted_rows, lambda rd: rd.get('墩台号'), [1])                # A: 墩台号
    _merge(sorted_rows, lambda rd: rd.get('钻孔编号'), [2, 3, 4, 5, 6, 7, 8])  # B-H: 钻孔级
    _merge(sorted_rows, lambda rd: rd.get('墩台号'), [16, 17, 18])       # P-R: 墩台级
    _merge(sorted_rows, lambda rd: rd.get('钻孔编号'), [14, 15])         # N-O: 孔级（溶洞累计厚度+线岩溶率）

    # 数据区域全部绘制框线（含被合并隐藏的单元格）
    for r in range(4, total_rows + 1):
        for c in range(1, 21):
            ws.cell(row=r, column=c).border = border

    # 数据行高固定为 25
    for r in range(4, total_rows + 1):
        ws.row_dimensions[r].height = 25

    wb.save(path)


def _write_table7_fallback(rows, pier_rates, pier_cave_hole_rates, pier_development, path):
    """模板缺失时的兜底生成（原始硬编码格式）"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Side, Border

    wb = Workbook(); ws = wb.active; ws.title = '附表7 岩溶率统计表'

    KEY_TO_COL = {
        '墩台号': 1, '钻孔编号': 2, '里程': 3, '偏移量': 4,
        '地面标高': 5, '孔深': 6, '基岩埋深': 7, '可溶岩累计厚度': 8,
        '溶洞顶板深度': 9, '溶洞底板深度': 10,
        '溶洞顶板高程': 11, '溶洞底板高程': 12, '溶洞高度': 13,
        '溶洞累计厚度': 14, '线岩溶率': 15,
        '溶洞充填物特征': 19, '原始描述': 20,
    }

    title_font = Font(name='宋体', size=22, bold=True)
    hdr_font = Font(name='宋体', size=11, bold=True)
    data_font = Font(name='宋体', size=11)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    center_nw = Alignment(horizontal='center', vertical='center', wrap_text=False)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 第1行：标题
    ws.merge_cells('A1:T1')
    c = ws['A1']; c.value = '附表7 岩溶率统计表'; c.font = title_font; c.alignment = center_nw; c.border = border

    # 第2行：主表头
    r2 = {'A': '墩台号', 'B': '钻孔编号', 'C': '钻孔信息', 'H': '可溶岩累计厚度(m)',
          'I': '溶洞位置（深度）（m）', 'K': '溶洞位置（高程）（m）', 'M': '溶洞高度\n(m)',
          'N': '溶洞累计厚度(m)', 'O': '线岩溶率\n(%)', 'P': '墩台线岩溶率(%)',
          'Q': '钻孔见洞率(%)', 'R': '墩台岩溶发育程度', 'S': '溶洞充填物特征', 'T': '原始描述'}
    for ref, val in r2.items():
        c = ws[ref+'2']; c.value = val; c.font = hdr_font; c.alignment = center; c.border = border
    ws.merge_cells('C2:G2'); ws.merge_cells('I2:J2'); ws.merge_cells('K2:L2')

    # 第3行：子表头
    r3 = {'C': '里程', 'D': '偏移量', 'E': '地面标高', 'F': '孔深', 'G': '基岩埋深',
          'I': '顶板', 'J': '底板', 'K': '顶板', 'L': '底板', 'T': '原始描述'}
    for ref, val in r3.items():
        ws[ref+'3'].value = val; ws[ref+'3'].font = hdr_font; ws[ref+'3'].alignment = center; ws[ref+'3'].border = border

    ws.row_dimensions[1].height = 41
    ws.row_dimensions[2].height = 27
    ws.row_dimensions[3].height = 27

    # 按墩台分组排序
    pier_groups = OrderedDict()
    current_pier = None
    for row_data in rows:
        p = row_data.get('墩台号')
        if p is not None: current_pier = p
        if current_pier not in pier_groups: pier_groups[current_pier] = []
        pier_groups[current_pier].append(row_data)
    # 组内排序：同钻孔的所有溶洞行必须紧邻。None行继承前一个非None行的钻孔号作为 effective_key
    for pier_name, group_rows in pier_groups.items():
        # 计算 effective_key：None行继承上一行的钻孔号
        indexed = list(enumerate(group_rows))
        effective = {}
        prev_k = None
        for orig_idx, r in enumerate(group_rows):
            k = r.get('钻孔编号') or prev_k
            effective[orig_idx] = k
            if r.get('钻孔编号'):
                prev_k = r['钻孔编号']
        indexed.sort(key=lambda t: (_pier_sort_key(effective[t[0]] or ''), t[0]))
        pier_groups[pier_name] = [r for _, r in indexed]
    sorted_rows = []
    for pier_name in sorted(pier_groups.keys(), key=_pier_sort_key):
        sorted_rows.extend(pier_groups[pier_name])

    data_row_to_excel = {}
    erow = 4
    # V3.0.3（S5）：同 _write_table7——O/P/Q 为百分比数值（39.0），非小数分数（0.39）
    pct_val_fmt = '0.0'
    num_fmt = '0.00'
    # 需要两位小数的列
    NUM2_COLS = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14}  # E-N列
    PCTVALUE_COL = 15  # O列: 线岩溶率(百分比数值，×100)

    for r_idx, row_data in enumerate(sorted_rows):
        data_row_to_excel[r_idx] = erow
        for data_key, col_idx in KEY_TO_COL.items():
            val = row_data.get(data_key)
            if val is not None:
                c = ws.cell(row=erow, column=col_idx, value=val)
                c.font = data_font; c.alignment = center_nw; c.border = border
                if col_idx in NUM2_COLS and isinstance(val, (int, float)):
                    c.number_format = num_fmt
                elif col_idx == PCTVALUE_COL and isinstance(val, (int, float)):
                    c.number_format = pct_val_fmt

        pier = row_data.get('墩台号')
        hid = row_data.get('钻孔编号')
        if pier and hid:
            rate = pier_rates.get(pier)
            if rate is not None:
                c = ws.cell(row=erow, column=16, value=rate)
                c.font = data_font; c.alignment = center_nw; c.border = border; c.number_format = pct_val_fmt
            cave_rate = pier_cave_hole_rates.get(pier)
            if cave_rate is not None:
                # S5：见洞率已为百分比数值，直接写入（此前 /100 后按 % 格式）
                c = ws.cell(row=erow, column=17, value=cave_rate)
                c.font = data_font; c.alignment = center_nw; c.border = border; c.number_format = pct_val_fmt
            dev = pier_development.get(pier)
            if dev is not None:
                c = ws.cell(row=erow, column=18, value=dev)
                c.font = data_font; c.alignment = center_nw; c.border = border

        erow += 1

    total_rows = erow - 1

    # 合并单元格
    def _merge(rows_list, key_fn, cols):
        prev_k, start = None, None
        for ri, rd in enumerate(rows_list):
            er = data_row_to_excel[ri]; k = key_fn(rd)
            if k is not None and k != prev_k:
                if prev_k is not None and start is not None and er-1 >= start:
                    for mc in cols: ws.merge_cells(start_row=start, start_column=mc, end_row=er-1, end_column=mc)
                prev_k, start = k, er
        if prev_k is not None and start is not None and total_rows >= start:
            for mc in cols: ws.merge_cells(start_row=start, start_column=mc, end_row=total_rows, end_column=mc)

    _merge(sorted_rows, lambda rd: rd.get('墩台号'), [1])
    _merge(sorted_rows, lambda rd: rd.get('钻孔编号'), [2,3,4,5,6,7,8])
    _merge(sorted_rows, lambda rd: rd.get('墩台号'), [16,17,18])
    _merge(sorted_rows, lambda rd: rd.get('钻孔编号'), [14,15])

    # 数据区域全部绘制框线（含被合并隐藏的单元格）
    for r in range(4, total_rows + 1):
        for c in range(1, 21):
            ws.cell(row=r, column=c).border = border

    # 列宽
    col_widths = {1:8, 2:18, 3:8, 4:8, 5:8, 6:8, 7:8, 8:10, 9:8, 10:8, 11:8, 12:8, 13:8, 14:10, 15:8, 16:10, 17:10, 18:14, 19:30, 20:30}
    for c, w in col_widths.items(): ws.column_dimensions[chr(64+c)].width = w

    # 数据行高固定为 25
    for r in range(4, ws.max_row + 1):
        ws.row_dimensions[r].height = 25

    wb.save(path)


# ============================================================
# V2026.09 新增（一致性清单 A2，自桌面 9月新版表头脚本.py 移植）：
# 新表头 岩溶发育统计表（15列 A-O）
# 数据来源/取值/排序/校验与旧版 _write_table7 完全一致，仅列映射与
# 合并规则按新表头重排；删除列：溶洞累计厚度/墩台线岩溶率/钻孔见洞率/
# 墩台岩溶发育程度/原始描述；新增列：覆盖层厚度（沿用旧版"基岩埋深"口径，
# 即首个可溶岩顶面埋深）。
# ============================================================
TPL_COL_NEW = {
    '墩台号': 1,           # A 墩台编号
    '钻孔编号': 2,         # B
    '里程': 3,             # C
    '偏移量': 4,           # D
    '地面标高': 5,         # E 孔口标高（m）
    '孔深': 6,             # F 钻孔深度（m）
    '基岩埋深': 7,         # G 覆盖层厚度（m）
    '溶洞顶板深度': 8,     # H 岩溶埋深(m)-洞顶
    '溶洞底板深度': 9,     # I 岩溶埋深(m)-洞底
    '溶洞顶板高程': 10,    # J 溶洞标高(m)-洞顶
    '溶洞底板高程': 11,    # K 溶洞标高(m)-洞底
    '可溶岩累计厚度': 12,  # L 可溶岩（含溶洞）总进尺（m）
    '溶洞高度': 13,        # M
    '线岩溶率': 14,        # N 钻孔线岩溶率（%）
    '溶洞充填物特征': 15,  # O
}


def _build_table7_new_header_fallback():
    """新模板文件缺失时，硬编码复刻新表头（15列 A-O），返回 (wb, ws)"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Side, Border

    wb = Workbook(); ws = wb.active; ws.title = '岩溶发育统计表'

    title_font = Font(name='宋体', size=22, bold=True)
    hdr_font = Font(name='宋体', size=11, bold=True)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    center_nw = Alignment(horizontal='center', vertical='center', wrap_text=False)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 第1行：标题
    ws.merge_cells('A1:O1')
    c = ws['A1']; c.value = '附表X  ×××××特大桥 岩溶发育统计表'
    c.font = title_font; c.alignment = center_nw; c.border = border

    # 第2行：主表头（B-G、L、M、N、O 纵向合并 2-3 行；H/I、J/K 横向合并）
    r2 = {'A': '墩台\n编号', 'B': '钻孔编号', 'C': '里程', 'D': '偏移量',
          'E': '孔口标高\n（m）', 'F': '钻孔深度（m）', 'G': '覆盖层厚度\n（m）',
          'H': '岩溶埋深(m)', 'J': '溶洞标高(m)',
          'L': '可溶岩（含溶洞）总进尺（m）', 'M': '溶洞高度\n（m）',
          'N': '钻孔线岩溶率\n（%）', 'O': '溶洞充填物特征'}
    for ref, val in r2.items():
        c = ws[ref + '2']; c.value = val; c.font = hdr_font; c.alignment = center; c.border = border
        ws.merge_cells(ref + '2:' + ref + '3')
    ws.merge_cells('H2:I2'); ws.merge_cells('J2:K2')

    # 第3行：子表头
    r3 = {'H': '洞顶', 'I': '洞底', 'J': '洞顶', 'K': '洞底'}
    for ref, val in r3.items():
        c = ws[ref + '3']; c.value = val; c.font = hdr_font; c.alignment = center; c.border = border

    ws.row_dimensions[1].height = 41
    ws.row_dimensions[2].height = 27
    ws.row_dimensions[3].height = 27
    return wb, ws


def _write_table7_new(rows, path):
    """写入新表头 岩溶发育统计表（使用 参数/溶洞统计模板（新）.xlsx，15列 A-O）

    填写要求与校验规则完全沿用旧版：
      - 排序：按墩台分组升序 → 组内按钻孔号升序 → 同钻孔溶洞行紧邻（洞顶深度升序）
      - 数字两位小数；线岩溶率为百分比数值（×100，一位小数，'0.0' 格式）
      - 合并：A 墩台级；B-G 钻孔级；L/N 钻孔级；溶洞级列（H-K/M/O）不合并
    """
    import openpyxl
    from openpyxl.styles import Font, Alignment, Side, Border

    # Web 适配：backend/review/ → 仓库根为三级 dirname（桌面端为二级）
    base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    template_path = os.path.join(base, '参数', '溶洞统计模板（新）.xlsx')
    if os.path.exists(template_path):
        wb = openpyxl.load_workbook(template_path)
        ws = wb.active
        # 删除模板原有数据行（若有），保留表头（第1-3行）
        for mc in list(ws.merged_cells.ranges):
            if mc.min_row >= 4:
                ws.unmerge_cells(str(mc))
        if ws.max_row >= 4:
            ws.delete_rows(4, ws.max_row - 3)
    else:
        wb, ws = _build_table7_new_header_fallback()

    data_font = Font(name='宋体', size=11)
    center = Alignment(horizontal='center', vertical='center', wrap_text=False)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 按墩台分组排序（与旧版 _write_table7 完全一致的规则）
    pier_groups = OrderedDict()
    current_pier = None
    for row_data in rows:
        p = row_data.get('墩台号')
        if p is not None: current_pier = p
        if current_pier not in pier_groups: pier_groups[current_pier] = []
        pier_groups[current_pier].append(row_data)
    for pier_name, group_rows in pier_groups.items():
        effective = {}
        prev_k = None
        for orig_idx in range(len(group_rows)):
            r = group_rows[orig_idx]
            k = r.get('钻孔编号') or prev_k
            effective[orig_idx] = k
            if r.get('钻孔编号'):
                prev_k = r['钻孔编号']
        indexed = list(enumerate(group_rows))
        indexed.sort(key=lambda t: (_pier_sort_key(effective[t[0]] or ''), t[0]))
        pier_groups[pier_name] = [r for _, r in indexed]
    sorted_rows = []
    for pier_name in sorted(pier_groups.keys(), key=_pier_sort_key):
        sorted_rows.extend(pier_groups[pier_name])

    erow = 4
    num2_fmt = '0.00'
    pct_val_fmt = '0.0'   # N列：百分比数值（×100，一位小数），与旧版 O 列口径一致
    NUM2_KEYS = {'里程', '偏移量', '地面标高', '孔深', '基岩埋深',
                  '溶洞顶板深度', '溶洞底板深度', '溶洞顶板高程', '溶洞底板高程',
                  '可溶岩累计厚度', '溶洞高度'}

    data_row_map = {}
    for r_idx, row_data in enumerate(sorted_rows):
        data_row_map[r_idx] = erow
        for data_key, col_idx in TPL_COL_NEW.items():
            val = row_data.get(data_key)
            if val is None:
                continue
            c = ws.cell(row=erow, column=col_idx, value=val)
            c.font = data_font; c.alignment = center; c.border = border
            if data_key in NUM2_KEYS and isinstance(val, (int, float)):
                c.number_format = num2_fmt
            elif data_key == '线岩溶率' and isinstance(val, (int, float)):
                c.number_format = pct_val_fmt
        erow += 1

    total_rows = erow - 1

    # 合并单元格：A 墩台级；B-G + L + N 钻孔级
    def _merge(rows_list, key_fn, cols):
        prev_k, start = None, None
        for ri, rd in enumerate(rows_list):
            er = data_row_map[ri]; k = key_fn(rd)
            if k is not None and k != prev_k:
                if prev_k is not None and start is not None and er - 1 >= start:
                    for mc in cols: ws.merge_cells(start_row=start, start_column=mc, end_row=er - 1, end_column=mc)
                prev_k, start = k, er
        if prev_k is not None and start is not None and total_rows >= start:
            for mc in cols: ws.merge_cells(start_row=start, start_column=mc, end_row=total_rows, end_column=mc)

    _merge(sorted_rows, lambda rd: rd.get('墩台号'), [1])                    # A: 墩台编号
    _merge(sorted_rows, lambda rd: rd.get('钻孔编号'), [2, 3, 4, 5, 6, 7])   # B-G: 钻孔级
    _merge(sorted_rows, lambda rd: rd.get('钻孔编号'), [12, 14])             # L/N: 钻孔级

    # 数据区域全部绘制框线（含被合并隐藏的单元格，15列）
    for r in range(4, total_rows + 1):
        for c in range(1, 16):
            ws.cell(row=r, column=c).border = border

    # 数据行高固定为 25
    for r in range(4, total_rows + 1):
        ws.row_dimensions[r].height = 25

    wb.save(path)


def _write_table8(stats, path):
    """写入附表8 溶洞统计表（复刻 V1.4.9.1 write_cave_stats_excel 格式）
    横向布局：溶洞高度分布 → 发育深度分布 → 充填程度分布
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Side, Border

    if stats is None:
        wb = Workbook(); ws = wb.active; ws.title = '附表8 溶洞统计表'
        ws.merge_cells('A1:E1'); ws['A1'].value = '附表8 溶洞统计表（无溶洞/土洞数据）'
        ws['A1'].font = Font(name='宋体', size=14, bold=True)
        ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
        wb.save(path)
        return

    wb = Workbook(); ws = wb.active; ws.title = '附表8 溶洞统计表'

    title_font = Font(name='宋体', size=11, bold=False)
    label_font = Font(name='宋体', size=10)
    num_font = Font(name='Times New Roman', size=10)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    medium = Side(style='medium'); thin = Side(style='thin'); none_s = Side(style=None)
    pct_fmt = '0.00%'

    for c in ['A', 'B', 'C', 'D', 'E']:
        ws.column_dimensions[c].width = 13

    def _set(row, col, val, font=label_font, fmt=None, bottom='thin', right=False):
        c = ws.cell(row=row, column=col, value=val)
        c.font = font; c.alignment = center; c.number_format = fmt if fmt else 'General'
        c.border = Border(left=none_s, right=medium if right else none_s, top=none_s,
                          bottom=medium if bottom == 'medium' else thin if bottom == 'thin' else none_s)

    # ===== 第1行：标题 =====
    ws.row_dimensions[1].height = 14.75
    ws.merge_cells('A1:E1')
    c = ws['A1']; c.value = '表3-6  溶洞统计表'; c.font = title_font; c.alignment = center
    c.border = Border(left=none_s, right=none_s, top=none_s, bottom=medium)

    # ===== 溶洞高度分布 (行2-4) =====
    ws.row_dimensions[2].height = 26.75
    _set(2, 1, '溶洞高度H（m）', bottom='thin', right=True)
    for i, lb in enumerate(stats['height_bins']):
        _set(2, i + 2, lb, bottom='thin', right=(i < 3))
    ws.row_dimensions[3].height = 14.75
    _set(3, 1, '个数', num_font, bottom='medium', right=True)
    for i, cnt in enumerate(stats['height_counts']):
        _set(3, i + 2, cnt, num_font, bottom='medium', right=(i < 3))
    ws.row_dimensions[4].height = 14.75
    _set(4, 1, '百分比', num_font, fmt=pct_fmt, bottom='medium', right=True)
    for i, pct in enumerate(stats['height_pcts']):
        _set(4, i + 2, pct, num_font, fmt=pct_fmt, bottom='medium', right=(i < 3))

    # ===== 发育深度分布 (行5-7) =====
    ws.row_dimensions[5].height = 26.75
    _set(5, 1, '发育深度L（m）', bottom='thin', right=True)
    for i, lb in enumerate(stats['depth_bins']):
        _set(5, i + 2, lb, bottom='thin', right=(i < 3))
    ws.row_dimensions[6].height = 14.75
    _set(6, 1, '个数', num_font, bottom='medium', right=True)
    for i, cnt in enumerate(stats['depth_counts']):
        _set(6, i + 2, cnt, num_font, bottom='medium', right=(i < 3))
    ws.row_dimensions[7].height = 14.75
    _set(7, 1, '百分比', num_font, fmt=pct_fmt, bottom='medium', right=True)
    for i, pct in enumerate(stats['depth_pcts']):
        _set(7, i + 2, pct, num_font, fmt=pct_fmt, bottom='medium', right=(i < 3))

    # ===== 充填程度分布 (行8-10) =====
    n_fill = len(stats['fill_bins'])
    ws.row_dimensions[8].height = 26.75
    _set(8, 1, '溶洞充填程度', bottom='thin', right=True)
    for i, lb in enumerate(stats['fill_bins']):
        _set(8, i + 2, lb, bottom='thin', right=(i < n_fill - 1))
    if n_fill <= 3:
        _set(8, 5, '/', bottom='thin')
    ws.row_dimensions[9].height = 14.75
    _set(9, 1, '个数', num_font, bottom='medium', right=True)
    for i, cnt in enumerate(stats['fill_counts']):
        _set(9, i + 2, cnt, num_font, bottom='medium', right=(i < n_fill - 1))
    if n_fill <= 3:
        _set(9, 5, '/')
    ws.row_dimensions[10].height = 14.75
    _set(10, 1, '百分比', num_font, fmt=pct_fmt, bottom='medium', right=True)
    for i, pct in enumerate(stats['fill_pcts']):
        _set(10, i + 2, pct, num_font, fmt=pct_fmt, bottom='medium', right=(i < n_fill - 1))
    if n_fill <= 3:
        _set(10, 5, '', bottom='medium')

    wb.save(path)
