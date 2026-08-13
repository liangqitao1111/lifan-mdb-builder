""" 理反 — 纵断面 DXF 贴块模块 (profile_dxf.py)

读取钻孔地层数据，在相邻钻孔间按主层号匹配贴 CAD 块，
输出 1:500 DXF 文件供 AutoCAD 使用。

用法：
    generate_profile(da, zkbh_list, output_path, group_interval=3,
                     block_template=' 地层标注/块.dxf')
"""

import os
import math
from collections import defaultdict
import ezdxf

# ---------- 配置常量（从 TOML 读取，兼容无配置文件的回退） ----------
from config import load_project_config, CAVITY_TYPES
_pf_cfg = load_project_config().get('DXF_纵断面', {})
OFFSET = float(_pf_cfg.get('贴块偏移', 10.0))
CAVE_WING = float(_pf_cfg.get('溶洞翼展', 16.0))
CAVE_GAP = float(_pf_cfg.get('溶洞间隙', 1.0))
TEXT_OFFSET = float(_pf_cfg.get('文字偏移', 15.0))
TEXT_HEIGHT = float(_pf_cfg.get('文字高度', 1.5))
N_SEG = int(_pf_cfg.get('溶洞分段数', 7))
CAVE_LTYPE = str(_pf_cfg.get('溶洞线型名', '岩溶空洞'))


def _load_valid_blocks(block_template):
    """ 从块模板 DXF 提取有效块名集合 """
    if not block_template or not os.path.exists(block_template):
        return None
    try:
        doc = ezdxf.readfile(block_template)
        return {b.name for b in doc.blocks
                if not b.name.startswith('*') and b.name != '0'}
    except Exception:
        return None


def _make_block_name(tczcbh, tcycbh):
    """层号 → 块名，返回 (带分隔名, 拼接名)

    优先用带'-'的名字（如 '2-31'），避免 2-31 与 23-1 去'-'后同名的冲突；
    模板无带'-'名时回退拼接名（见 generate_profile._place_block 的消歧逻辑）。
    注意：溶洞/土洞的 TCZCBH 可能为空，此时视为 "0"
    """
    main = str(tczcbh).strip().replace('-', '') if tczcbh else '0'
    sub = str(tcycbh).strip().replace('-', '') if tcycbh else ''
    if sub:
        return f'{main}-{sub}', main + sub
    return main, main


