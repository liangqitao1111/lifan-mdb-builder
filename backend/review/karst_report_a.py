"""理反 — A类工程岩溶统计报告生成模块
按整个场地进行统计，去除墩台分类。
不修改现有 karst_report.py，新增独立模块。
"""
import os
import datetime
import time

from karst_report import (
    extract_filling_from_tcms, judge_development,
    _write_table8, _build_cave_stats,
    CAVE_TYPES, SOLUBLE_ROCK_TYPES,
)
from applog import get_logger


def reload_from_config():
    """配置保存后重建父模块 karst_report 的派生常量（P1-1）

    本模块经 `from karst_report import ...` 绑定的是【顶层】 karst_report 模块
    实例（sys.path 同时含 backend 与 backend/review 时，'karst_report' 与
    'review.karst_report' 是同一文件加载的两个独立模块对象）。review_api 的
    _reload_config_modules 只重建 'review.karst_report'，顶层实例的 STAT_VARS/
    阈值会永久陈旧（A 类附表 8A 分档与发育程度新旧混用）——此处对两个名字
    都执行 _build_karst_constants。
    """
    import sys as _sys
    for _name in ('karst_report', 'review.karst_report'):
        _mod = _sys.modules.get(_name)
        if _mod is None:
            continue
        _fn = getattr(_mod, '_build_karst_constants', None)
        if callable(_fn):
            try:
                _fn()
            except Exception:
                pass


def _template_path_a():
    """A 类附表7 模板路径（独立函数，便于测试注入替代模板验证裁剪逻辑）"""
    base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base, '参数', '岩溶率统计表（A类）.xlsx')


def _safe_save_xlsx(wb, path):
    """安全保存 Excel：若目标文件被占用（PermissionError），自动加时间戳另存"""
    try:
        wb.save(path)
        return path
    except PermissionError:
        # 文件被占用（如 Excel 打开中），加时间戳另存
        base, ext = os.path.splitext(path)
        ts = datetime.datetime.now().strftime('%H%M%S')
        new_path = f"{base}_{ts}{ext}"
        wb.save(new_path)
        return new_path


