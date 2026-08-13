"""小柱状图 DXF 生成模块 — 理反

每 500m 一个区间，聚合区间内全部钻孔的地层和水位数据，
生成综合柱状图 DXF。每根柱子：
  • 左侧：稳定水位标注（三角箭头+横线+文字，对应每柱）
  • 中间：地层柱（空心矩形，不填充，高150×宽10）
  • 右侧：地层名称 + 平均厚度（标注到对应区间内）

文字样式 "地质"（comfont.shx + hztxt.shx，宽高比 0.7）
图层 "地质-小柱状图"（柱体、厚度）
图层 "标注"（地层名称文字）

主程序调用：
    from column_dxf import generate_columns
    placed, msg = generate_columns(da, path)
"""

import traceback
from collections import defaultdict
from typing import List, Dict, Any, Tuple

import ezdxf
from ezdxf.enums import TextEntityAlignment

# ---------------------------------------------------------------------------
# 配置常量（从 TOML 读取，兼容无配置文件的回退）
# ---------------------------------------------------------------------------
from config import load_project_config
_dxf_cfg = load_project_config().get('DXF_小柱状图', {})
INTERVAL = _dxf_cfg.get('里程间隔', 500)              # 里程区间长度 (m)
MAX_DEPTH = float(_dxf_cfg.get('最大深度', 15.0))      # 最大显示深度 (m)

# 柱状图几何参数（CAD 坐标，单位 mm）
COL_WIDTH = int(_dxf_cfg.get('柱宽', 10))              # 柱宽
COL_HEIGHT = int(_dxf_cfg.get('柱高', 15))             # 柱高 15mm（对应 15m 深度）
DEPTH_SCALE = COL_HEIGHT / MAX_DEPTH                  # 1m = 1mm
OFFSET_Y = 12                                          # 柱体顶部 Y

# 右侧标注
SPACING = int(_dxf_cfg.get('水平间距', 80))            # 各柱水平间距

# 水位标注
ARROW_SIZE = 2.0                                       # 三角箭头边长

# 文字样式与图层
TEXT_STYLE = '地质'
LAYER_COLUMN = '地质-小柱状图'
LAYER_DELETE = '删除'
SHX_FONT = 'comfont.shx'
BIG_FONT = 'hztxt.shx'
WIDTH_FACTOR = 0.7

# 岩土名称简化映射
STRATA_NAME_MAP = _dxf_cfg.get('岩土名称简化', {
    '溶洞': '灰岩',
    '土洞': '灰岩',
    '溶蚀': '灰岩',
    '岩溶化灰岩': '灰岩',
    '素填土': '填土',
    '杂填土': '填土',
    '人工填土': '填土',
    '回填土': '填土',
})

# 最小平均厚度阈值（小于该值的不参与统计和绘制）
MIN_THICKNESS = float(_dxf_cfg.get('最小厚度', 1.0))
# 统计厚度阈值：小于该值的单层样本不参与厚度范围计算
MIN_SAMPLE_THICKNESS = float(_dxf_cfg.get('最小样本厚度', 0.5))


# ---------------------------------------------------------------------------
# 岩土名称简化
# ---------------------------------------------------------------------------

def _simplify_name(raw_name: str) -> str:
    for keyword, target in STRATA_NAME_MAP.items():
        if keyword in raw_name:
            return target
    return raw_name


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def _load_boreholes(da) -> List[Dict[str, Any]]:
    bhs = da.get_all_boreholes()
    result = []
    for bh in bhs:
        lc = bh.get('zklc', 0)
        if lc is None or lc <= 0:
            continue
        result.append(bh)
    return sorted(result, key=lambda x: x['zklc'])


