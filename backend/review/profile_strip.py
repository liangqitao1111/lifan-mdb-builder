"""理反 — 地层剖面条带 DXF (profile_strip.py)

沿线路按500m分段，横向10单位=500m，纵向15单位=15m，
每段取深度趋势绘成连续条带。薄层合并，仅显示主要层位。
"""
import os, math
from collections import Counter
import ezdxf
from ezdxf.enums import TextEntityAlignment
from config import classify_lithology, load_project_config

LAYER = '地质-剖面条带'
STYLE = '地质'
MAX_DEPTH = 15

# 地层颜色（从 TOML 读取，兼容无配置文件的回退）
# V3.0.5（一致性清单 B5 同步）：懒加载——避免 import 时读取配置文件失败导致后端启动崩溃；
# 首次 generate_strip 时才加载，失败回退默认色并写日志
_color_cfg = {}
FORM_COLORS = {
    '填土': 251, '黏性土': 50, '粉土': 130, '砂土': 30, '碎石土': 170,
    '软土': 120, '灰岩': 140, '花岗岩': 140, '砂岩': 160, '基岩': 140,
}
_cfg_loaded = False


def _build_config():
    """重建 剖面颜色映射（懒加载：首次生成 DXF 时调用；配置保存后经 reload 调用）"""
    global _color_cfg, FORM_COLORS, _cfg_loaded

    _color_cfg = load_project_config().get('DXF_剖面颜色', {})
    FORM_COLORS = {
        '填土': _color_cfg.get('填土', 251),
        '黏性土': _color_cfg.get('黏性土', 50),
        '粉土': _color_cfg.get('粉土', 130),
        '砂土': _color_cfg.get('砂土', 30),
        '碎石土': _color_cfg.get('碎石土', 170),
        '软土': _color_cfg.get('软土', 120),
        '灰岩': _color_cfg.get('灰岩', 140),
        '花岗岩': _color_cfg.get('花岗岩', 140),
        '砂岩': _color_cfg.get('砂岩', 160),
        '基岩': _color_cfg.get('基岩', 140),
    }
    _cfg_loaded = True


def _ensure_config():
    """懒加载守卫：首次调用时加载配置，失败时使用默认颜色"""
    global _cfg_loaded
    if _cfg_loaded:
        return
    try:
        _build_config()
    except Exception as e:
        from applog import log_error
        log_error(f'剖面颜色配置加载失败，使用默认颜色: {e}')
        _cfg_loaded = True


def reload_from_config():
    """配置保存后重建本模块派生常量（review_api._reload_config_modules 调用）"""
    global _cfg_loaded
    _cfg_loaded = False
    _build_config()


def _text(msp, text, pos, height=1.2, halign=TextEntityAlignment.LEFT):
    t = msp.add_text(text, dxfattribs={'height': height, 'style': STYLE, 'layer': LAYER})
    t.set_placement(pos, align=halign)
    return t


def _simplify_name(name):
    lt = classify_lithology(name or '')
    if lt in ('cavity',) or '孤石' in name or '漂石' in name:
        return 'rock'
    if lt == 'other':
        for kw in ('灰岩', '石灰', '花岗', '砂岩', '泥岩', '页岩',
                    '大理岩', '岩溶', '白云', '玄武'):
            if kw in name:
                return 'rock'
    return lt if lt in ('fill','clay','silt','sand','gravel','muck','rock') else 'other'


def _label(name):
    table = {'fill':'填土','clay':'黏性土','silt':'粉土',
             'sand':'砂土','gravel':'碎石土','muck':'软土','rock':'基岩'}
    return table.get(name, name)