def generate_karst_report_a(da, out_dir):
    """A类岩溶统计：按整个场地统计，去除墩台分组
    返回 (table7_path, table8_path)
    """
    # ---- 1. 加载数据 ----
    boreholes = [b for b in da.get_all_boreholes() if b['zksd'] > 0]
    all_strata = {}
    for b in boreholes:
        all_strata[b['zkbh']] = da.get_strata('', b['zkbh'])

    # ---- 2. 构建输出行（无墩台列）----
    output_rows = []
    site_total_soluble = 0.0
    site_total_cave = 0.0
    site_holes_with_soluble = 0
    site_holes_with_cave = 0

    for b in boreholes:
        zkbh = b['zkbh']
        strata = all_strata[zkbh]
        zkbg = b['zkbg']
        zksd = b['zksd']
        zklc = b.get('zklc', 0) or 0
        zkpil = b.get('zkpil', 0) or 0

        prev_depth = 0.0
        soluble_thick = 0.0
        cave_thick = 0.0
        cave_rows = []
        bedrock_depth = None

        for s in strata:
            name = s.get('tcymc', '')
            bottom = s.get('tccdsd', 0)
            thick = s.get('tchd', 0) or (bottom - prev_depth)

            if name in SOLUBLE_ROCK_TYPES:
                soluble_thick += thick
                if bedrock_depth is None:
                    bedrock_depth = prev_depth

            # V2.2.2 K2：高程 0 真值——地面标高 zkbg=0 时仍计算高程（is not None 判定）
            # P1-3：与父模块 karst_report（thick>0 才计溶洞）口径对齐——tchd=0/缺失
            # 且层顶=层底（bottom==prev）的零厚"溶洞"行不进入附表 7A，避免与附表 8A 条数分裂
            if name in CAVE_TYPES and thick > 0:
                top = bottom - thick
                top_elev = zkbg - top if zkbg is not None else None
                bottom_elev = zkbg - bottom if zkbg is not None else None
                desc = s.get('tcms', '')
                filling, has_fd, fs = extract_filling_from_tcms(desc, name)
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

        cave_rows.sort(key=lambda cr: cr['top'])

        if soluble_thick <= 0:
            continue  # 无可溶岩 → 跳过

        site_total_soluble += soluble_thick
        site_holes_with_soluble += 1

        # V3.0.3（S5）：孔级线岩溶率统一为百分比数值（×100，一位小数），与表头 (%) 一致
        # （合规审查 Agent3-S5；此前输出小数 0.39 易被误读为 0.39%）
        karst_rate_val = round(cave_thick / soluble_thick * 100, 1) if soluble_thick > 0 else 0

        if cave_rows:
            site_total_cave += cave_thick
            site_holes_with_cave += 1
            for idx, cr in enumerate(cave_rows):
                is_first = (idx == 0)
                output_rows.append({
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
                    '钻孔见洞率': 0,  # 待替换为场地级
                    '溶洞充填物特征': cr['filling'],
                    '原始描述': cr['desc'],
                })
        else:
            output_rows.append({
                '钻孔编号': zkbh,
                '里程': round(zklc, 2) if zklc else None,
                '偏移量': round(zkpil, 2) if zkpil else None,
                '地面标高': round(zkbg, 2), '孔深': round(zksd, 2),
                '基岩埋深': round(bedrock_depth, 2) if bedrock_depth is not None else None,
                '可溶岩累计厚度': round(soluble_thick, 2) if soluble_thick > 0 else None,
                '溶洞顶板深度': None, '溶洞底板深度': None,
                '溶洞顶板高程': None, '溶洞底板高程': None,
                '溶洞高度': None, '溶洞累计厚度': 0.0, '线岩溶率': 0,
                '钻孔见洞率': 0,  # 待替换为场地级
                '溶洞充填物特征': '无溶洞', '原始描述': None,
            })

    # ---- 3. 计算场地统计 ----
    # V3.0.3（S5）：场地级线岩溶率统一为百分比数值（×100，一位小数），与表头 (%)、
    # judge_development 的 % 入参同口径（此前输出小数 0.39 易被误读为 0.39%）
    site_line_rate = round(site_total_cave / site_total_soluble * 100, 1) if site_total_soluble > 0 else 0
    site_cave_hole_rate = round(site_holes_with_cave / site_holes_with_soluble * 100, 1) if site_holes_with_soluble > 0 else 0
    site_development = judge_development(site_line_rate, site_cave_hole_rate)

    # 用场地级值替换有溶洞的孔行；无溶洞孔行保持 0（避免把场地级比率误读为该孔有岩溶）
    # P2-3：替换判定用"溶洞顶板深度"（与父模块 B1 同口径）——修复前用
    # 'not 溶洞累计厚度'，cave_thick 四舍五入到 0.0 的孔被漏替换，孔已计入
    # 场地见洞率分母却显示 0，统计与显示矛盾
    for row in output_rows:
        if row.get('溶洞顶板深度') is None:
            continue
        row['线岩溶率'] = site_line_rate
        row['钻孔见洞率'] = f"{site_cave_hole_rate}%"

    # 钻孔编号自然排序且保留组内顺序
    import re as _re

    # 先给所有行填上钻孔编号（从首个非空行往下传播），用于排序
    _last_bh = None
    for row in output_rows:
        if row.get('钻孔编号'):
            _last_bh = row['钻孔编号']
        else:
            row['_sort_key'] = _last_bh  # 暂存排序用

    def _natural_key(r):
        n = r.get('钻孔编号') or r.get('_sort_key', '')
        # P1-2：修复前 int/str 混合键在同位置比较抛 TypeError（孔号数字开头
        # 与字母开头混排时整个 A 类报告崩溃）；统一 (type_tag, value) 二元组
        return [(0, int(c)) if c.isdigit() else (1, c.lower())
                for c in _re.split(r'(\d+)', str(n))]

    output_rows.sort(key=_natural_key)

    # 排序后清理：仅保留每组第一行的钻孔编号，后续行置空（用于合并）
    _prev_bh = None
    for row in output_rows:
        row.pop('_sort_key', None)
        if row.get('钻孔编号'):
            if row['钻孔编号'] == _prev_bh:
                row['钻孔编号'] = None  # 同一组的后续行，供合并用
            else:
                _prev_bh = row['钻孔编号']

    site_stats = {
        'line_rate': site_line_rate,
        'cave_hole_rate': site_cave_hole_rate,
        'development': site_development,
        'total_holes': site_holes_with_soluble,
        'cave_holes': site_holes_with_cave,
        'total_soluble': round(site_total_soluble, 2),
        'total_cave': round(site_total_cave, 2),
    }

    # ---- 4. 写附表7 ----
    table7_path = os.path.join(out_dir, '岩溶率统计表（A类）.xlsx')
    table7_path = _write_table7_a(output_rows, site_stats, table7_path)

    # ---- 5. 写附表8（复用原模块）----
    cave_stats = _build_cave_stats(da)
    table8_path = os.path.join(out_dir, '溶洞统计表（A类）.xlsx')
    table8_path = _write_table8_safe(cave_stats, table8_path)

    return table7_path, table8_path