def _group_intervals(bhs: List[Dict], interval: int = INTERVAL) -> List[Dict]:
    if not bhs:
        return []
    current_start = int(min(b['zklc'] for b in bhs))
    groups = []
    while True:
        current_end = current_start + interval
        group_bhs = [b for b in bhs if current_start <= b['zklc'] < current_end]
        if group_bhs:
            groups.append({
                'start': current_start,
                'end': current_end,
                'bhs': group_bhs,
            })
        remaining = [b for b in bhs if b['zklc'] >= current_end]
        if not remaining:
            break
        current_start = current_end
    return groups


def _aggregate_strata(group: Dict) -> List[Dict]:
    """深度切片统计 + 合并相邻同名 → 无重叠无间隙的连续地层柱

    方法：
      1. 0~15m 每 0.5m 切片，统计每个切片内出现最多的岩土名称
      2. 合并相邻同名切片为连续层
      3. 过滤平均厚度 < 1m 的层
    """
    SLICE = 0.5                 # 切片步长 (m)
    total_slices = int(MAX_DEPTH / SLICE)  # 30 个切片

    # ---- 第一步：逐切片统计各岩土出现次数 ----
    # 先收集每个钻孔的深度-岩土对照表
    borehole_depth_names = {}  # {zkbh: [(top, bottom, name), ...]}

    for bh in group['bhs']:
        zkbh = bh['zkbh']
        strata = bh.get('strata', [])
        if not strata:
            continue
        entries = []
        prev_depth = 0.0
        for s in strata:
            bottom = float(s.get('tccdsd', 0) or 0)
            thick = float(s.get('tchd', 0) or 0)
            if thick <= 0:
                thick = bottom - prev_depth
            top = bottom - thick
            if top >= MAX_DEPTH:
                prev_depth = bottom
                continue
            if bottom > MAX_DEPTH:
                bottom = MAX_DEPTH
            raw_name = (s.get('tcymc', '') or s.get('tcmc', '') or '').strip()
            name = _simplify_name(raw_name)
            if not name:
                prev_depth = bottom
                continue
            entries.append((top, bottom, name))
            prev_depth = float(s.get('tccdsd', 0) or 0)
        if entries:
            borehole_depth_names[zkbh] = entries

    if not borehole_depth_names:
        return []

    # 逐切片投票
    slice_names = []  # 每个切片的胜出岩土名
    for i in range(total_slices):
        d_top = i * SLICE
        d_bot = d_top + SLICE
        tallies = defaultdict(int)
        for zkbh, entries in borehole_depth_names.items():
            # 找这个钻孔里覆盖当前切片的岩土。
            # P3 修复：由"全包含"(top<=d_top and bottom>=d_bot) 改为"部分覆盖"
            # (top<d_bot and bottom>d_top)——<0.5m 薄层此前不参与投票，
            # 导致其深度区间无岩土名（None 跳过）在柱体里产生空段。
            for top, bottom, name in entries:
                if top < d_bot and bottom > d_top:
                    tallies[name] += 1
                    break  # 每个孔只投一票
        if tallies:
            slice_names.append(max(tallies, key=tallies.get))
        else:
            slice_names.append(None)

    # ---- 第二步：合并相邻同名切片 ----
    merged = []
    i = 0
    while i < total_slices:
        name = slice_names[i]
        if name is None:
            i += 1
            continue
        j = i
        while j + 1 < total_slices and slice_names[j + 1] == name:
            j += 1
        # 切片 i..j 组成一层
        top = i * SLICE
        bottom = (j + 1) * SLICE
        merged.append({'name': name, 'top': top, 'bottom': bottom})
        i = j + 1

    if not merged:
        return []

    # ---- 第三步：过滤平均厚度 < 1m 的层 ----
    filtered = []
    for m in merged:
        thick = m['bottom'] - m['top']
        if thick >= MIN_THICKNESS:
            filtered.append({'name': m['name'], 'top': m['top'],
                             'bottom': m['bottom'], 'thick_avg': round(thick, 1)})

    # ---- 第四步：计算每层的厚度范围（从该区间内各钻孔的原始数据）----
    # 收集每个岩土名在所有钻孔中出现的厚度值
    name_thicknesses = defaultdict(list)
    for zkbh, entries in borehole_depth_names.items():
        for top, bottom, entry_name in entries:
            thick = bottom - top
            if thick >= MIN_SAMPLE_THICKNESS:   # 厚度 < 0.5m 的薄层不参与厚度统计
                name_thicknesses[entry_name].append(thick)

    for m in filtered:
        name = m['name']
        thicks = name_thicknesses.get(name, [m['thick_avg']])
        m['thick_min'] = round(min(thicks), 1)
        m['thick_max'] = round(max(thicks), 1)

    return filtered


