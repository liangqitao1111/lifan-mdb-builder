# -*- coding: utf-8 -*-
"""理反 Web · 复核与修正 API（复用桌面版规则引擎，SQLite 工作库）
================================================================
- POST /api/db/{db_id}/review         全库复核（返回按孔分组问题）
- GET  /api/db/{db_id}/review/{zkbh}  单孔复核
- POST /api/db/{db_id}/spt-scan       标贯批量修正建议扫描
- GET  /api/db/{db_id}/rules          规则清单（含说明）
================================================================
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "review"))

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/db/{db_id}", tags=["review"])

# 延迟导入（避免启动时加载过重）
_engine_cache = {}


def _get_engine(db_path, project_type, max_plasticity, use_std_stratum):
    """按参数组合缓存 RuleEngine（同参复用）"""
    key = (project_type, max_plasticity, use_std_stratum)
    if key not in _engine_cache:
        from review.rule_engine import RuleEngine
        _engine_cache[key] = RuleEngine(project_type, max_plasticity, use_std_stratum)
    return _engine_cache[key]


def _get_da(db_path):
    from review.sqlite_dao import connect_sqlite
    from review.dao import DataAccess
    return DataAccess(connect_sqlite(db_path))


def _issue_to_dict(i):
    return {"rule_id": i.rule_id, "level": i.risk_level,
            "field": getattr(i, "field", ""), "message": i.message}


def _review_all(db_path, project_type, max_plasticity, use_std_stratum):
    da = _get_da(db_path)
    engine = _get_engine(db_path, project_type, max_plasticity, use_std_stratum)
    bhs = da.get_all_boreholes()
    sa = da.get_all_strata(); sp = da.get_all_spt()
    dp = da.get_all_dpt(); tt = da.get_all_test()
    issues_by_hole = {}
    summary = {"holes": 0, "h": 0, "m": 0, "total": 0}
    for b in bhs:
        zk = str(b.get("zkbh") or "").strip()
        if not zk:
            continue
        issues = engine.review_strata(sa.get(zk, []), sp.get(zk, []),
                                      dp.get(zk, []), tt.get(zk, []))
        hs = [i for i in issues if i.risk_level == "H"]
        ms = [i for i in issues if i.risk_level == "M"]
        issues_by_hole[zk] = {"h": len(hs), "m": len(ms),
                              "issues": [_issue_to_dict(i) for i in issues]}
        summary["holes"] += 1
        summary["h"] += len(hs)
        summary["m"] += len(ms)
        summary["total"] += len(issues)
    return {"summary": summary, "by_hole": issues_by_hole}


def _resolve_db_path(db_id):
    if not db_id or ".." in db_id or not all(c.isalnum() or c in "-_" for c in db_id):
        raise HTTPException(400, "非法 db_id")
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "..", "work", "dbs", f"{db_id}.db")
    if not os.path.exists(path):
        raise HTTPException(404, f"工作库 {db_id} 不存在")
    return os.path.normpath(path)


@router.post("/review")
def review_db(db_id: str, project_type: str = "B",
              max_plasticity: str = "硬塑", use_std_stratum: bool = False):
    path = _resolve_db_path(db_id)
    try:
        return _review_all(path, project_type, max_plasticity, use_std_stratum)
    except Exception as e:
        raise HTTPException(500, f"复核失败: {e}")


@router.get("/review/{zkbh}")
def review_hole(db_id: str, zkbh: str, project_type: str = "B",
                max_plasticity: str = "硬塑", use_std_stratum: bool = False):
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    engine = _get_engine(path, project_type, max_plasticity, use_std_stratum)
    try:
        strata = da.get_strata(None, zkbh) if hasattr(da, "get_strata") else []
        spt = da.get_spt_data(zkbh) if hasattr(da, "get_spt_data") else []
        dpt = da.get_dpt_data(zkbh) if hasattr(da, "get_dpt_data") else []
        test = da.get_test_data(zkbh) if hasattr(da, "get_test_data") else []
        if not strata and not spt and not dpt:
            raise HTTPException(404, f"钻孔 {zkbh} 无数据")
        issues = engine.review_strata(strata, spt, dpt, test)
        return {"zkbh": zkbh, "issues": [_issue_to_dict(i) for i in issues]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"单孔复核失败: {e}")


@router.post("/spt-scan")
def spt_scan(db_id: str, project_type: str = "B",
             max_plasticity: str = "硬塑", use_std_stratum: bool = False):
    """标贯批量修正建议扫描（只读，不改库）"""
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    engine = _get_engine(path, project_type, max_plasticity, use_std_stratum)
    try:
        from review.spt_corrector import SptCorrector
        corr = SptCorrector(da, engine)
        suggestions = corr.scan_all()
        return {"total": len(suggestions),
                "suggestions": [s.__dict__ if hasattr(s, "__dict__") else str(s)
                                for s in suggestions]}
    except Exception as e:
        raise HTTPException(500, f"标贯扫描失败: {e}")


@router.get("/rules")
def list_rules(db_id: str):
    path = _resolve_db_path(db_id)
    engine = _get_engine(path, "B", "硬塑", False)
    try:
        rules = engine.get_rules()
        return {"rules": rules}
    except Exception as e:
        raise HTTPException(500, f"规则清单失败: {e}")


# =====================================================================
# 统计成果 API（复用桌面版统计模块，输出 xlsx 到 work/reports/{db_id}/）
# =====================================================================
def _reports_dir(db_id):
    base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "..", "work", "reports", db_id)
    os.makedirs(d, exist_ok=True)
    return d


@router.post("/stats/karst")
def stats_karst(db_id: str, project_type: str = "B"):
    """岩溶统计报告（附表7/附表8），返回生成的文件名列表"""
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    out_dir = _reports_dir(db_id)
    try:
        if project_type.upper() == "A":
            from review.karst_report_a import generate_karst_report_a
            paths = generate_karst_report_a(da, out_dir)
        else:
            from review.karst_report import generate_karst_report
            paths = generate_karst_report(da, out_dir)
        if isinstance(paths, (tuple, list)):
            names = [os.path.basename(p) for p in paths if p]
        else:
            names = [os.path.basename(paths)]
        return {"ok": True, "project_type": project_type, "files": names,
                "download": f"/api/db/{db_id}/stats/download?file=" + names[0] if names else None}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"岩溶统计失败: {e}")


@router.post("/stats/soil")
def stats_soil(db_id: str, project_type: str = "A", filter_plasticity: bool = True,
               filter_cv: bool = True):
    """土工试验统计（按地层分组），生成 xlsx"""
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    out_dir = _reports_dir(db_id)
    try:
        from review.soil_stats import collect_stratum_statistics, write_statistics_excel
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template = os.path.join(root, "参数", "数据表头.xlsx")
        if not os.path.exists(template):
            template = None
        stats_list = collect_stratum_statistics(da, filter_plasticity, filter_cv, project_type)
        output = os.path.join(out_dir, f"土工统计_{project_type}.xlsx")
        write_statistics_excel(stats_list, template, output)
        return {"ok": True, "count": len(stats_list), "file": os.path.basename(output),
                "download": f"/api/db/{db_id}/stats/download?file=" + os.path.basename(output)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"土工统计失败: {e}")


@router.get("/stats/download")
def stats_download(db_id: str, file: str):
    """下载统计产物（防路径穿越）"""
    if not file or ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "..", "work", "reports", db_id, file)
    if not os.path.exists(path):
        raise HTTPException(404, f"产物不存在: {file}")
    return FileResponse(path, filename=file)