def _write_table8_safe(stats, path):
    """包装原 _write_table8，处理文件被占用的情形"""
    import openpyxl
    # 原 _write_table8 内部直接 save 到 path，被占用时无法处理
    # 这里重写：先在临时位置生成，再用安全保存
    tmp_path = path + '.tmp'
    _write_table8(stats, tmp_path)
    try:
        os.replace(tmp_path, path)
        return path
    except PermissionError:
        base, ext = os.path.splitext(path)
        ts = datetime.datetime.now().strftime('%H%M%S')
        new_path = f"{base}_{ts}{ext}"
        os.replace(tmp_path, new_path)
        return new_path


def _write_table7_a(rows, site_stats, path):
    """写入A类附表7：无墩台列，仅填充数据不修改模板表头"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, Side, Border

    template_path = _template_path_a()
    if not os.path.exists(template_path):
        return _write_table7_fallback_a(rows, site_stats, path)

    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    data_font = Font(name='宋体', size=11)
    center = Alignment(horizontal='center', vertical='center', wrap_text=False)
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # 探测子表头行：找同时包含多个具体列名（顶板/底板/地面标高/孔深等）的行
    data_header_row = None
    for r in range(1, ws.max_row + 1):
        vals = [str(ws.cell(row=r, column=c).value or '') for c in range(1, 15)]
        score = sum(1 for kw in ['里程', '偏移量', '顶板', '底板', '地面标高', '孔深', '基岩埋深']
                    if kw in vals)
        if score >= 2:
            data_header_row = r
    if data_header_row is None:
        for r in range(1, ws.max_row + 1):
            vals = [str(ws.cell(row=r, column=c).value or '') for c in range(1, 10)]
            if '钻孔编号' in vals:
                data_header_row = r
                break
    if data_header_row is None:
        data_header_row = 3

    # 列映射：读子表头行 + 大组表头行（用于消除同名子表头歧义）
    group_row_vals = {}
    if data_header_row > 1:
        gr = data_header_row - 1
        for c in range(1, ws.max_column + 1):
            v = str(ws.cell(row=gr, column=c).value or '').strip()
            if v:
                group_row_vals[c] = v
        # V2.2.2 A1：合并单元格仅锚点有值（如 F2:G2='溶洞位置（深度）（m）'、
        # H2:I2='溶洞位置（高程）（m）'），按 merged_cells 把组名展开到组内每一列，
        # 否则第 7/9 列（底板）读不到大组表头 → "底板深度/底板高程"映射错位、底板高程永空。
        for mc in ws.merged_cells.ranges:
            if mc.min_row == gr and mc.max_row == gr:
                anchor = group_row_vals.get(mc.min_col)
                if anchor:
                    for cc in range(mc.min_col, mc.max_col + 1):
                        group_row_vals.setdefault(cc, anchor)

    col_map = {}
    for c in range(1, ws.max_column + 1):
        h = str(ws.cell(row=data_header_row, column=c).value or '').strip()
        # 子表头为空时回退到大组表头
        if not h:
            h = group_row_vals.get(c, '')
        g = group_row_vals.get(c, '')
        if h == '钻孔编号':
            col_map['钻孔编号'] = c
        elif h == '地面标高':
            col_map['地面标高'] = c
        elif h == '孔深':
            col_map['孔深'] = c
        elif h == '基岩埋深':
            col_map['基岩埋深'] = c
        elif '可溶岩' in h or '可溶岩' in g:
            col_map['可溶岩累计厚度'] = c
        elif h in ('顶板', '顶板深度'):
            col_map['溶洞顶板深度' if '高程' not in g else '溶洞顶板高程'] = c
        elif h in ('底板', '底板深度'):
            col_map['溶洞底板深度' if '高程' not in g else '溶洞底板高程'] = c
        elif h == '里程':
            col_map['里程'] = c
        elif h == '偏移量':
            col_map['偏移量'] = c
        elif '溶洞高度' in h or ('溶洞' in g and '高度' in h):
            col_map['溶洞高度'] = c
        elif '累计厚度' in h or '累计厚度' in g:
            col_map['溶洞累计厚度'] = c
        elif '线岩溶率' in h or '线岩溶率' in g:
            col_map['线岩溶率'] = c
        elif '见洞率' in h or '见洞率' in g:
            col_map['钻孔见洞率'] = c
        elif '充填物' in h or '充填物' in g:
            col_map['溶洞充填物特征'] = c
        elif '原始描述' in h or '描述' in h:
            col_map['原始描述'] = c

    # 记录模板已有合并区域
    template_merges = set(str(mc) for mc in ws.merged_cells.ranges)

    # 解除数据区域（表头下方）的模板合并，允许重新写入
    data_start = data_header_row + 1
    for mc in list(ws.merged_cells.ranges):
        if mc.min_row >= data_start:
            try: ws.unmerge_cells(str(mc))
            except Exception:
                get_logger().debug('karst_report_a._write_table7_a: 解除模板合并失败 %s', str(mc))

    # 记录模板原始行数，用于判断超出行
    template_max_row = ws.max_row

    # P2-1：填充前清空模板数据区旧值（模板自带示例/旧数据行时残留混入输出）
    # 范围收窄到本次数据行数（data_start .. data_start+len(rows)-1）——
    # 修复前清到 template_max_row 会把表尾注记/落款（如"注：本表以勘察报告
    # 为准"、审核签名行）一并清掉，T9 裁剪随之误删（T9 测试回归）；
    # 模板示例行数远超本次数据的极端场景仍残留，由 T9 尾部裁剪兜底
    _clear_end = min(template_max_row, data_start + len(rows) - 1)
    for r in range(data_start, _clear_end + 1):
        for c in range(1, ws.max_column + 1):
            ws.cell(row=r, column=c).value = None

    # 填充数据
    for ri, row_data in enumerate(rows):
        erow = data_start + ri
        for key, col in col_map.items():
            cell = ws.cell(row=erow, column=col)
            try:
                cell.value = row_data.get(key)
            except AttributeError:
                # 已合并单元格的从属单元格，跳过
                pass
            cell.font = data_font
            cell.alignment = center
            cell.border = border

    total_data_rows = data_start + len(rows) - 1

    # 合并数据行：同一钻孔的所有非洞穴独有列
    merge_cols = sorted(set(
        col_map.get(k, 0) for k in
        ['钻孔编号', '地面标高', '孔深', '基岩埋深', '可溶岩累计厚度',
         '溶洞累计厚度', '线岩溶率', '钻孔见洞率']
        if k in col_map
    ))

    def _merge_range(sr, sc, er, ec):
        try: ws.merge_cells(start_row=sr, start_column=sc, end_row=er, end_column=ec)
        except Exception:
            get_logger().debug('karst_report_a._write_table7_a: 合并失败 (%d,%d)-(%d,%d)', sr, sc, er, ec)

    def _merge_by(rows_list, key_fn, cols):
        if not cols:
            return
        prev_k, start = None, None
        erow = data_start
        for ri, rd in enumerate(rows_list):
            k = key_fn(rd)
            if k is not None and k != prev_k:
                if prev_k is not None and start is not None and erow - 1 >= start:
                    for mc in cols:
                        _merge_range(start, mc, erow - 1, mc)
                prev_k, start = k, erow
            erow += 1
        if prev_k is not None and start is not None and total_data_rows >= start:
            for mc in cols:
                _merge_range(start, mc, total_data_rows, mc)

    _merge_by(rows, lambda rd: rd.get('钻孔编号'), merge_cols)

    # V2.2.3 T8：为超出模板原始行数的数据行补上格式——
    # template_max_row 必须在填充前记录（见上方），此处不再二次赋值，
    # 否则 max_row 已被填充行为扩大，"超模板行补格式"分支永不触发（死代码）。
    for r in range(data_start, total_data_rows + 1):
        if r > template_max_row:
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(row=r, column=c)
                cell.font = data_font
                cell.alignment = center
                cell.border = border

    # V2.2.3 T9 + V2.2.4 H8：裁剪模板残留空行——只删除"最后一个有值行之后"的
    # 多余空行（自下而上找最后非空行），不依赖"数据区以下全空"的模板假设：
    # 模板中间含零星空行、尾部带附注/落款（有值）时均保留，仅清尾随空行。
    if total_data_rows >= data_start and ws.max_row > total_data_rows:
        last_nonempty = data_start - 1
        for r in range(ws.max_row, data_start - 1, -1):
            if any(ws.cell(row=r, column=c).value not in (None, '')
                   for c in range(1, ws.max_column + 1)):
                last_nonempty = r
                break
        if last_nonempty < ws.max_row:
            ws.delete_rows(last_nonempty + 1, ws.max_row - last_nonempty)

    return _safe_save_xlsx(wb, path)


def _write_table7_fallback_a(rows, site_stats, path):
    """兜底：模板缺失时直接用 openpyxl 创建"""
    import openpyxl
    from openpyxl.styles import Font, Alignment, Side, Border

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '岩溶率统计'

    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_font = Font(name='宋体', size=11, bold=True)
    data_font = Font(name='宋体', size=11)
    center = Alignment(horizontal='center', vertical='center')

    # V2.2.2 A2：兜底表补齐"钻孔见洞率"列（与主模板列结构一致，主模板含该列）
    headers = ['钻孔编号', '里程', '偏移量', '地面标高', '孔深',
               '基岩埋深', '可溶岩\n累计厚度', '溶洞\n顶板深度', '溶洞\n底板深度',
               '溶洞\n顶板高程', '溶洞\n底板高程', '溶洞高度',
               '溶洞\n累计厚度', '线岩溶率', '钻孔见洞率', '', '',
               '溶洞充填物特征', '原始描述']

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = border

    data_row = 2
    for row_data in rows:
        cols = ['钻孔编号', '里程', '偏移量', '地面标高', '孔深',
                '基岩埋深', '可溶岩累计厚度', '溶洞顶板深度', '溶洞底板深度',
                '溶洞顶板高程', '溶洞底板高程', '溶洞高度',
                '溶洞累计厚度', '线岩溶率', '钻孔见洞率']
        for ci, key in enumerate(cols, 1):
            val = row_data.get(key)
            cell = ws.cell(row=data_row, column=ci, value=val)
            cell.font = data_font
            cell.alignment = center
            cell.border = border
        # 填充物特征
        cell = ws.cell(row=data_row, column=18, value=row_data.get('溶洞充填物特征'))
        cell.font = data_font; cell.alignment = center; cell.border = border
        # 原始描述
        cell = ws.cell(row=data_row, column=19, value=row_data.get('原始描述'))
        cell.font = data_font; cell.alignment = Alignment(horizontal='left', vertical='center'); cell.border = border
        data_row += 1

    # 合并单元格：同一钻孔的钻孔级信息列
    total_data_rows = data_row - 1
    def _merge_by(rows_list, key_fn, cols):
        prev_k, start = None, None
        erow = 2
        for ri, rd in enumerate(rows_list):
            k = key_fn(rd)
            if k is not None and k != prev_k:
                if prev_k is not None and start is not None and erow - 1 >= start:
                    for mc in cols:
                        try: ws.merge_cells(start_row=start, start_column=mc, end_row=erow - 1, end_column=mc)
                        except Exception:
                            get_logger().debug('karst_report_a._write_table7_fallback_a: 合并失败 行%d-%d 列%d', start, erow - 1, mc)
                prev_k, start = k, erow
            erow += 1
        if prev_k is not None and start is not None and total_data_rows >= start:
            for mc in cols:
                try: ws.merge_cells(start_row=start, start_column=mc, end_row=total_data_rows, end_column=mc)
                except Exception:
                    get_logger().debug('karst_report_a._write_table7_fallback_a: 末组合并失败 行%d-%d 列%d', start, total_data_rows, mc)

    _merge_by(rows, lambda rd: rd.get('钻孔编号'), [1,2,3,4,5,6,7])  # 钻孔编号~可溶岩累计厚度
    _merge_by(rows, lambda rd: rd.get('钻孔编号'), [13, 14, 15])    # 溶洞累计厚度 + 线岩溶率 + 钻孔见洞率

    # 为超出模板原始行数的数据行补上格式
    for r in range(data_row, total_data_rows + 1):
        if r > ws.max_row:
            for c in range(1, 20):
                cell = ws.cell(row=r, column=c)
                cell.font = data_font
                cell.alignment = center
                cell.border = border

    return _safe_save_xlsx(wb, path)
