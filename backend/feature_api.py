# -*- coding: utf-8 -*-
"""backend/feature_api.py：P1 功能——标贯一键应用/动探修正/问题导出/表头映射"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "review"))

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/db/{db_id}", tags=["feature"])

COLUMN_CN = {
    "ZKBH": "钻孔编号", "GCSY": "工程代码", "ID": "ID",
    "TCXH": "层号", "TCZCBH": "主层", "TCYCBH": "亚层", "TCDZSD": "地质时代", "TCDZCY": "地质成因",
    "TCCDSD": "层底深度(m)", "TCHD": "层厚(m)", "TCYMC": "岩土名称", "TCMC": "岩土名称", "TCYS": "颜色",
    "TCMSD": "密实度", "TCSID": "湿度", "TCKSX": "可塑性", "TCFHCD": "风化程度", "TCMS": "描述",
    "BGDSD": "深度(m)", "BGGC": "杆长(m)", "BGJS": "实测N", "BGXZJS": "修正N",
    "DTDSD": "深度(m)", "DTLX": "类型", "DTJS": "N63.5", "DTXZJS": "修正",
    "SWCH": "序号", "SWSD": "地下水位深度(m)", "SWLX": "水位类型", "SWXZ": "地下水类型",
    "CY": "是否参与", "SWCSRQ": "测水时间",
    "QYBH": "取样编号", "QYSD": "深度(m)", "QYHSL": "含水率%", "QYYX": "液限%",
    "QYSY": "塑限%", "QYDC": "综合定名",
    "QYIL": "IL", "QYIP": "IP", "QYE0": "e₀",
    "QYSD2": "20~2mm", "QYSD05": "2~0.5mm", "QYSD025": "0.5~0.25mm",
    "QYSD0075": "0.25~0.075mm", "QYSD0": "<0.075mm",
    "DTLX_B": "类型", "BGDSD_B": "深度",
}


def _db_path(db_id):
    if not db_id or ".." in db_id or not all(c.isalnum() or c in "-_" for c in db_id):
        raise HTTPException(400, "非法 db_id")
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(os.path.join(base, "..", "work", "dbs", f"{db_id}.db"))
    if not os.path.exists(path):
        raise HTTPException(404, f"工作库 {db_id} 不存在")
    return path


def _review_all(db_id, project_type, max_plasticity, use_std_stratum, include_test):
    from review.sqlite_dao import connect_sqlite
    from review.dao import DataAccess
    from review.rule_engine import RuleEngine
    da = DataAccess(connect_sqlite(_db_path(db_id)))
    engine = RuleEngine(project_type, max_plasticity, use_std_stratum)
    bhs = da.get_all_boreholes()
    sa = da.get_all_strata(); sp = da.get_all_spt()
    # P1-⑩：显式传 project_type（模块级 set_project_type 全局态会串口径）
    dp = da.get_all_dpt(project_type)
    tt = da.get_all_test() if include_test else {}
    out = {}
    for b in bhs:
        zk = str(b.get("zkbh") or "").strip()
        if not zk:
            continue
        out[zk] = engine.review_strata(sa.get(zk, []), sp.get(zk, []),
                                       dp.get(zk, []), tt.get(zk, []))
    return out


# ---------- 1) 标贯一键应用 ----------
class SptApplyItem(BaseModel):
    zkbh: str = ""
    bgdsd: float = 0
    new_n: float = 0
    issue_type: str = ""        # density / plasticity / weathering（v33：可塑性走状态写回）
    expected_state: str = ""    # plasticity 条目的目标状态（写回地层 TCKSX）


@router.post("/spt-apply")
def spt_apply(db_id: str, payload: dict):
    """将标贯修正建议写回工作库。
    density/weathering → BGJS 更新；plasticity（R-DEN-007）→ 该标贯深度所在层 TCKSX 状态写回。
    """
    items = [SptApplyItem(**x) for x in (payload or {}).get("items", []) if isinstance(x, dict)]
    if not items:
        raise HTTPException(400, "缺少 items")
    import sqlite3
    path = _db_path(db_id)
    conn = sqlite3.connect(path)
    updated = 0
    n_updated = 0
    s_updated = 0
    try:
        for it in items:
            if it.issue_type == "plasticity" and it.expected_state:
                # P1-⑥ 可塑性状态修正：地层定位必须与规则引擎层位判定口径一致——
                # 引擎按 (层顶, 层底] 归属（prev_depth < bgdsd <= depth），此前 SQL
                # 只取 MIN(TCCDSD >= bgdsd)，深度恰等于层界时归错层（引擎归下层、
                # SQL 归上层）。现按层顶/层底区间逐层匹配，找不到（跨层空隙）则跳过。
                rows = conn.execute(
                    "SELECT id, TCCDSD FROM [z_g_TuCeng] WHERE ZKBH = ? ORDER BY TCCDSD",
                    (it.zkbh,)).fetchall()
                target_id = None
                prev_depth = 0.0
                d = float(it.bgdsd)
                for rid, depth in rows:
                    depth = float(depth or 0)
                    if prev_depth < d <= depth:
                        target_id = rid
                        break
                    prev_depth = depth
                if target_id is not None:
                    cur = conn.execute(
                        "UPDATE [z_g_TuCeng] SET TCKSX = ? WHERE id = ?",
                        (it.expected_state, target_id))
                    s_updated += cur.rowcount
                else:
                    # 深度不在任何层区间（跨层/超深）：跳过，不静默写错层
                    continue
            else:
                # P2-5 浮点容差：BGDSD 为 REAL 存储，前端传 3 位小数，
                # 精确等值可能 miss（如 5.1000000000000005 vs 5.1）
                cur = conn.execute(
                    "UPDATE [z_y_BiaoGuan] SET BGJS = ? WHERE ZKBH = ? AND ABS(BGDSD - ?) < 0.005",
                    (float(it.new_n), it.zkbh, float(it.bgdsd)))
                n_updated += cur.rowcount
            updated += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "updated": updated, "n_updated": n_updated, "s_updated": s_updated, "total": len(items)}


# ---------- 2) 动探杆长修正 ----------
class DptCorrectPayload(BaseModel):
    zkbh: str = ""
    project_type: str = "A"


@router.post("/dpt-correct")
def dpt_correct(db_id: str, payload: DptCorrectPayload):
    zkbh = payload.zkbh
    project_type = payload.project_type
    """按 GB50021 杆长修正公式，对钻孔重型动探计算并写回修正值 DTXZJS"""
    from review.config import dpt_rod_correction_a, dpt_rod_length_offset
    from review.dao import _is_heavy_dpt  # P2-5：与规则引擎 R-DEN-002 共用同一重型判定
    import sqlite3
    path = _db_path(db_id)
    conn = sqlite3.connect(path)
    updated = 0
    try:
        rows = conn.execute(
            "SELECT ZKBH, DTDSD, DTLX, DTJS, DTXZJS FROM [z_y_DongTan] WHERE ZKBH = ? ORDER BY DTDSD",
            (zkbh,)).fetchall()
        for r in rows:
            dtlx_raw = str(r[2]).strip() if r[2] else ''
            if not _is_heavy_dpt(dtlx_raw):
                continue  # 仅重型（GB50021 表B.0.1；轻型/超重型跳过）
            try:
                dtjs = float(r[3] or 0)
                dtdsd = float(r[1] or 0)
            except (TypeError, ValueError):
                continue
            if dtjs <= 0:
                continue
            alpha = dpt_rod_correction_a(dtdsd + dpt_rod_length_offset(), dtjs)
            new_val = round(dtjs * alpha, 2)
            old_val = r[4]
            # P2-5：数值容差比较（修复前 str 比较对 0.0/0 等表示差异会重复写库）
            try:
                old_f = float(old_val) if old_val is not None else None
            except (TypeError, ValueError):
                old_f = None
            if old_f is None or abs(old_f - new_val) > 0.005:
                conn.execute("UPDATE [z_y_DongTan] SET DTXZJS = ? WHERE ZKBH = ? AND DTDSD = ?",
                             (new_val, zkbh, dtdsd))
                updated += 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "zkbh": zkbh, "updated": updated}


# ---------- 3) 复核问题导出 ----------
@router.post("/issues-export")
def issues_export(db_id: str, project_type: str = "B",
                  max_plasticity: str = "硬塑", use_std_stratum: bool = False,
                  include_test: bool = True, level_filter: str = "all"):
    """全库复核 → 导出问题清单 xlsx"""
    from review.issue_exporter import export_issues_to_xlsx
    issues = _review_all(db_id, project_type, max_plasticity, use_std_stratum, include_test)
    # rule_desc_map：从规则引擎取
    from review.sqlite_dao import connect_sqlite
    from review.dao import DataAccess
    from review.rule_engine import RuleEngine
    da = DataAccess(connect_sqlite(_db_path(db_id)))
    engine = RuleEngine(project_type, max_plasticity, use_std_stratum)
    rules = engine.get_rules() or []
    desc = {}
    for r in rules:
        if isinstance(r, dict):
            rid = r.get("id") or r.get("rule_id")
            d = r.get("desc") or r.get("description") or ""
        else:
            rid = getattr(r, "rule_id", None)
            d = getattr(r, "description", "") or ""
        if rid:
            desc[rid] = d
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "reports", db_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "复核问题清单.xlsx")
    lf = None if str(level_filter).lower() in ("all", "") else str(level_filter).upper()
    export_issues_to_xlsx(issues, out, lf, desc)
    return {"ok": True, "file": os.path.basename(out),
            "download": f"/api/db/{db_id}/stats/download?file={os.path.basename(out)}"}


# ---------- 4) 列名中文映射 ----------
@router.get("/column-map")
def column_map(db_id: str):
    return {"columns": COLUMN_CN}
