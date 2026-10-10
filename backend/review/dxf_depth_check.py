"""理反 — 纵断面DXF地层深度复核模块 (dxf_depth_check.py)

对比DXF纵断面图上"层底深度"标注与数据库中的地层深度，
输出不一致项，用于发现绘图或绘图/录入错误。

一致性清单 A1：自桌面端 V3.1.11（模块/dxf_depth_check.py, 7b0d8c0）移植。
Web 适配（纯文本 DXF 解析逻辑逐行保留）：
  - 数据侧不再直连 MDB（pypyodbc/ConnectionManager），改由 Web 调用方把
    SQLite 工作库地层/标贯按 {钻孔: [{depth,name,code}]} / {钻孔: [(深度,击数)]}
    传入（check_depth_da）； MDB 专用入口 check_depth 保留并给出明确迁移提示。
  - 移除 _connect_cad（win32com）与 sys.path 注入（review 包内相对导入）。
  - CAD 参数从 TOML「CAD复核」读取，与桌面同键。

用法（Web 后端）:
    from review.dxf_depth_check import check_depth_da
    result = check_depth_da(dxf_path, da)   # da = DataAccess(sqlite_conn)
"""
import re
import os
import sys
import time
import statistics
from collections import defaultdict

# ======================== 配置 ========================
from config import load_project_config
_cad_cfg = load_project_config().get('CAD复核', {})
SEARCH_RADIUS = float(_cad_cfg.get('搜索半径', 16.0))               # 层底深度文字在钻孔编号右侧搜索范围(绘图单位)
TOLERANCE = float(_cad_cfg.get('深度公差', 0.05))                   # 深度对比允许误差(米)

# 标贯击数扫描专用参数
SPT_RIGHT_OFFSET = float(_cad_cfg.get('标贯搜索偏移', 12.5))       # 标贯N=xx文字在钻孔"孔号"右侧的搜索范围(绘图单位)
VERTICAL_SCALE = float(_cad_cfg.get('竖向比例', 2.5))               # 竖向比例：每米深度对应的CAD单位。
SPT_Y_MARGIN = float(_cad_cfg.get('标贯Y容差', 2.0))               # 允许标贯文字略高于孔口(绘图单位)
SPT_FALLBACK_DEPTH_M = float(_cad_cfg.get('标贯下界兜底', 120.0))   # 某孔查不到本场地层时，标贯竖向下界的兜底深度(米)。


def reload_from_config():
    """重载 CAD 复核参数，供参数中心保存后即时生效。"""
    global SEARCH_RADIUS, TOLERANCE, SPT_RIGHT_OFFSET, VERTICAL_SCALE
    global SPT_Y_MARGIN, SPT_FALLBACK_DEPTH_M
    cfg = load_project_config().get('CAD复核', {})
    try:
        SEARCH_RADIUS = float(cfg.get('搜索半径', 16.0))
        TOLERANCE = float(cfg.get('深度公差', 0.05))
        SPT_RIGHT_OFFSET = float(cfg.get('标贯搜索偏移', 12.5))
        VERTICAL_SCALE = float(cfg.get('竖向比例', 2.5))
        SPT_Y_MARGIN = float(cfg.get('标贯Y容差', 2.0))
        SPT_FALLBACK_DEPTH_M = float(cfg.get('标贯下界兜底', 120.0))
    except (TypeError, ValueError, OverflowError):
        SEARCH_RADIUS, TOLERANCE = 16.0, 0.05
        SPT_RIGHT_OFFSET, VERTICAL_SCALE = 12.5, 2.5
        SPT_Y_MARGIN, SPT_FALLBACK_DEPTH_M = 2.0, 120.0


def _auto_detect_prefix_da(da):
    """从 DAO（SQLite 工作库）自动检测钻孔编号前缀（所有钻孔ID的最长公共前缀）

    与桌面 _auto_detect_prefix 同口径；数据源由 MDB 游标改为 DataAccess。
    """
    ids = sorted([str(b['zkbh']).strip() for b in da.get_all_boreholes()
                  if b.get('zkbh') and str(b['zkbh']).strip()])
    if not ids:
        return ""
    prefix = ids[0]
    for bh in ids[1:]:
        while prefix and not bh.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    # 末尾必须到 '-' 以便拆分墩台号
    if '-' in prefix:
        prefix = prefix[:prefix.rindex('-') + 1]
    return prefix


def scan_dxf_depth(dxf_path_or_doc, prefix, max_db_depth=None, db_depth_map=None,
                   v_scale=VERTICAL_SCALE, db_spt=None):
    """扫描DXF文件，提取钻孔编号、层底深度与标贯击数

    参数:
        dxf_path_or_doc: DXF文件路径(str)
        max_db_depth:    已废弃（保留兼容），由 db_depth_map 取代
        db_depth_map:    {钻孔编号: 本孔最深地层深度(米)}，用于标贯竖向搜索下界
        v_scale:         竖向比例（每米对应的CAD单位），缺省时由本张DXF自动推算
        db_spt:          {钻孔编号: [击数,...]} 数据库标贯，触发二次细致扫描；None 不触发

    返回:
        (dxf_depths, all_boreholes, no_depth_bhs, spt_data, v_scale_used, spt_warnings, spt_supplemented)
        v_scale_used: 实际采用的竖向比例（自动推算或被传入值）
        spt_warnings: {钻孔: [{'n','y'}, ...]} 被下界裁掉的标贯（疑似漏读）
        spt_supplemented: {钻孔: [{'n','y'}, ...]} 二次细致扫描补充回收的标贯
    """
    if not isinstance(dxf_path_or_doc, str):
        raise TypeError("仅支持 DXF 文件路径（str），不支持 AutoCAD COM 对象或 DWG")
    dxf_depths, boreholes, no_depth, spt_data, v_scale_used, spt_warnings, spt_supplemented, depth_isolated = _scan_dxf_file(
        dxf_path_or_doc, prefix, db_depth_map=db_depth_map, v_scale=v_scale, db_spt=db_spt)
    return dxf_depths, boreholes, no_depth, spt_data, v_scale_used, spt_warnings, spt_supplemented, depth_isolated


def _is_text_entity(code):
    """判断是否为 TEXT 或 MTEXT 实体类型"""
    return code in ('TEXT', 'MTEXT')


def _clean_mtext(txt):
    """清理 MTEXT 格式化代码，保留正文（含 N=xx 等有效文字）
    例: {\\fSimSun|b0|i0|c134|p2;N=10} → N=10
         \\P 与 \\p 为换行，转成空格。
    """
    import re as _re
    # 反复剥离最内层花括号包裹的格式码，兼容嵌套
    prev = None
    while prev != txt:
        prev = txt
        # {\\<letter>...;<body>} → 只保留 <body>，避免正文被整段删除
        txt = _re.sub(r'\{\\[A-Za-z][^;]*;([^{}]*)\}', r'\1', txt)
        # 裸花括号 {...} → 保留内容
        txt = _re.sub(r'\{([^{}]*)\}', r'\1', txt)
    # 去掉 \\P / \\p 换行符
    txt = txt.replace('\\P', ' ').replace('\\p', ' ')
    return txt.strip()