def _build_segments(boreholes, segment_m=500):
    valid = [(b['zkbh'], b.get('zklc', 0) or 0)
             for b in boreholes if b['zksd'] > 0 and (b.get('zklc', 0) or 0) > 0]
    valid.sort(key=lambda x: x[1])
    if not valid:
        return []
    min_lc = float(valid[0][1])
    max_lc = valid[-1][1]
    segments, seg_start = [], min_lc
    # P1-1：单孔/全部孔里程相同（min_lc == max_lc）时原 `while seg_start < max_lc`
    # 直接为假 → 条带生成失败；改为 `<=` 保证至少一段。
    # P2-1：末段边界孔（里程恰等于 seg_end=max_lc）原 `bh < seg_end` 会整孔丢弃，
    # 末段改含端点 `<=`。
    while seg_start <= max_lc:
        seg_end = seg_start + segment_m
        is_last = seg_end >= max_lc
        if is_last:
            seg_bhs = [bh for bh in valid if seg_start <= bh[1] <= max_lc]
        else:
            seg_bhs = [bh for bh in valid if seg_start <= bh[1] < seg_end]
        if seg_bhs:
            segments.append({
                'start': seg_start, 'end': seg_end,
                'boreholes': [b[0] for b in seg_bhs],
                'x': int((seg_start - min_lc) / segment_m) * 10,
            })
        if is_last:
            break
        seg_start = seg_end
    return segments


def _build_column(da, borehole_ids, slice_m=0.5):
    """对一组钻孔取深度趋势：每 slice_m 切片多数投票"""
    total_slices = int(MAX_DEPTH / slice_m)
    counter = [Counter() for _ in range(total_slices)]

    for zkbh in borehole_ids:
        strata = da.get_strata('', zkbh)
        prev = 0.0
        for s in strata:
            name = s.get('tcymc', '')
            bottom = s.get('tccdsd', 0) or 0
            if bottom <= 0 or prev >= MAX_DEPTH:
                if prev >= MAX_DEPTH:
                    break
                continue
            top = max(prev, 0.0)
            bot = min(bottom, float(MAX_DEPTH))
            if top >= bot:
                # P2-2：prev 只前进不后退（修复前 bottom 小于上一层时 prev 倒退，
                # 已投过的切片区间被重复计票，多数投票失真）
                prev = max(prev, bottom)
                continue
            simple = _simplify_name(name)
            si = int(top / slice_m)
            ei = int(math.ceil(bot / slice_m))
            for i in range(si, min(ei, total_slices)):
                counter[i][simple] += 1
            prev = max(prev, bottom)

    dominant = []
    for i in range(total_slices):
        if counter[i]:
            dom, _ = counter[i].most_common(1)[0]
        else:
            dom = dominant[-1] if dominant else 'other'
        dominant.append(dom)

    if not dominant:
        return []

    # 合并相邻同类型
    layers = []
    cur, cur_top = dominant[0], 0.0
    for i in range(1, total_slices):
        if dominant[i] != cur:
            layers.append({'name': _label(cur), 'top': cur_top, 'bottom': i * slice_m})
            cur, cur_top = dominant[i], i * slice_m
    layers.append({'name': _label(cur), 'top': cur_top, 'bottom': MAX_DEPTH})

    # 滤薄层(<1m)：P3 修复——不再无条件并入上层。
    # 规则：优先并入同名相邻层；两侧都不同名时并入更厚的相邻层（投票占优侧），
    # 避免 0.5m 淤泥夹层被并入上部砂层造成条带失真。
    # 第一遍：合并同名相邻层
    layers2 = []
    for l in layers:
        if layers2 and layers2[-1]['name'] == l['name']:
            layers2[-1]['bottom'] = l['bottom']
        else:
            layers2.append(dict(l))
    # 第二遍：薄层并入相邻多数类型
    merged = []
    for i, l in enumerate(layers2):
        if (l['bottom'] - l['top']) >= 1.0:
            merged.append(l)
            continue
        up = merged[-1] if merged else None
        down = layers2[i + 1] if i + 1 < len(layers2) else None
        if down is not None and down['name'] == l['name']:
            down['top'] = l['top']          # 并入下方同名层
            continue
        if up is not None and up['name'] == l['name']:
            up['bottom'] = l['bottom']      # 并入上方同名层
            continue
        up_thick = (up['bottom'] - up['top']) if up else 0.0
        down_thick = (down['bottom'] - down['top']) if down else 0.0
        if up is None:
            down['top'] = l['top']
        elif down is None or up_thick >= down_thick:
            up['bottom'] = l['bottom']      # 并入更厚的相邻层
        else:
            down['top'] = l['top']
    result = []
    for l in merged:
        if result and result[-1]['name'] == l['name']:
            result[-1]['bottom'] = l['bottom']
        else:
            result.append(l)
    for l in result:
        l['top'] = round(l['top'], 1)
        l['bottom'] = round(l['bottom'], 1)
    return result