def _get_water_level_range(group: Dict) -> Tuple[float, float]:
    """获取区间内稳定水位深度范围"""
    depths = []
    for bh in group['bhs']:
        water_list = bh.get('water', [])
        for w in water_list:
            swlx = w.get('swlx', '')      # SWLX: '1'=稳定
            swsd = w.get('swsd', 0)
            if swlx == '1' and swsd > 0:
                depths.append(swsd)
    if not depths:
        return None, None
    return min(depths), max(depths)


# ---------------------------------------------------------------------------
# DXF 工具
# ---------------------------------------------------------------------------

def _setup_style(doc):
    """新建文字样式 地质"""
    style = doc.styles.new(TEXT_STYLE)
    style.dxf.font = SHX_FONT
    style.dxf.bigfont = BIG_FONT
    style.dxf.width = WIDTH_FACTOR
    return style


def _setup_layers(doc):
    for name in (LAYER_COLUMN, LAYER_DELETE):
        if name not in doc.layers:
            doc.layers.new(name)


def _add_text(msp, text, pos, height=1.2, layer=LAYER_COLUMN,
              align=TextEntityAlignment.MIDDLE_LEFT):
    t = msp.add_text(text, height=height,
                     dxfattribs={'style': TEXT_STYLE, 'layer': layer})
    t.set_placement(pos, align=align)
    return t


def _draw_water_level(msp, ox, y0, wl_min, wl_max):
    """绘制稳定水位：只标注深度范围数字，无线条无三角"""
    if wl_min is None:
        return
    wl_depth = (wl_min + wl_max) / 2
    y_wl = y0 - wl_depth * DEPTH_SCALE

    _add_text(msp, f'{wl_min:.1f}-{wl_max:.1f}', (ox - 3, y_wl),
              height=1.5, align=TextEntityAlignment.MIDDLE_RIGHT)