def _parse_blocks(lines):
    """解析 DXF 的 BLOCKS 区，返回 {块名: [{'txt':..., 'x':..., 'y':..., 'style':...}, ...]}
    用于展开 INSERT 实体时获取块内文本。
    """
    blocks = {}
    in_blocks = False
    in_block = False
    block_name = ''
    block_entities = []
    i = 0
    while i < len(lines):
        code = lines[i]
        if not in_blocks:
            if code == 'SECTION':
                if i + 1 < len(lines) and lines[i + 1] == 'BLOCKS':
                    in_blocks = True
            i += 1; continue
        if code == 'ENDSEC':
            break
        if code == '0' and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt == 'BLOCK':
                in_block = True
                block_entities = []
                i += 2; continue
            elif nxt == 'ENDBLK':
                if block_name:
                    blocks[block_name] = block_entities
                in_block = False
                i += 2; continue
        if in_block:
            if code == '2':
                block_name = lines[i + 1] if i + 1 < len(lines) else ''
            elif code == '0' and _is_text_entity(lines[i + 1]):
                # 块内的 TEXT/MTEXT
                props = {}
                j = i + 2
                while j < len(lines):
                    gc = lines[j]
                    if gc == '0' or _is_text_entity(gc): break
                    if j + 1 < len(lines): props[gc] = lines[j + 1]
                    j += 2
                txt = _clean_mtext(props.get('1', '').strip())
                try:
                    x, y = float(props.get('10', 0)), float(props.get('20', 0))
                    style = props.get('7', '')
                    block_entities.append({'txt': txt, 'x': x, 'y': y, 'style': style})
                except (ValueError, TypeError):
                    pass
                i = j; continue
        i += 1
    return blocks


def _expand_insert_entities(lines, block_texts, i):
    """处理 INSERT 实体：返回块内文本实体的列表（坐标已平移到插入点）"""
    insert_name = ''
    insert_x, insert_y = 0.0, 0.0
    j = i + 2
    while j < len(lines):
        gc = lines[j]
        if gc == '0' or _is_text_entity(gc): break
        if gc == '2': insert_name = lines[j + 1] if j + 1 < len(lines) else ''
        if gc == '10':
            try: insert_x = float(lines[j + 1]) if j + 1 < len(lines) else 0
            except (ValueError, TypeError): pass
        if gc == '20':
            try: insert_y = float(lines[j + 1]) if j + 1 < len(lines) else 0
            except (ValueError, TypeError): pass
        j += 2
    if insert_name not in block_texts:
        return [], j
    expanded = []
    for be in block_texts[insert_name]:
        expanded.append({
            'txt': be['txt'],
            'x': be['x'] + insert_x,
            'y': be['y'] + insert_y,
            'style': be['style'],
        })
    return expanded, j


# 同排/跨排判据：同一排钻孔的"孔口标注"Y 抖动实测约 1.5 绘图单位；
# 上下叠放的两排孔 Y 相差数百单位。取 5.0 作为"是否同一竖向高度"的分界，
# 既能把同排视为等高（改用水平距离比较），又能干净地分辨上下两排。
DEPTH_LEVEL_TOL = 5.0


def _pick_depth_owner(boreholes, d):
    """为一条"层底深度"文字挑选归属钻孔。

    归属口径（V3.0.7 根因③修正）：
      1) 候选：0 ≤ dx ≤ 搜索半径（文字在本孔编号右侧），且文字不高于孔口+5；
      2) 在候选中取 **孔口位于文字正上方且最近** 者（即 (孔口Y − 文字Y) 最小）；
      3) 竖向高度相同（差值 ≤ DEPTH_LEVEL_TOL）时，改取 **水平距离 dx 最小** 者。

    为什么必须带竖向判据：同一 x 列可能上下叠着两排钻孔。实测江村西展布图里
    26-ZD-JCXT-Z84-1（y≈30349）与 26-ZD-JCXT-167-1（y≈29134）x 仅差 3.5，
    而 167-1 的层底深度文字 x 恰好比 Z84-1 的编号右 0.065 —— 旧的"只比 dx"
    口径会把 167-1 的文字全判给 Z84-1，使 Z84-1 的"深度0点基准"被污染成 167-1
    的值，进而把 167-1 的标贯全部收走，形成"标贯与库一致却报差异"的残留误判。

    返回 (钻孔名, 水平距离dx, 竖向距离dy)；无候选时返回 (None, None, None)。
    """
    best_bh, best_dx, best_dy = None, None, None
    for bh, (bx, by) in boreholes.items():
        dx = d['x'] - bx
        if not (0 <= dx <= SEARCH_RADIUS) or d['y'] > by + 5:
            continue
        dy = by - d['y']
        if best_bh is None:
            best_bh, best_dx, best_dy = bh, dx, dy
        elif dy < best_dy - DEPTH_LEVEL_TOL:
            best_bh, best_dx, best_dy = bh, dx, dy
        elif abs(dy - best_dy) <= DEPTH_LEVEL_TOL and dx < best_dx:
            best_bh, best_dx, best_dy = bh, dx, dy
    return best_bh, best_dx, best_dy


def _assign_depth_texts_to_boreholes(boreholes, depth_texts):
    """把"层底深度"文字归属到钻孔，返回 {钻孔: [(深度m, 文字Y), ...]}。

    归属口径见 _pick_depth_owner（本列正上方最近 → 同高度再比水平距离）。
    """
    groups = defaultdict(list)
    for d in depth_texts:
        if d['depth'] <= 0:
            continue
        best_bh, _dx, _dy = _pick_depth_owner(boreholes, d)
        if best_bh is not None:
            groups[best_bh].append((d['depth'], d['y']))
    return groups


def _derive_v_scale(boreholes, depth_texts, default=VERTICAL_SCALE):
    """从本张DXF自动推算竖向比例（每米深度对应的CAD单位）。

    V3.0.7 修正（根因修复）——改用【同一钻孔内部】的层底深度文字差分求斜率：
        v = (浅层文字Y − 深层文字Y) / (深层深度 − 浅层深度)
    同一孔内"孔口标注文字并不落在真正的 0 深度线上"这一系统偏移，会在差分中
    自动抵消，因此结果无偏。实测江村西展布图：真值 **5.0000**（孔内差分中位数，
    6113 个样本），而旧的"全局 (孔口Y−文字Y)/深度 取中位数"估成 **5.6000（+12%）**
    —— 该偏差使 CAD 标贯换算深度随深度线性漂移（浅部 +1.0m、深部 −1.5m），
    正是"标贯与数据库一致却报差异"的根因。

    返回 float；样本不足时回退旧口径，再不足回退 default。
    """
    groups = _assign_depth_texts_to_boreholes(boreholes, depth_texts)
    slopes = []
    for _bh, arr in groups.items():
        if len(arr) < 2:
            continue
        arr.sort()
        (d0, y0), (d1, y1) = arr[0], arr[-1]
        if d1 - d0 > 0.5:
            s = (y0 - y1) / (d1 - d0)
            # 工程纵断面合理区间 1:20 ~ 1:2000（0.5 ~ 50 单位/米）
            if 0.5 <= s <= 50:
                slopes.append(s)
    if len(slopes) >= 3:
        return statistics.median(slopes)

    # ---- 兜底（孔内样本不足，如极稀疏小图）：退化为"全局 (孔口Y−文字Y)/深度
    #      取中位数"，归属口径与孔内法保持一致（_assign_depth_texts_to_boreholes）----
    ratios = []
    for _bh, arr in _assign_depth_texts_to_boreholes(boreholes, depth_texts).items():
        for depth, y in arr:
            if depth <= 0:
                continue
            off = boreholes[_bh][1] - y
            if off > 0:
                r = off / depth
                if 0.5 <= r <= 50:
                    ratios.append(r)
    if len(ratios) < 3:
        return default
    return statistics.median(ratios)


def _derive_datum_map(boreholes, depth_texts, v_scale):
    """每孔"深度0点"的CAD Y 基准（V3.0.7 新增，根因修复）。

    问题：孔的 0 深度线并不在"孔口标注"文字的 Y 上——实测江村西展布图里
    孔口标注比真正的 0 深度线**高约 8.4 绘图单位（≈1.7m）**，直接用孔口标注 Y
    换算标贯深度会整体偏浅。

    办法：用本孔"层底深度"文字反推基准——文字 Y 与真实 0 线的距离恰为
    v_scale × 深度，故 datum = Y_文字 + v_scale × 深度；同孔多条取中位数。
    实测同一孔的各条反推值完全一致（如 30341.2），基准可靠。

    稳健化：同一孔的各条反推值本应完全一致，故先取中位数，再剔除偏离中位数
    超过 max(2.0, v_scale×1)（≈1m）的离群条（典型来源：极深文字被相邻下方
    孔抢走归属），只用剩余条重新取中位数，避免单条错配污染整孔基准。

    返回 {钻孔: 基准Y}；推导不出该孔基准的孔不出现，调用方回退孔口标注 Y。
    """
    datums = {}
    for bh, arr in _assign_depth_texts_to_boreholes(boreholes, depth_texts).items():
        vals = [y + v_scale * d for d, y in arr]
        if not vals:
            continue
        med = statistics.median(vals)
        tol = max(2.0, v_scale * 1.0)
        kept = [v for v in vals if abs(v - med) <= tol]
        datums[bh] = statistics.median(kept) if kept else med
    return datums