def generate_strip(da, output_path, segment_m=500):
    """生成地层剖面条带 DXF"""
    _ensure_config()
    boreholes = da.get_all_boreholes()
    segments = _build_segments(boreholes, segment_m)
    if not segments:
        return 0, '没有带里程的钻孔数据'

    doc = ezdxf.new('R2010')
    s = doc.styles.add(STYLE, font='comfont.shx')
    s.dxf.bigfont = 'hztxt.shx'
    s.dxf.width = 0.7
    msp = doc.modelspace()

    y_top = MAX_DEPTH + 4  # 留 4 单位标题空间

    # 深度尺
    for d in range(0, MAX_DEPTH + 1, 5):
        y = y_top - d
        msp.add_line((-3, y), (0, y), dxfattribs={'color': 7, 'layer': LAYER})
        _text(msp, str(d), (-4, y - 0.6), halign=TextEntityAlignment.RIGHT)

    # 外框
    total_w = len(segments) * 10
    msp.add_lwpolyline([(0, y_top), (total_w, y_top),
                         (total_w, y_top - MAX_DEPTH), (0, y_top - MAX_DEPTH)],
                        close=True, dxfattribs={'color': 7, 'layer': LAYER})

    # 每 500m 一条竖向虚线
    for i in range(1, len(segments)):
        x = i * 10
        msp.add_line((x, y_top), (x, y_top - MAX_DEPTH),
                      dxfattribs={'color': 9, 'layer': LAYER})

    placed = 0
    for seg in segments:
        x_left = seg['x']
        composite = _build_column(da, seg['boreholes'])
        if not composite:
            continue

        for layer in composite:
            ty = y_top - layer['top']
            by = y_top - layer['bottom']
            color = FORM_COLORS.get(layer['name'], 8)
            # 填充
            hatch = msp.add_hatch(color=color, dxfattribs={'layer': LAYER})
            hatch.paths.add_polyline_path(
                [(x_left, ty), (x_left + 10, ty), (x_left + 10, by), (x_left, by)],
                is_closed=True)

        # 里程标注
        km = seg['start'] / 1000
        _text(msp, f'DK{km:.1f}', (x_left, y_top + 0.5), height=1.0)

        # 水位
        water = []
        for zkbh in seg['boreholes'][:1]:
            try:
                wd = da.get_water_data(zkbh)
                for w in wd:
                    d = w.get('swsd', 0) or 0
                    if d > 0:
                        water.append(d)
            except Exception as e:
                # B5 同步：水位读取失败写告警日志，不再静默吞掉
                from applog import log_warning
                log_warning(f'剖面条带水位数据读取失败（孔 {zkbh}）: {e}')
        if water:
            w_min, w_max = min(water), max(water)
            wy = y_top - (w_min + w_max) / 2
            w_text = f'{w_min:.1f}~{w_max:.1f}'
            _text(msp, w_text, (x_left + 1, wy - 0.5), height=0.8)

        placed += 1

    # 图例
    leg_x = total_w + 5
    leg_y = y_top - 1
    for name, color in FORM_COLORS.items():
        msp.add_lwpolyline([(leg_x, leg_y), (leg_x + 4, leg_y), (leg_x + 4, leg_y + 2), (leg_x, leg_y + 2)],
                            close=True, dxfattribs={'color': color, 'layer': LAYER})
        _text(msp, name, (leg_x + 5, leg_y + 0.5), height=0.8)
        leg_y -= 2.5

    doc.saveas(output_path)
    return placed, f'已生成 {placed} 段，{total_w}x{MAX_DEPTH}'