def _draw_column(msp, ox, aggregated, interval_start, interval_end, total_bhs,
                 wl_min, wl_max):
    """绘制单根综合柱状图"""
    y0 = OFFSET_Y

    # ---- 顶部里程标注（图层="删除"） ----
    label = f'{int(interval_start)}-{int(interval_end)}m ({total_bhs}孔)'
    _add_text(msp, label, (ox + COL_WIDTH / 2, y0 + 3),
              height=1.5, layer=LAYER_DELETE, align=TextEntityAlignment.MIDDLE_CENTER)

    # ---- 左侧水位标注 ----
    _draw_water_level(msp, ox, y0, wl_min, wl_max)

    # ---- 逐层绘制 ----
    # 地层之间只画分隔横线，不画独立闭合矩形（避免相邻层边线重叠）
    prev_y = y0  # 柱顶
    for s in aggregated:
        top = max(s['top'], 0.0)
        bottom = min(s['bottom'], MAX_DEPTH)
        if bottom <= top:
            continue

        y_top = y0 - top * DEPTH_SCALE
        y_bot = y0 - bottom * DEPTH_SCALE

        # 只画当前层的地层分隔线（下边界横线）—— 柱顶线由外框负责
        if prev_y > y_bot:  # 有有效层高
            msp.add_line((ox, y_bot), (ox + COL_WIDTH, y_bot),
                         dxfattribs={'layer': LAYER_COLUMN})

        mid_y = (prev_y + y_bot) / 2

        # 右侧：厚度范围（柱右边缘偏移 3mm）
        if s['thick_min'] == s['thick_max']:
            thick_text = f'{s["thick_min"]:.1f}'
        else:
            thick_text = f'{s["thick_min"]:.1f}-{s["thick_max"]:.1f}'
        _add_text(msp, thick_text, (ox + COL_WIDTH + 3, mid_y),
                  height=1.2, align=TextEntityAlignment.MIDDLE_LEFT)

        # 右侧：地层名称（图层="删除"，柱右边缘偏移 15mm）
        _add_text(msp, s['name'], (ox + COL_WIDTH + 15, mid_y),
                  height=1.2, layer=LAYER_DELETE,
                  align=TextEntityAlignment.MIDDLE_LEFT)

        prev_y = y_bot

    # ---- 柱体外框 ----
    x1, x2 = ox, ox + COL_WIDTH
    y_bottom = y0 - COL_HEIGHT
    msp.add_lwpolyline([
        (x1, y0), (x2, y0), (x2, y_bottom), (x1, y_bottom), (x1, y0),
    ], close=True, dxfattribs={'layer': LAYER_COLUMN})


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def generate_columns(da, output_path: str, interval: int = None,
                     col_height: float = None, col_width: float = None) -> Tuple[int, str]:
    """生成小柱状图 DXF，col_height/col_width 为自定义柱尺寸(mm)。
    参数优先取调用值；未传时每次运行时读取 TOML「DXF_小柱状图」（改参数即时生效）。"""
    global COL_HEIGHT, COL_WIDTH, DEPTH_SCALE, INTERVAL, MAX_DEPTH, SPACING, \
        MIN_THICKNESS, MIN_SAMPLE_THICKNESS, STRATA_NAME_MAP
    _cfg = load_project_config().get('DXF_小柱状图', {})
    INTERVAL = int(_cfg.get('里程间隔', INTERVAL))
    MAX_DEPTH = float(_cfg.get('最大深度', MAX_DEPTH))
    COL_WIDTH = int(_cfg.get('柱宽', COL_WIDTH))
    COL_HEIGHT = int(_cfg.get('柱高', COL_HEIGHT))
    SPACING = int(_cfg.get('水平间距', SPACING))
    MIN_THICKNESS = float(_cfg.get('最小厚度', MIN_THICKNESS))
    MIN_SAMPLE_THICKNESS = float(_cfg.get('最小样本厚度', MIN_SAMPLE_THICKNESS))
    STRATA_NAME_MAP = _cfg.get('岩土名称简化', STRATA_NAME_MAP)
    if col_height is not None:
        COL_HEIGHT = col_height
    if col_width is not None:
        COL_WIDTH = col_width
    if interval is None:
        interval = INTERVAL
    DEPTH_SCALE = COL_HEIGHT / MAX_DEPTH
    try:
        bhs = _load_boreholes(da)
        if not bhs:
            return 0, '没有可绘制的钻孔（均无里程数据）'

        all_strata = da.get_all_strata()
        all_water = da.get_all_water()
        for bh in bhs:
            bh['strata'] = all_strata.get(bh['zkbh'], [])
            bh['water'] = all_water.get(bh['zkbh'], [])

        groups = _group_intervals(bhs, interval)
        if not groups:
            return 0, '分组后无数据'

        doc = ezdxf.new('R2010', setup=True)
        _setup_style(doc)
        _setup_layers(doc)
        msp = doc.modelspace()

        placed = 0
        for idx, g in enumerate(groups):
            aggregated = _aggregate_strata(g)
            if not aggregated:
                continue
            wl_min, wl_max = _get_water_level_range(g)
            ox = idx * SPACING
            _draw_column(msp, ox, aggregated,
                         g['start'], g['end'], len(g['bhs']),
                         wl_min, wl_max)
            placed += 1

        doc.saveas(output_path)
        return placed, f'成功生成 {placed} 根小柱状图'

    except Exception as e:
        traceback.print_exc()
        raise