def _scan_dxf_file(path, prefix, db_depth_map=None, v_scale=VERTICAL_SCALE, db_spt=None):
    """直接解析DXF文本文件（极快，秒级）"""
    if db_depth_map is None:
        db_depth_map = {}
    boreholes = {}
    depth_texts = []

    # 自动检测编码：GBK 或 UTF-8
    for enc in ['gbk', 'utf-8', 'gb2312', 'utf-16']:
        try:
            with open(path, 'r', encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

    lines = [l.strip() for l in content.replace('\r\n', '\n').replace('\r', '\n').split('\n')]

    # 先解析 BLOCKS 区，建立块名→文本实体列表的映射
    block_texts = _parse_blocks(lines)

    in_entities = False
    i = 0
    while i < len(lines):
        code = lines[i]
        if not in_entities:
            if code == 'ENTITIES':
                in_entities = True
            i += 1
            continue
        if code == 'ENDSEC':
            break
        if code != '0' or i >= len(lines) - 1:
            i += 1
            continue
        nxt = lines[i + 1]
        # 处理 INSERT → 展开块内文本
        if nxt == 'INSERT' and block_texts:
            expanded, k = _expand_insert_entities(lines, block_texts, i)
            for be in expanded:
                txt = be['txt']
                style = be.get('style', '')
                x, y = be['x'], be['y']
                txt_s = txt.strip()
                if style == '孔口标注' and txt_s.startswith(prefix) and not any(
                        kw in txt_s for kw in ('缺', '无', '标贯', '水位', '备注')):
                    boreholes[txt_s] = (x, y)
                elif style == '层底深度':
                    # 仅认样式=层底深度，避免块内纯数字(标高/桩号)误判
                    m = re.match(r'([\d.]+)', txt)
                    if m:
                        depth_texts.append({'x': x, 'y': y, 'depth': float(m.group(1))})
            i = k; continue

        if not _is_text_entity(nxt):
            i += 1
            continue

        # 提取 TEXT/MTEXT 属性
        props = {}
        j = i + 2
        while j < len(lines):
            gc = lines[j]
            if gc == '0' or _is_text_entity(gc):
                break
            if j + 1 < len(lines):
                props[gc] = lines[j + 1]
            j += 2
        i = j  # 跳过已解析的属性行

        txt = _clean_mtext(props.get('1', '').strip())
        style = props.get('7', '')
        try:
            x = float(props.get('10', 0))
            y = float(props.get('20', 0))
        except (ValueError, TypeError):
            continue

        txt_s = txt.strip()
        if style == '孔口标注' and txt_s.startswith(prefix) and not any(
                kw in txt_s for kw in ('缺', '无', '标贯', '水位', '备注')):
            boreholes[txt_s] = (x, y)
        elif style == '层底深度':
            m = re.match(r'([\d.]+)', txt)
            if m:
                depth_texts.append({'x': x, 'y': y, 'depth': float(m.group(1))})

    # ---- 未施工/无深度标注标记 ----
    unconstructed = set()
    lines2 = lines  # 重用已加载的行
    i2 = 0
    while i2 < len(lines2):
        code = lines2[i2]
        if code != '0' or i2 >= len(lines2) - 1:
            i2 += 1; continue
        if not _is_text_entity(lines2[i2 + 1]):
            i2 += 1; continue
        props = {}
        j = i2 + 2
        while j < len(lines2):
            gc = lines2[j]
            if gc == '0' or _is_text_entity(gc): break
            if j + 1 < len(lines2): props[gc] = lines2[j + 1]
            j += 2
        i2 = j
        txt = _clean_mtext(props.get('1', '').strip())
        if any(kw in txt for kw in ('未施工', '平面图', '未钻', '未作', '未做')):
            try:
                x = float(props.get('10', 0))
                y = float(props.get('20', 0))
                for bh, (bx, by) in boreholes.items():
                    if 0 <= x - bx <= SEARCH_RADIUS and abs(y - by) < 5:
                        unconstructed.add(bh)
            except (ValueError, TypeError):
                pass

    # 最近匹配：每个层底深度分配给右侧最近的钻孔。
    # 但若该深度离最近已识别孔口的水平距离过远（> 隔离阈值），说明它极可能
    # 属于一个“未被识别的相邻钻孔”——硬塞给最近孔会给邻居孔造成假异常
    # （典型现象：75-1 凭空多出了本属于 75-2 的深度）。故将其隔离到
    # depth_isolated，由报告提示人工核对，不再污染任何孔。
    # 隔离阈值：基于已识别孔口的平均水平间距（中位数稳健，抗离群）动态估算，
    # 夹在 [8, 15] 之间。孔越密，阈值越严。
    xs = sorted(bx for bx, _by in boreholes.values())
    gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    med_gap = statistics.median(gaps) if gaps else SEARCH_RADIUS
    isolate_dist = max(8.0, min(med_gap * 0.7, 15.0))

    # 竖向比例提前推算（用于层柱竖向跨度上限）
    v_scale = _derive_v_scale(boreholes, depth_texts, default=v_scale)
    # 每孔"深度0点"基准（V3.0.7）：孔口标注 Y 偏高，标贯换算需用层底文字反推的基准
    datum_map = _derive_datum_map(boreholes, depth_texts, v_scale)
    # 层柱竖向跨度上限：深度文字必须落在本孔“真实层柱”范围内
    # （dy = 孔口y − 文字y，CAD单位）。允许的竖向跨度≈120m以深余量；
    # 超出者判定为“属于另一个未被识别、但x坐标与本孔重合的钻孔”，隔离提示，
    # 不再错配给本孔（典型现象：75-1 凭空多出本属于下方另一孔的层底深度）。
    column_drop_max = v_scale * 120 + 50

    dxf_depths = {}
    depth_isolated = []   # [{'x','y','depth','nearest','dist','reason'}]
    for d in depth_texts:
        # V3.0.7 根因③：归属改用"本列正上方最近 → 同高度再比水平距离"，
        # 与 _assign_depth_texts_to_boreholes（竖向比例/基准反推）口径一致，
        # 避免上下叠放两排孔时刻度文字跨排错配。
        best_bh, best_dist, dy_drop = _pick_depth_owner(boreholes, d)
        if best_bh:
            if best_dist <= isolate_dist and dy_drop <= column_drop_max:
                if best_bh not in dxf_depths:
                    dxf_depths[best_bh] = []
                dxf_depths[best_bh].append(d['depth'])
            else:
                depth_isolated.append({
                    'x': d['x'], 'y': d['y'], 'depth': d['depth'],
                    'nearest': best_bh, 'dist': round(best_dist, 1),
                    'reason': '水平过远' if best_dist > isolate_dist else '竖向超界',
                })
    for bh in dxf_depths:
        dxf_depths[bh] = sorted(dxf_depths[bh])

    # ---- 竖向比例已在深度归属前推算（用于层柱竖向跨度上限），此处沿用 ----

    # ---- 标贯击数扫描（两阶段：常规 + 缺失孔细致扫描）----
    spt_data, spt_warnings, spt_supplemented = _scan_spt_from_dxf(
        lines, boreholes, block_texts,
        db_depth_map=db_depth_map, v_scale=v_scale, db_spt=db_spt,
        datum_map=datum_map)

    # 过滤：无深度但不录入异常的（已施工钻孔）
    no_depth_bhs_raw = sorted(set(boreholes.keys()) - set(dxf_depths.keys()))
    no_depth_bhs = [bh for bh in no_depth_bhs_raw if bh not in unconstructed]
    return dxf_depths, boreholes, no_depth_bhs, spt_data, v_scale, spt_warnings, spt_supplemented, depth_isolated


# 动探/标贯统一正则（常规扫描）：匹配 N= / N63.5= / N120= / N10= 等变体
#   group(1)=类型(N/N10/N63.5/N120)，group(2)=击数
_SPT_RE = re.compile(r'(N63\.5|N120|N10|N)\s*=\s*([\d.]+)', re.IGNORECASE)

# 二次细致扫描放宽正则（不放松窗口，仅加强解析）：
#   1) N 前缀支持全角等号(N＝12)、冒号(N:12)；排除动探 N63.5/N120/N10
#   2) "击"后缀写法(12击)
#   命中分支：group(1)=N=xx 击数，group(2)=xx击 击数
_SPT_RE_LOOSE = re.compile(
    r'(?:N(?!63\.5|120|10)\s*[=：:＝]\s*(\d+(?:\.\d+)?)'   # N=12 / N＝12 / N:12
    r'|(\d+(?:\.\d+)?)\s*击)'                                # 12击
    , re.IGNORECASE)

# 动探文字硬过滤：N63.5 / N120 / N10（重型动力触探）无论是否带"击"后缀，
# 一律不作标贯计入。放在两阶段匹配最前，确保动探"X击"写法不会漏进标贯。
_DYNA_RE = re.compile(r'N(?:63\.5|120|10)\b', re.IGNORECASE)


def _scan_spt_from_dxf(lines, boreholes, block_texts=None, db_depth_map=None,
                       v_scale=VERTICAL_SCALE, db_spt=None, datum_map=None):
    """从DXF中扫描标贯击数（两阶段自适应）。

    窗口固定（不放松）：水平=钻孔编号右侧 [bx, bx+SPT_RIGHT_OFFSET]；
    竖向=孔口 by 向下到 本孔DB最深地层深度×竖向比例 处。

    第一阶段（常规）：仅取样式=='标贯动探' 的 N=xx（排除动探 N10/N63.5/N120）。
    第二阶段（细致）：对"CAD标贯条数 < DB条数"的孔，在固定窗口内加强解析——
        样式放宽到含'标贯'、正则兼容全角等号/冒号/"击"后缀，捞回被格式/样式卡掉的标贯。
        补充标贯单独返回 spt_supplemented，并标记 supplemented 并入 spt_data。

    参数:
        lines:          DXF 行列表
        boreholes:      {钻孔编号: (bx, by)}，由孔口标注得到
        block_texts:    BLOCKS 区解析结果（块内文本）
        db_depth_map:   {钻孔编号: 本孔DB最深地层深度(米)}；None 时不限下界
        v_scale:        竖向比例（每米对应的CAD单位）
        db_spt:         {钻孔编号: [击数,...]} 数据库标贯，用于触发二次扫描；None 不触发
    返回:
        (spt_data, spt_warnings, spt_supplemented)
    """
    if block_texts is None:
        block_texts = {}
    if db_depth_map is None:
        db_depth_map = {}
    if db_spt is None:
        db_spt = {}
    if datum_map is None:
        datum_map = {}

    # 全图最深地层（仅作参考/兜底；标贯下界实际由"本孔层柱 & 最近下方孔口"双重约束）
    _mv = [v for v in db_depth_map.values() if v]
    max_depth_m = max(_mv) if _mv else 0.0

    # 第一遍：收集全部文本实体（含块内展开），供常规与二次细致扫描共用。
    # 不做样式/类型过滤，所有文字原样留存，二次扫描时再按不同规则匹配。
    all_texts = []

    def _push(txt, x, y, style):
        all_texts.append({'txt': txt, 'x': x, 'y': y, 'style': style})

    # 先定位到 ENTITIES 区
    in_entities = False
    i = 0
    while i < len(lines):
        if lines[i] == 'ENTITIES':
            in_entities = True
            i += 1; break
        i += 1

    while i < len(lines):
        code = lines[i]
        if code == 'ENDSEC':
            break
        if code != '0' or i >= len(lines) - 1:
            i += 1; continue
        nxt = lines[i + 1]
        # 处理 INSERT → 展开块内文本
        if nxt == 'INSERT' and block_texts:
            expanded, k = _expand_insert_entities(lines, block_texts, i)
            for be in expanded:
                _push(be['txt'], be['x'], be['y'], be.get('style', ''))
            i = k; continue
        if not _is_text_entity(nxt):
            i += 1; continue
        props = {}
        j = i + 2
        while j < len(lines):
            gc = lines[j]
            if gc == '0' or _is_text_entity(gc): break
            if j + 1 < len(lines): props[gc] = lines[j + 1]
            j += 2
        i = j
        txt = _clean_mtext(props.get('1', '').strip())
        try:
            x = float(props.get('10', 0))
            y = float(props.get('20', 0))
        except (ValueError, TypeError):
            continue
        _push(txt, x, y, props.get('7', ''))

    # ---- 辅助吸收深层标贯：宋体地层编号标签(如 8-33)不属任何已识别钻孔 ----
    # 宋体地层编号标签(如 8-33)在展布图里标注在层柱右侧、位于深层标贯附近，
    # 它们不属于任何已识别钻孔，但位置恰好能在 x 列上"拦截"那些因图框拼接、
    # 标贯列偏移等原因落到上方孔右侧窗口的深层标贯击数。
    # 这里把它们加入"标贯归属扩展集"(spt_structures)，让它们先吸走这些深层标贯，
    # 避免其被上方正式孔误收而报"漏读"。
    # 最终返回时过滤掉地层编号标签，不参与 DB 对比。
    _strata_re = re.compile(r'^\d+-\d+$')
    strata_labels = {}
    for t in all_texts:
        if t['style'] == '宋体' and _strata_re.match(t['txt'].strip()):
            strata_labels[t['txt']] = (t['x'], t['y'])
    spt_structures = dict(boreholes)
    spt_structures.update(strata_labels)
    # candidate_mouths 仅供 below_gap(下方孔口距离)用，仅用正式孔口位置，
    # 地层编号标签不参与(避免错误收窄正式孔标贯下界)。
    # V3.0.7：孔口位置统一改用"深度0点基准"(datum_map)，否则基准偏高 1.7m 会
    # 使标贯竖向窗口整体上移，裁掉本孔最深部的标贯。
    candidate_mouths = [(bx, datum_map.get(bh, by))
                        for bh, (bx, by) in boreholes.items()]
    below_gap = {}
    for bh, (bx, by) in spt_structures.items():
        y0 = datum_map.get(bh, by)
        best = None
        for mx, my in candidate_mouths:
            if my < y0 - 5 and abs(mx - bx) <= 15:
                d = y0 - my
                if best is None or d < best:
                    best = d
        below_gap[bh] = best

    def _match_for(style_ok, spt_re):
        """固定窗口匹配：每个标贯文字只归给"能收它的最上方孔口"，
        从本质上避免跨排/跨列错配。窗口仍为右侧12.5 + 下界约束 + 孔口之上少量容差。
        返回 (spt_data, leaked)：
          spt_data: {bh: [{'n','y'}, ...]} 按 Y 从上往下(孔口→深处)排序
          leaked:   {bh: [{'n','y'}, ...]} 落在下界之下、被裁掉的标贯(疑似漏读)
        """
        # 预计算每个孔的竖向窗口
        bounds = {}
        for bh, (bx, by) in spt_structures.items():
            own_depth_m = db_depth_map.get(bh)
            gap = below_gap.get(bh)
            # V3.0.7：竖向窗口以"每孔深度0点基准"为准（孔口标注 Y 实测偏高≈1.7m）
            y0 = datum_map.get(bh, by)
            # 本孔层柱上限(米)：优先本孔地层深度；查不到则用绝对兜底 120m。
            col_m = (own_depth_m if (own_depth_m and own_depth_m > 0)
                     else SPT_FALLBACK_DEPTH_M)
            y_bottom = y0 - (col_m * v_scale + SPT_Y_MARGIN)
            # 仅当本孔查不到地层(own=None)时，用"到最近下方孔口距离"约束下界，
            # 避免其无限制下收下方排标贯。
            if (not own_depth_m or own_depth_m <= 0) and gap:
                y_bottom = max(y_bottom, y0 - (gap - SPT_Y_MARGIN))
            bounds[bh] = (bx, y0, y0 + SPT_Y_MARGIN, y_bottom)

        collected = {bh: [] for bh in spt_structures}
        collected_below = {bh: [] for bh in spt_structures}  # 下界之下的标贯（疑似漏读，P3 修复）
        for t in all_texts:
            # 动探文字（N63.5/N120/N10，可能带"击"）一律不作标贯计入
            if _DYNA_RE.search(t['txt']):
                continue
            m = spt_re.search(t['txt'])
            if not m:
                continue
            if spt_re is _SPT_RE:
                # 常规：用 group(1) 的类型过滤掉重型动探
                if m.group(1).upper() != 'N':
                    continue
                val = m.group(2)
            else:
                # 细致：group(1)=N=xx 击数，group(2)=xx击 击数（已排除动探）
                val = m.group(1) if m.group(1) else m.group(2)
            if not style_ok(t['style']):
                continue
            try:
                n = float(val)
            except (ValueError, TypeError):
                continue
            # 找能收它的最上方孔口（dy 最小）—— 从本质上避免跨排/跨列
            best_bh, best_dy = None, None
            below_bh, below_dy = None, None  # 下界之下的候选归属（漏读预警用）
            for bh, (bx, by, y_top, y_bottom) in bounds.items():
                dx = t['x'] - bx
                if not (0 <= dx <= SPT_RIGHT_OFFSET):
                    continue
                if t['y'] > y_top:
                    continue
                dy = by - t['y']
                if t['y'] >= y_bottom:
                    if best_dy is None or dy < best_dy:
                        best_dy = dy
                        best_bh = bh
                else:
                    # 落在本孔下界之下：仍按"最近的孔口"归属，用于漏读预警
                    if below_dy is None or dy < below_dy:
                        below_dy = dy
                        below_bh = bh
            if best_bh is not None:
                collected[best_bh].append(
                    {'n': n, 'y': t['y'], 'dy0': datum_map.get(best_bh)})
            elif below_bh is not None:
                collected_below[below_bh].append(
                    {'n': n, 'y': t['y'], 'dy0': datum_map.get(below_bh)})

        spt_data = {}
        leaked = {}
        for bh, items in collected.items():
            if not items:
                continue
            matches = sorted(items, key=lambda c: c['y'], reverse=True)
            spt_data[bh] = [{'n': c['n'], 'y': c['y'], 'dy0': c.get('dy0')}
                            for c in matches]
        for bh, items in collected_below.items():
            if not items:
                continue
            # 漏读预警：仅当本孔有 DB 最深地层深度时才提示（无深度信息的孔可能是相邻孔标贯）
            own_depth_m = db_depth_map.get(bh)
            if own_depth_m and own_depth_m > 0:
                leaked[bh] = [{'n': c['n'], 'y': c['y'], 'dy0': c.get('dy0')}
                              for c in items]
        return spt_data, leaked

    # 常规扫描：样式=='标贯动探' + 标准正则（排除动探）
    spt_data, spt_warnings = _match_for(
        style_ok=lambda s: s == '标贯动探', spt_re=_SPT_RE)

    # 二次细致扫描：仅对"CAD标贯条数 < DB条数"的孔，在固定窗口内加强解析
    # （样式含'标贯' + 放宽正则），不放松右侧12.5与下界，捞回被格式/样式卡掉的标贯。
    spt_supplemented = {}
    detail_data, _ = _match_for(
        style_ok=lambda s: '标贯' in s, spt_re=_SPT_RE_LOOSE)

    # 地层编号标签仅用于内部吸收不属于任何正式孔的深层标贯，避免其污染上方孔；不参与最终 DB 对比
    spt_data = {k: v for k, v in spt_data.items() if k in boreholes}
    spt_warnings = {k: v for k, v in spt_warnings.items() if k in boreholes}
    detail_data = {k: v for k, v in detail_data.items() if k in boreholes}

    for bh in sorted(db_spt):
        cad_n = len(spt_data.get(bh, []))
        db_n = len(db_spt.get(bh, []))
        if db_n <= 0 or cad_n >= db_n:
            continue                    # 仅当 CAD 比 DB 少时触发二次扫描
        base = {(round(c['n'], 1), round(c['y'], 1)) for c in spt_data.get(bh, [])}
        added = [c for c in detail_data.get(bh, [])
                 if (round(c['n'], 1), round(c['y'], 1)) not in base]
        if not added:
            continue
        spt_supplemented[bh] = [{'n': c['n'], 'y': c['y'], 'dy0': c.get('dy0')}
                                for c in added]
        # 并入 spt_data（标记 supplemented，便于下游比对与报告单列）
        merged = spt_data.get(bh, []) + [
            {'n': c['n'], 'y': c['y'], 'dy0': c.get('dy0'),
             'supplemented': True} for c in added]
        merged.sort(key=lambda c: c['y'], reverse=True)
        spt_data[bh] = merged

    return spt_data, spt_warnings, spt_supplemented


def _scan_dxf_com(doc, prefix):
    """已弃用。DWG 不再支持，请用户先导出 DXF 再复核。
    保留函数体以便调用方获得明确提示。
    """
    raise NotImplementedError(
        "DWG 已不再支持（速度慢且依赖 AutoCAD 运行时）。"
        "请在 CAD 中执行 DXFOUT 导出为 .dxf 后再运行复核。")


def read_db_strata_da(da, prefix=None):
    """读取工作库地层深度（Web 适配，替代 read_db_strata）

    参数:
        da:     DataAccess（SQLite 工作库）
        prefix: 钻孔编号前缀（None 自动检测）

    返回:
        {钻孔编号: [{depth, name, code}, ...]}
    """
    if prefix is None:
        prefix = _auto_detect_prefix_da(da)
    strata = defaultdict(list)
    for bh_key, layers in (da.get_all_strata() or {}).items():
        bh = str(bh_key or '').strip()
        if not bh:
            continue
        # 桌面读侧口径：TCMC 优先（get_all_strata 已回填 tcymc），此处直接复用
        for s in layers:
            depth = s.get('tccdsd')
            if depth in (None, ''):
                continue
            strata[bh].append({
                "depth": float(depth),
                "name": (s.get('tcymc') or '').strip(),
                "code": (f"{s.get('tczcbh')}-{s.get('tcycbh')}"
                         if s.get('tczcbh') else ""),
            })
    return dict(strata)


def compare_depth(dxf_depths, db_strata, tolerance=TOLERANCE):
    """模糊匹配对比DXF和DB的深度列表

    参数:
        dxf_depths: {编号: [深度, ...]}
        db_strata:  {编号: [{depth, name, code}, ...]}
        tolerance:  允许误差

    返回:
        {钻孔编号: {
            "dxf": [...],           # DXF全部深度
            "db":  [...],           # DB全部深度
            "status": "ok"|"mismatch"|"db_not_found",
            "extra_dxf": [...],     # DXF有DB没有的深度
            "extra_db": [(depth, name), ...],  # DB有DXF没有的
        }}
    """
    results = {}

    for bh in sorted(dxf_depths.keys()):
        dxf_list = sorted(dxf_depths[bh])
        db_entries = db_strata.get(bh, [])
        db_list = sorted([s["depth"] for s in db_entries])
        db_info = {s["depth"]: s["name"] for s in db_entries}

        if not db_list:
            results[bh] = {
                "dxf": dxf_list, "db": [],
                "status": "db_not_found",
                "extra_dxf": dxf_list, "extra_db": [],
            }
            continue

        # 贪婪匹配：每个DXF深度找最近的未匹配DB深度
        used_db = [False] * len(db_list)
        extra_dxf = []

        for dd in dxf_list:
            best_j, best_diff = None, tolerance * 2
            for j, bd in enumerate(db_list):
                if not used_db[j] and abs(dd - bd) < best_diff:
                    best_diff = abs(dd - bd)
                    best_j = j
            if best_j is not None and best_diff <= tolerance:
                used_db[best_j] = True
            else:
                extra_dxf.append(round(dd, 2))

        extra_db = [(round(db_list[j], 2), db_info.get(db_list[j], ""))
                     for j, u in enumerate(used_db) if not u]

        if extra_dxf or extra_db:
            results[bh] = {
                "dxf": dxf_list, "db": db_list,
                "status": "mismatch",
                "extra_dxf": extra_dxf, "extra_db": extra_db,
            }
        # else: 完全一致，不加入结果（节省内存）

    return results


def format_report(results, dxf_depths, all_boreholes, no_depth_bhs, prefix="", doc_name="", db_name="", v_scale=None, spt_warnings=None, spt_supplemented=None, depth_isolated=None, spt_mismatch=None, doc_path=None):
    """生成文本报告

    参数:
        spt_warnings:    {钻孔: [{'n','y'}, ...]} 被下界裁掉的标贯（疑似漏读）
        spt_supplemented:{钻孔: [{'n','y'}, ...]} 二次细致扫描补充回收的标贯
        spt_mismatch:    {钻孔: {...}} 标贯判定不一致明细（V3.0.7 新增，含带深度的逐条对照）
        doc_path:        被复核 DXF 的完整路径（V3.0.9 新增，写入报告头部便于区分历次复核）

    返回:
        报告字符串
    """
    total = len(dxf_depths)
    n_mismatch = sum(1 for r in results.values() if r.get("status") == "mismatch")
    n_notfound = sum(1 for r in results.values() if r.get("status") == "db_not_found")

    lines = []
    lines.append("=" * 60)
    lines.append("  纵断面地层深度复核报告")
    # V3.0.9：报告正文标注"复核时间 + 图纸完整路径"。报告文件过去是固定名覆盖写，
    # 且正文只有图纸文件名、没有时间戳，用户翻看目录时无法分辨报告属于哪一次复核
    # （症状："上一次的残留没保存、影响当次成果"）。补上时间与完整路径即可唯一标识。
    lines.append(f"  复核时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if doc_name:
        lines.append(f"  图纸: {doc_name}")
    if doc_path:
        lines.append(f"  图纸路径: {doc_path}")
    if db_name:
        lines.append(f"  数据库: {db_name}")
    if v_scale is not None:
        lines.append(f"  竖向比例(每米CAD单位): {v_scale:.4f}  (≈1:{round(1000/v_scale)} 纵断面)")
    lines.append("=" * 60)
    lines.append(f"  DXF钻孔总数:           {len(all_boreholes)}")
    lines.append(f"  提取到深度:             {total}")
    lines.append(f"  无深度标注(未施工/平面图): {len(no_depth_bhs)}")
    lines.append("  ─────────────────────────────")
    lines.append(f"  深度完全一致:           {total - n_mismatch - n_notfound}")
    lines.append(f"  深度不一致:             {n_mismatch} ⚠")
    lines.append(f"  数据库无此钻孔:         {n_notfound}")
    if spt_warnings:
        n_leak = sum(len(v) for v in spt_warnings.values())
        lines.append(f"  标贯疑似漏读(超出DB深度): {len(spt_warnings)} 孔 / {n_leak} 条 ⚠")
    if spt_supplemented:
        n_sup = sum(len(v) for v in spt_supplemented.values())
        lines.append(f"  标贯补充回收(细致扫描):   {len(spt_supplemented)} 孔 / {n_sup} 条")
    if spt_mismatch:
        lines.append(f"  标贯判定不一致:         {len(spt_mismatch)} 孔 ⚠（见文末逐条对照）")
    lines.append("=" * 60)
    lines.append("")

    # 按墩台汇总
    pier_stats = defaultdict(lambda: {"total": 0, "ok": 0, "error": 0})
    for bh in all_boreholes:
        pier = re.sub(re.escape(prefix), "", bh).split("-")[0] if prefix and bh.startswith(prefix) else bh
        m = re.search(r'\d+', str(pier))
        pier_stats[pier]["total"] += 1
        if bh in no_depth_bhs:
            continue
        if bh in results and results[bh].get("status") == "mismatch":
            pier_stats[pier]["error"] += 1
        else:
            pier_stats[pier]["ok"] += 1

    lines.append("按墩台汇总:")
    lines.append(f"{'墩台':8s} {'总数':5s} {'一致':5s} {'不一致':5s}")
    lines.append("-" * 25)
    for pier in sorted(pier_stats.keys(), key=lambda p: int(re.search(r'\d+', str(p)).group()) if re.search(r'\d+', str(p)) else 0):
        s = pier_stats[pier]
        flag = " ⚠" if s["error"] > 0 else ""
        lines.append(f"  {pier:6s} {s['total']:5d} {s['ok']:5d} {s['error']:5d}{flag}")

    lines.append("")

    # 详细差异
    n_diff = 0
    for bh in sorted(results.keys()):
        info = results[bh]
        if info.get("status") != "mismatch":
            continue
        n_diff += 1
        ex_dxf = info.get("extra_dxf", [])
        ex_db = info.get("extra_db", [])

        lines.append(f"  ▶ {bh}")
        if ex_dxf:
            lines.append(f"    DXF多{len(ex_dxf)}层: {', '.join(f'{d:.2f}m' for d in ex_dxf)}")
        if ex_db:
            items = ", ".join(f"{d:.2f}m({n})" if n else f"{d:.2f}m" for d, n in ex_db)
            lines.append(f"    DB多{len(ex_db)}层: {items}")
        lines.append(f"    DXF全: {info['dxf']}")
        lines.append(f"    DB全:  {info['db']}")

    if n_notfound > 0:
        lines.append("\n--- 数据库无此钻孔 ---")
        for bh in sorted(results.keys()):
            info = results[bh]
            if info.get("status") == "db_not_found":
                lines.append(f"  {bh} DXF深度: {info['dxf']}")

    if no_depth_bhs:
        lines.append("\n--- 无深度标注(未施工/平面图) ---")
        for bh in no_depth_bhs:
            x, y = all_boreholes.get(bh, (0, 0))
            lines.append(f"  {bh} @ ({x:.0f}, {y:.0f})")

    # ---- 标贯疑似漏读明细 ----
    if spt_warnings:
        lines.append("\n--- 标贯疑似漏读(超出数据库最深地层，已自动排除，请人工核对) ---")
        for bh in sorted(spt_warnings.keys()):
            items = spt_warnings[bh]
            ns = ", ".join(f"N={c['n']:.0f}" for c in items)
            lines.append(f"  ▶ {bh}: 右侧列内有 {len(items)} 条标贯落在数据库最深地层之下 ({ns})")

    # ---- 标贯补充回收明细（二次细致扫描，固定窗口内加强解析，疑似需核对）----
    if spt_supplemented:
        lines.append("\n--- 标贯补充回收(二次细致扫描，已并入标贯对比，疑似请核对) ---")
        for bh in sorted(spt_supplemented.keys()):
            items = spt_supplemented[bh]
            ns = ", ".join(f"N={c['n']:.0f}" for c in items)
            lines.append(f"  ▶ {bh}: 补充回收 {len(items)} 条 ({ns})")

    # ---- 标贯击数逐条对照（V3.0.7：把"为什么判为差异"摊开，便于人工复核）----
    if spt_mismatch:
        lines.append("\n--- 标贯击数逐条对照(仅列判定不一致的钻孔；CAD↔数据库 按深度) ---")
        for bh in sorted(spt_mismatch.keys()):
            sm = spt_mismatch[bh]
            if not isinstance(sm, dict):
                continue
            cpts = sm.get('cad_pts')
            dpts = sm.get('db_pts')
            if cpts:
                lines.append(f"  ▶ {bh}")
                lines.append("      CAD : " + " / ".join(
                    f"{d:.2f}m N={n:g}" for d, n in cpts))
                lines.append("      数据库: " + " / ".join(
                    f"{d:.2f}m {n:g}" for d, n in (dpts or [])))
            else:
                lines.append(f"  ▶ {bh}（无深度信息，按顺序比对）")
                lines.append("      CAD : " + ", ".join(
                    f"N={v:g}" for v in (sm.get('cad') or [])))
                lines.append("      数据库: " + ", ".join(
                    f"{v:g}" for v in (sm.get('db') or [])))
            reasons = []
            for d, cn, dn in (sm.get('diffs') or []):
                reasons.append(f"≈{float(d):.2f}m 击数不同(CAD {cn:g} / 库 {dn:g})")
            if sm.get('extra_cad'):
                reasons.append("CAD多出 " + ", ".join(f"N={v:g}" for v in sm['extra_cad']))
            if sm.get('extra_db'):
                reasons.append("库多出 " + ", ".join(f"{v:g}" for v in sm['extra_db']))
            lines.append("      判定: " + ("；".join(reasons) if reasons else "—"))

    # ---- 无法归属的层底深度（疑似对应未被识别的钻孔）----
    if depth_isolated:
        lines.append("\n--- 无法归属的层底深度(疑似有钻孔未被识别，请核对) ---")
        lines.append(f"  共 {len(depth_isolated)} 条层底深度数字无法可靠归属到任何已识别钻孔：")
        lines.append("  （它们离最近钻孔【水平过远】或【竖向远超本孔层柱】；极可能属于图中")
        lines.append("   未被识别的相邻钻孔；这些数字已不参与任何孔的对比，以免给正常孔造成假异常）")
        for it in sorted(depth_isolated, key=lambda c: c['x'])[:30]:
            reason = it.get('reason', '')
            lines.append(f"  @({it['x']:.0f},{it['y']:.0f}) 深度{it['depth']:.2f}m "
                         f"最近孔={it['nearest']}(距离{it['dist']:.1f})"
                         + (f" [{reason}]" if reason else ""))
        if len(depth_isolated) > 30:
            lines.append(f"  ... 其余 {len(depth_isolated) - 30} 条省略")

    return "\n".join(lines)


def read_db_spt_da(da, prefix=None):
    """读取工作库标贯击数（Web 适配，替代 read_db_spt）

    返回 {钻孔编号: [(深度, 击数), ...]}：深度用于与 CAD 标贯按深度对齐比对
    （P3 位置比对修复，与桌面同口径）。
    """
    if prefix is None:
        prefix = _auto_detect_prefix_da(da)
    spt = defaultdict(list)
    for bh, rows in (da.get_all_spt() or {}).items():
        bh = str(bh or '').strip()
        if not bh:
            continue
        for r in rows:
            depth = r.get('bgdsd')
            n = r.get('bgjs')
            if depth in (None, '') or n in (None, ''):
                continue
            spt[bh].append((round(float(depth), 2), round(float(n), 1)))
    return dict(spt)


# ======================== 标贯序列对齐（V3.0.7） ========================
# 背景（V3.0.7 修复"标贯值一致却报差异"）：
#   旧逻辑用"严格深度公差(深度公差, 默认0.05m) + 贪婪最近"匹配 CAD 与 DB 的标贯。
#   但 CAD 侧标贯深度是由 (孔口Y − 文字Y) / 竖向比例 换算而来：竖向比例估计偏差、
#   标贯文字插入点锚点(基线/中心)差异，都会产生"随深度放大的"小偏差。当某条标贯
#   的换算深度偏离 DB 深度超过 0.05m 时，它会被记入 extra_cad，对应的 DB 记录被记入
#   extra_db —— 两者击数其实一模一样，界面却把 extra 与 diffs 一并显示为"标贯差异"，
#   于是表现为"CAD 与数据库击数完全一致，却报有差异"（两张一样的清单）。
#   现改为序列对齐（同时考虑"击数值"与"深度接近度"）：
#     · 击数相同且深度相近 → 配对，代价≈0（不再误报）
#     · 配对后击数真的不同 → 计入 diffs（真差异，附深度）
#     · 一侧真的多出/缺失 → 计入 extra_cad / extra_db
_ALIGN_MIS = 1.0          # 击数不同的对齐代价
_ALIGN_GAP = 1.0          # 单侧缺口（一侧多读/漏读）代价
_ALIGN_FAR_REL = 0.5      # 相对深度偏离上限：超过即禁配（防止跨层位硬配）
_ALIGN_FAR = 1.0e9        # 禁配代价


def _align_spt_seqs(cad_pts, db_pts):
    """同一钻孔的 CAD / DB 标贯按"击数值 + 深度接近度"做最优序列对齐。

    参数:
        cad_pts: [(深度m, 击数), ...]，按深度升序
        db_pts:  [(深度m, 击数), ...]，按深度升序

    返回:
        (diffs, extra_cad, extra_db)
        diffs:     [(深度m, CAD击数, DB击数), ...] 已配对但击数不同（真差异）
        extra_cad: [击数, ...] CAD 侧多出（DB 无对应）
        extra_db:  [击数, ...] DB 侧多出（CAD 无对应）
    """
    nA, nB = len(cad_pts), len(db_pts)
    if nA == 0 or nB == 0:
        return [], [n for _d, n in cad_pts], [n for _d, n in db_pts]
    # 深度偏离一律用【相对量】：以两点平均深度为尺度。
    # V3.0.7（根因修复）：绝对米数罚项会被"随深度放大的"竖向比例/锚点偏差
    # 在深部顶到封顶值，与"缺口"同价，于是本可配对的两点被拆开而误报；
    # 相对量则与深度无关，任何比例性偏差都不会破坏配对。
    def _rel(da, db):
        return abs(da - db) / max((abs(da) + abs(db)) / 2.0, 2.0)

    def _sub_cost(i, j):
        da, na = cad_pts[i - 1]
        db, nb = db_pts[j - 1]
        rel = _rel(da, db)
        if rel > _ALIGN_FAR_REL:
            return _ALIGN_FAR
        return (0.0 if abs(na - nb) < 1e-6 else _ALIGN_MIS) + min(rel, 1.0)

    # dp[i][j]：cad 前 i 条 与 db 前 j 条 的最小对齐代价
    dp = [[0.0] * (nB + 1) for _ in range(nA + 1)]
    for i in range(1, nA + 1):
        dp[i][0] = dp[i - 1][0] + _ALIGN_GAP
    for j in range(1, nB + 1):
        dp[0][j] = dp[0][j - 1] + _ALIGN_GAP
    for i in range(1, nA + 1):
        for j in range(1, nB + 1):
            dp[i][j] = min(
                dp[i - 1][j - 1] + _sub_cost(i, j),
                dp[i - 1][j] + _ALIGN_GAP,
                dp[i][j - 1] + _ALIGN_GAP,
            )

    # 回溯（优先取"配对"分支，保证击数相同的点尽量成对）
    diffs, extra_cad, extra_db = [], [], []
    i, j = nA, nB
    while i > 0 or j > 0:
        if i > 0 and j > 0 and abs(
                dp[i][j] - (dp[i - 1][j - 1] + _sub_cost(i, j))) < 1e-9:
            da, na = cad_pts[i - 1]
            db, nb = db_pts[j - 1]
            if abs(na - nb) > 1e-6:
                diffs.append((round(da, 2), na, nb))
            i -= 1; j -= 1; continue
        if i > 0 and abs(dp[i][j] - (dp[i - 1][j] + _ALIGN_GAP)) < 1e-9:
            extra_cad.append(cad_pts[i - 1][1]); i -= 1; continue
        if j > 0:
            extra_db.append(db_pts[j - 1][1]); j -= 1; continue
        # 浮点极端兜底，避免死循环
        if i > 0:
            extra_cad.append(cad_pts[i - 1][1]); i -= 1
        else:
            extra_db.append(db_pts[j - 1][1]); j -= 1
    diffs.reverse(); extra_cad.reverse(); extra_db.reverse()
    return diffs, extra_cad, extra_db


def check_depth_da(dxf_path, da, prefix=None):
    """主入口（Web 适配）：读取DXF + 从 DataAccess 读库 + 对比

    参数:
        dxf_path: DXF 文件路径（必须是 .dxf，DWG 不支持）
        da:       DataAccess（SQLite 工作库）
        prefix:   钻孔编号前缀（None 自动检测）

    返回:
        (results, dxf_depths, all_boreholes, no_depth_bhs, report_text,
         spt_cad, spt_mismatch, v_scale_used, spt_warnings, spt_supplemented)
    """
    if dxf_path is None:
        raise ValueError("必须提供 dxf_path 参数（DXF 文件路径）")
    if not isinstance(dxf_path, str):
        raise TypeError("dxf_path 必须是字符串形式的 DXF 文件路径")
    if not dxf_path.lower().endswith('.dxf'):
        raise ValueError(
            f"仅支持 DXF 文件（当前: {os.path.basename(dxf_path)}）。"
            f"请在 CAD 中执行 DXFOUT 导出为 .dxf 后再运行复核。")
    if not os.path.exists(dxf_path):
        raise FileNotFoundError(f"DXF 文件不存在: {dxf_path}")
    if da is None:
        raise ValueError("必须提供 da（DataAccess）")

    if prefix is None:
        prefix = _auto_detect_prefix_da(da)

    # 先读数据库，获取地层深度（用于推算标贯竖向搜索下界：每孔最深地层）
    db_strata = read_db_strata_da(da, prefix)
    db_depth_map = {}  # {bh: 本孔最深地层深度(米)}
    for _bh, layers in db_strata.items():
        if layers:
            db_depth_map[_bh] = max(s["depth"] for s in layers)
    # 数据库标贯（用于触发二次细致扫描：CAD条数 < DB条数 的孔）
    db_spt = read_db_spt_da(da, prefix)

    # 仅 DXF 直读路径（DWG/COM 已移除，与桌面一致）
    (dxf_depths, all_bh, no_depth, spt_cad,
     v_scale_used, spt_warnings, spt_supplemented, depth_isolated) = scan_dxf_depth(
        dxf_path, prefix, db_depth_map=db_depth_map, v_scale=VERTICAL_SCALE,
        db_spt=db_spt)
    doc_label = os.path.basename(dxf_path)
    # 仅对比"数据库中也存在"的钻孔；图里画了但本库未录入的孔不计入异常
    # （避免把"尚未进库"的孔误报为深度/标贯错误）
    db_bh = set(db_strata.keys())
    results = compare_depth({bh: d for bh, d in dxf_depths.items() if bh in db_bh},
                            db_strata)

    # 标贯对比：仅对比 CAD 与 DB 都有的钻孔（db_spt 已在上方读取）
    spt_mismatch = {}  # {bh: {'diffs': [...], 'extra_cad': [...], 'extra_db': [...],
                       #        'cad': [...], 'db': [...]}}
    for bh in sorted(set(spt_cad) & set(db_spt)):
        cad = spt_cad.get(bh, [])
        db = db_spt.get(bh, [])   # [(深度m, 击数), ...]
        # 统一为 N 值列表（新结构是 {'n', 'y'}，兼容旧 list[float]）
        cad_vals = [c['n'] if isinstance(c, dict) else c for c in cad]
        db_vals = [x[1] for x in db]
        diffs = []
        # CAD 标贯文字 y → 深度 m：(孔口 y − 标贯 y) / 竖向比例，再与 DB BGDSD 比对。
        # （P3：不按位置索引比对，避免"中间漏读又被补扫"造成整段错位；
        #  V3.0.7：配对算法由"严格公差贪婪"升级为"值+深度序列对齐"，见 _align_spt_seqs。）
        # V3.0.7：深度0点优先用"每孔基准"（标贯项携带的 dy0，由本孔层底深度文字反推）；
        # 取不到基准的孔回退孔口标注 Y（旧行为）。孔口标注 Y 实测比真实0线高约1.7m。
        label_y = all_bh.get(bh, (None, None))[1] if all_bh.get(bh) else None
        _datums = [c.get('dy0') for c in cad
                   if isinstance(c, dict) and c.get('dy0') is not None]
        use_y0 = statistics.median(_datums) if _datums and len(_datums) == len(cad) else label_y
        use_depth = bool(v_scale_used and v_scale_used > 0 and use_y0 is not None)
        cad_depths = None
        if use_depth:
            cad_depths = [(use_y0 - c['y']) / v_scale_used
                          for c in cad if isinstance(c, dict) and c.get('y') is not None]
            use_depth = use_depth and len(cad_depths) == len(cad) and len(db) > 0
        # 供对齐与"逐条对照"报告使用的 (深度m, 击数) 序列（无法换算深度时为 None）
        cad_pts = db_pts = None
        if use_depth and cad_depths:
            # V3.0.7：改为"值+深度"序列对齐（见 _align_spt_seqs 注释）——
            # 不再用严格公差贪婪匹配，避免"随深度放大的换算偏差"把击数一致的
            # 标贯同时甩进 extra_cad / extra_db（界面表现为"两张一样清单却报差异"）。
            cad_pts = sorted(
                ((cad_depths[i], cad_vals[i]) for i in range(len(cad_depths))),
                key=lambda t: t[0])
            db_pts = sorted(((float(dd), nn) for dd, nn in db), key=lambda t: t[0])
            diffs, extra_cad, extra_db = _align_spt_seqs(cad_pts, db_pts)
        else:
            # 深度信息不完整（旧结构/无竖向比例）→ 退化为按索引比对
            diffs = []
            for idx in range(min(len(cad_vals), len(db_vals))):
                if abs(float(cad_vals[idx]) - float(db_vals[idx])) > 1e-6:
                    diffs.append((idx + 1, cad_vals[idx], db_vals[idx]))
            extra_cad = cad_vals[len(db_vals):]
            extra_db = db_vals[len(cad_vals):]
        if diffs or extra_cad or extra_db:
            spt_mismatch[bh] = {
                'diffs': diffs,
                'extra_cad': extra_cad,
                'extra_db': extra_db,
                'cad': cad_vals,
                'db': db_vals,
                # V3.0.7：附上带深度的逐条序列，供报告"标贯逐条对照"与人工复核
                'cad_pts': cad_pts,
                'db_pts': db_pts,
            }
    report = format_report(
        results, dxf_depths, all_bh, no_depth,
        prefix=prefix,
        doc_name=doc_label,
        doc_path=dxf_path,
        db_name=os.path.basename(getattr(da, 'db_path', '') or '工作库'),
        v_scale=v_scale_used,
        spt_warnings=spt_warnings,
        spt_supplemented=spt_supplemented,
        depth_isolated=depth_isolated,
        spt_mismatch=spt_mismatch,
    )
    return (results, dxf_depths, all_bh, no_depth, report, spt_cad,
            spt_mismatch, v_scale_used, spt_warnings, spt_supplemented)


def check_depth(dxf_path=None, mdb_path=None):
    """【已迁移】桌面版 MDB 直连入口不再可用（Web 架构：SQLite 工作库）。

    请改用 check_depth_da(dxf_path, da)。
    """
    raise NotImplementedError(
        "Web 版不支持 MDB 直连复核（无 pypyodbc/Jet 运行时）。"
        "请使用 check_depth_da(dxf_path, da)，da 为 SQLite 工作库的 DataAccess。")


# ======================== 独立运行 ========================
if __name__ == "__main__":
    print("用法（Web 后端）: from review.dxf_depth_check import check_depth_da; "
          "check_depth_da(<dxf文件>, <DataAccess>)")