def generate_profile(da, zkbh_list, output_path, group_interval=3,
                     block_template=None, draw_cave=True, draw_label=True,
                     draw_label_text=True,
                     h_scale=500, v_scale=500):
    """ 生成纵断面 DXF 文件
    Args:
        h_scale: 横比例 1:N（默认500 即 1:500）
        v_scale: 纵比例 1:N（默认500 即 1:500）
        draw_cave: 是否绘制溶洞线
        draw_label: 是否生成地层编号块
        draw_label_text: 是否生成地层编号文字
    """

    if not zkbh_list:
        raise ValueError(" 钻孔列表为空")

    # 每次生成时重读 TOML「DXF_纵断面」（改参数即时生效）
    global OFFSET, CAVE_WING, CAVE_GAP, TEXT_OFFSET, TEXT_HEIGHT, N_SEG, CAVE_LTYPE
    _pf_cfg = load_project_config().get('DXF_纵断面', {})
    OFFSET = float(_pf_cfg.get('贴块偏移', OFFSET))
    CAVE_WING = float(_pf_cfg.get('溶洞翼展', CAVE_WING))
    CAVE_GAP = float(_pf_cfg.get('溶洞间隙', CAVE_GAP))
    TEXT_OFFSET = float(_pf_cfg.get('文字偏移', TEXT_OFFSET))
    TEXT_HEIGHT = float(_pf_cfg.get('文字高度', TEXT_HEIGHT))
    N_SEG = int(_pf_cfg.get('溶洞分段数', N_SEG))
    CAVE_LTYPE = str(_pf_cfg.get('溶洞线型名', CAVE_LTYPE))

    # ---- 1. 加载数据 ----
    all_bh = {b['zkbh']: b for b in da.get_all_boreholes() if b['zkbh'] in zkbh_list}
    ordered = sorted(zkbh_list, key=lambda z: all_bh.get(z, {}).get('zklc', 0) or 0)

    boreholes = []
    for zkbh in ordered:
        b = all_bh.get(zkbh)
        if not b or b.get('zksd', 0) <= 0:
            continue
        strata = da.get_strata('', zkbh)
        if not strata:
            continue
        boreholes.append({
            'zkbh': zkbh,
            'zklc': float(b.get('zklc', 0) or 0),
            'zkbg': float(b.get('zkbg', 0) or 0),
            'zksd': float(b.get('zksd', 0) or 0),
            'strata': strata,
        })

    if len(boreholes) < 2:
        raise ValueError(" 至少需要2个有效钻孔")

    # ---- 2. 加载块模板（直接用作基础文档，不转换版本） ----
    if not block_template or not os.path.exists(block_template):
        raise ValueError(" 块模板不存在")
    doc = ezdxf.readfile(block_template)
    msp = doc.modelspace()

    # 清空模板中已有的模型空间实体（模板里有示例块摆放）
    for entity in list(msp):
        msp.delete_entity(entity)

    # 确保有"地层"图层
    if '地层' not in [l.dxf.name for l in doc.layers]:
        doc.layers.add('地层', color=7)

    valid_blocks = _load_valid_blocks(block_template)

    # 块名消歧：拼接名（无'-'）可能冲突（如 2-31 与 23-1 都→231）。
    # 策略：优先带'-'的块名；模板无带'-'名时才回退拼接名，且仅当该拼接名
    # 在本次绘图中唯一对应一个层号对时回退，避免贴错块。
    used_pairs = set()
    for bh in boreholes:
        for s in bh['strata']:
            cb = str(s.get('tczcbh', '')).strip()
            yb = str(s.get('tcycbh', '')).strip()
            if cb or yb:
                used_pairs.add((cb, yb))
    concat_owners = defaultdict(set)
    for cb, yb in used_pairs:
        _dashed, concat = _make_block_name(cb, yb)
        concat_owners[concat].add((cb, yb))

    H_SCALE = 1000.0 / h_scale    # 横比例（1:N → 1m=N/1000 mm 图纸）
    V_SCALE = 1000.0 / v_scale    # 纵比例
    BASE_LAYER = '0'

    # ---- 3. 贴块 ----
    # 规则：
    #   - 溶洞/土洞（TCZCBH 为空）：每个孔都标，左侧 10 单位
    #   - 普通地层：按间隔分组，参照孔全标，其余孔仅标新增层，右侧 10 单位
    placed = 0
    skipped = []

    def _is_cave(s):
        """溶洞/土洞层：TCZCBH 为空；同时按岩土名称（CAVITY_TYPES）判断，
        兼容以 tczcbh='0' 存储的工程（地层标准状态参数表主层 0 即溶洞 1/2/3）。"""
        cb = str(s.get('tczcbh', '')).strip()
        yb = str(s.get('tcycbh', '')).strip()
        name = str(s.get('tcymc', '')).strip()
        if name in CAVITY_TYPES:
            return True
        return (not cb) and bool(yb)

    def _is_karst(s):
        """溶洞（区别于土洞）：TCYMC 含"溶"字"""
        name = str(s.get('tcymc', '')).strip()
        return '溶' in name

    def _place_block(bh, s, prev_depth, side_right=True):
        """贴块：side_right=True 右侧，False 左侧"""
        nonlocal placed
        cb = str(s.get('tczcbh', '')).strip()
        yb = str(s.get('tcycbh', '')).strip()
        if not cb and not yb:
            return
        block_name, block_name_legacy = _make_block_name(cb, yb)
        if valid_blocks is not None and block_name not in valid_blocks:
            # 模板无带'-'名：仅当拼接名唯一归属本层号对时回退（消歧，避免贴错块）
            if (block_name_legacy in valid_blocks
                    and block_name_legacy != block_name
                    and len(concat_owners.get(block_name_legacy, set())) == 1):
                block_name = block_name_legacy
            else:
                if block_name not in skipped:
                    skipped.append(block_name)
                return
        mid_el = bh['zkbg'] - (prev_depth + s['tccdsd']) / 2
        offset = OFFSET if side_right else -OFFSET
        x_pos = bh['zklc'] * H_SCALE + offset
        msp.add_blockref(block_name, (x_pos, mid_el * V_SCALE),
                         dxfattribs={'layer': '地层'})
        placed += 1

    # ---- 3a. 溶洞/土洞标签：每个孔都标，左侧 ----
    if draw_label:
        for bh in boreholes:
            strata = bh['strata']
            for idx, s in enumerate(strata):
                if not _is_cave(s):
                    continue
                prev = strata[idx - 1]['tccdsd'] if idx > 0 else 0
                _place_block(bh, s, prev, side_right=False)

    # ---- 3a2. 溶洞/土洞线：端部半圆弧形 ----
    #   溶洞用"岩溶空洞"线型，土洞用 ByLayer
    if draw_cave:
        has_lt = CAVE_LTYPE in [lt.dxf.name for lt in doc.linetypes]

        for bh in boreholes:
            strata = bh['strata']
            for idx, s in enumerate(strata):
                if not _is_cave(s):
                    continue
                is_karst = _is_karst(s)
                top_depth = strata[idx - 1]['tccdsd'] if idx > 0 else 0
                bottom_depth = s['tccdsd']
                x_bh = bh['zklc'] * H_SCALE
                y_top = (bh['zkbg'] - top_depth) * V_SCALE
                y_bot = (bh['zkbg'] - bottom_depth) * V_SCALE
                y_mid = (y_top + y_bot) / 2
                r = max(abs(y_top - y_bot) / 2, 0.01)

                # 线型：仅溶洞用岩溶空洞
                lt = {'linetype': CAVE_LTYPE} if (is_karst and has_lt) else {}

                # 左侧多段线
                left = [(x_bh - CAVE_GAP, y_top, 0)]
                left.append((x_bh - CAVE_WING, y_top, 0))
                for k in range(1, N_SEG):
                    theta = math.radians(90 + 180 * k / N_SEG)
                    lx = x_bh - CAVE_WING + r * math.cos(theta)
                    ly = y_mid + r * math.sin(theta)
                    left.append((lx, ly, 0))
                left.append((x_bh - CAVE_WING, y_bot, 0))
                left.append((x_bh - CAVE_GAP, y_bot, 0))
                pl = msp.add_lwpolyline(left, format='xyb',
                    dxfattribs={'layer': '地层', 'color': 5, **lt})
                pl.dxf.flags = 128
                if is_karst and not has_lt:
                    pass  # 线型缺失时静默跳过，不影响绘制

                # 右侧多段线
                right = [(x_bh + CAVE_GAP, y_bot, 0)]
                right.append((x_bh + CAVE_WING, y_bot, 0))
                for k in range(1, N_SEG):
                    theta = math.radians(270 + 180 * k / N_SEG)
                    rx = x_bh + CAVE_WING + r * math.cos(theta)
                    ry = y_mid + r * math.sin(theta)
                    right.append((rx, ry, 0))
                right.append((x_bh + CAVE_WING, y_top, 0))
                right.append((x_bh + CAVE_GAP, y_top, 0))
                pr = msp.add_lwpolyline(right, format='xyb',
                    dxfattribs={'layer': '地层', 'color': 5, **lt})
                pr.dxf.flags = 128

    # ---- 3b. 普通地层：按组标注，右侧 ----
    if draw_label:
        for g_start in range(0, len(boreholes), group_interval):
            g_end = min(g_start + group_interval, len(boreholes))
            ref_bh = boreholes[g_start]
            ref_strata = ref_bh['strata']

            # 收集参照孔的非溶洞层号
            ref_layer_keys = set()
            for s in ref_strata:
                if _is_cave(s):
                    continue
                cb = str(s.get('tczcbh', '')).strip()
                if cb:
                    ref_layer_keys.add(cb.replace('-', ''))

            # 参照孔：贴所有非溶洞层
            for idx, s in enumerate(ref_strata):
                if _is_cave(s):
                    continue
                prev = ref_strata[idx - 1]['tccdsd'] if idx > 0 else 0
                _place_block(ref_bh, s, prev, side_right=True)

            # 组内其余孔：仅贴参照孔中不存在的非溶洞层
            for h_idx in range(g_start + 1, g_end):
                bh = boreholes[h_idx]
                strata = bh['strata']
                for idx, s in enumerate(strata):
                    if _is_cave(s):
                        continue
                    cb = str(s.get('tczcbh', '')).strip()
                    if cb.replace('-', '') in ref_layer_keys:
                        continue
                    prev = strata[idx - 1]['tccdsd'] if idx > 0 else 0
                    _place_block(bh, s, prev, side_right=True)

    # ---- 4. 地层编号文字（每个孔每个地层右侧20单位） ----
    if draw_label_text:
        # 确保"复核"图层存在
        if '复核' not in [l.dxf.name for l in doc.layers]:
            doc.layers.add('复核', color=7)

        # 创建 SHX 文字样式
        style_name = 'REVIEW_TEXT'
        try:
            style = doc.styles.new(style_name, dxfattribs={
                'font': 'comfont.shx',
                'bigfont': 'hztxt.shx',
                'width': 0.7,
            })
        except Exception:
            style = doc.styles.get('Standard')

        from config import fmt_label

        for bh in boreholes:
            strata = bh['strata']
            for idx, s in enumerate(strata):
                cb = str(s.get('tczcbh', '')).strip()
                # 空层号跳过（没有编号的层不标）
                if not cb:
                    continue
                label = fmt_label(cb, str(s.get('tcycbh', '')).strip())
                prev = strata[idx - 1]['tccdsd'] if idx > 0 else 0
                mid_el = bh['zkbg'] - (prev + s['tccdsd']) / 2
                x_pos = bh['zklc'] * H_SCALE + TEXT_OFFSET
                y_pos = mid_el * V_SCALE
                txt = msp.add_text(label, dxfattribs={
                    'layer': '复核',
                    'height': TEXT_HEIGHT,
                    'style': style_name,
                })
                txt.set_placement((x_pos, y_pos))

    # ---- 5. 参考基点（仅第一个孔） ----
    ref_x = boreholes[0]['zklc'] * H_SCALE
    ref_y = boreholes[0]['zkbg'] * V_SCALE
    msp.add_circle((ref_x, ref_y), 10, dxfattribs={'layer': BASE_LAYER})
    cross = 15
    msp.add_line((ref_x - cross, ref_y), (ref_x + cross, ref_y),
                 dxfattribs={'layer': BASE_LAYER})
    msp.add_line((ref_x, ref_y - cross), (ref_x, ref_y + cross),
                 dxfattribs={'layer': BASE_LAYER})
    msp.add_text(f' 基点 {boreholes[0]["zkbh"]}', dxfattribs={
        'layer': BASE_LAYER, 'height': 8,
    }).set_placement((ref_x + 12, ref_y + 4))

    # ---- 6. 保存 ----
    output_path = str(output_path)
    if not output_path.lower().endswith('.dxf'):
        output_path += '.dxf'
    doc.saveas(output_path)
    return output_path, len(boreholes), placed, skipped
