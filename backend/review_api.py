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
            "field": getattr(i, "field", ""), "message": i.message,
            # v18：透传行索引/参考值，供前端「问题卡片 → 定位高亮表格行」
            "layer_index": getattr(i, "layer_index", -1),
            "ref_value": getattr(i, "ref_value", 0.0)}


def _review_all(db_path, project_type, max_plasticity, use_std_stratum, include_test=True):
    da = _get_da(db_path)
    engine = _get_engine(db_path, project_type, max_plasticity, use_std_stratum)
    bhs = da.get_all_boreholes()
    sa = da.get_all_strata(); sp = da.get_all_spt()
    dp = da.get_all_dpt(); tt = da.get_all_test() if include_test else {}
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
              max_plasticity: str = "硬塑", use_std_stratum: bool = False,
              include_test: bool = True):
    path = _resolve_db_path(db_id)
    try:
        return _review_all(path, project_type, max_plasticity, use_std_stratum, include_test)
    except Exception as e:
        raise HTTPException(500, f"复核失败: {e}")


@router.get("/review/{zkbh}")
def review_hole(db_id: str, zkbh: str, project_type: str = "B",
                max_plasticity: str = "硬塑", use_std_stratum: bool = False,
                include_test: bool = True):
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    engine = _get_engine(path, project_type, max_plasticity, use_std_stratum)
    try:
        strata = da.get_strata(None, zkbh) if hasattr(da, "get_strata") else []
        spt = da.get_spt_data(zkbh) if hasattr(da, "get_spt_data") else []
        dpt = da.get_dpt_data(zkbh) if hasattr(da, "get_dpt_data") else []
        test = da.get_test_data(zkbh) if hasattr(da, "get_test_data") and include_test else []
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
        import dataclasses
        from review.spt_corrector import SptCorrector
        corr = SptCorrector(da, engine)
        suggestions = corr.scan_all()

        def _ser(x):
            if dataclasses.is_dataclass(x) and not isinstance(x, type):
                return {k: _ser(v) for k, v in dataclasses.asdict(x).items()}
            if isinstance(x, dict):
                return {k: _ser(v) for k, v in x.items()}
            if isinstance(x, (list, tuple)):
                return [_ser(v) for v in x]
            return x

        return {"total": len(suggestions), "suggestions": [_ser(s) for s in suggestions]}
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


# =====================================================================
# DXF 导出 API（复用桌面版 profile_dxf / column_dxf，ezdxf 纯 Python）
# =====================================================================
def _dxf_dir(db_id):
    base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "..", "work", "dxf", db_id)
    os.makedirs(d, exist_ok=True)
    return d


@router.post("/dxf/profile")
def dxf_profile(db_id: str, holes: str = "", h_scale: int = 500, v_scale: int = 500,
                group_interval: int = 3, draw_cave: bool = True,
                draw_label: bool = True, draw_label_text: bool = True):
    """纵断面 DXF：holes 为逗号分隔钻孔编号（≥2），按里程排序"""
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    zkbh_list = [z.strip() for z in (holes or "").split(",") if z.strip()]
    if len(zkbh_list) < 2:
        raise HTTPException(400, "至少输入 2 个钻孔编号（逗号分隔）")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    block_tpl = os.path.join(root, "地层标注", "块.dxf")
    out = os.path.join(_dxf_dir(db_id), f"profile_{db_id}.dxf")
    try:
        from review.profile_dxf import generate_profile
        out_path, hole_count, placed, skipped = generate_profile(
            da, zkbh_list, out, group_interval, block_tpl,
            draw_cave, draw_label, draw_label_text, h_scale, v_scale)
        return {"ok": True, "file": os.path.basename(out_path), "holes": hole_count,
                "placed": placed, "skipped": skipped,
                "download": f"/api/db/{db_id}/dxf/download?file=" + os.path.basename(out_path)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"纵断面生成失败: {e}")


@router.post("/dxf/columns")
def dxf_columns(db_id: str, interval: int = None, col_h: int = None, col_w: int = None):
    """综合小柱状图 DXF（全部钻孔）；未传参数时用 TOML「DXF_小柱状图」默认值"""
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    out = os.path.join(_dxf_dir(db_id), f"columns_{db_id}.dxf")
    try:
        from review.column_dxf import generate_columns
        placed, msg = generate_columns(da, out, interval, col_h, col_w)  # None → 函数内读 TOML
        return {"ok": True, "file": os.path.basename(out), "placed": placed, "msg": msg,
                "download": f"/api/db/{db_id}/dxf/download?file=" + os.path.basename(out)}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"柱状图生成失败: {e}")


@router.get("/dxf/download")
def dxf_download(db_id: str, file: str):
    if not file or ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "..", "work", "dxf", db_id, file)
    if not os.path.exists(path):
        raise HTTPException(404, f"产物不存在: {file}")
    return FileResponse(path, filename=file)


# =====================================================================
# 参数中心 API（工程配置.toml 查看/编辑/保存）
# =====================================================================
def _config_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "参数", "工程配置.toml")


# 参数中心中「代码未消费」的参数（Web 端无引用；保留数据但标注，避免用户误改）
_DEAD_CONFIG_KEYS = [
    "CAD复核",               # Web 未实现 CAD 复核（桌面版 dxf_depth_check 使用）
    "岩溶统计.洞高标签",      # 标签由「洞高阈值」动态生成，配置数组未消费
    "岩溶统计.埋深标签",      # 同上
    "岩溶_关键词.有效填充物",  # 填充物判定仅消费 无效填充物/跳过词/跳过前缀
    # 手动修正范围（桌面版「手动修正」功能参数；Web 无该功能，判定表直接驱动规则）
    "公用.手动修正_密实度N值范围",
    "A类.手动修正_标贯N值范围",
    "A类.手动修正_可塑性N值范围",
    "B类.手动修正_标贯N值范围",
    "B类.手动修正_可塑性N值范围",
]


@router.get("/config")
def get_config(db_id: str, include_dxf: bool = False):
    """读取工程配置.toml：返回原始文本 + 结构概览（段落/键数）。
    include_dxf=True 时保留 DXF 图形参数段（工具页「修正与成果」内嵌表单读取）。"""
    p = _config_path()
    if not os.path.exists(p):
        raise HTTPException(404, f"配置文件不存在: {p}")
    raw = io_open(p, "utf-8").read()
    structured = None
    overview = {}
    try:
        import tomllib
        data = tomllib.loads(raw)
        # DXF 图形参数已移入「修正与成果」页对应卡片（纵断面/小柱状图），参数中心不再展示
        if not include_dxf:
            for _k in ('DXF_小柱状图', 'DXF_纵断面'):
                data.pop(_k, None)
        structured = data
        overview = {k: (len(v) if isinstance(v, dict) else type(v).__name__) for k, v in data.items()}
    except Exception as e:
        overview = {"解析失败": str(e)}
    return {"path": p, "size": len(raw), "raw_text": raw,
            "structured": structured, "overview": overview,
            "dead_keys": _DEAD_CONFIG_KEYS}


@router.put("/config")
def put_config(db_id: str, payload: dict):
    """保存工程配置.toml：优先 updates 补丁（TomlFile 行级替换保留注释），
    兼容旧 raw_text 整写（tomllib 校验）"""
    p = _config_path()
    if not os.path.exists(p):
        raise HTTPException(404, f"配置文件不存在: {p}")
    updates = (payload or {}).get("updates")
    deletes = (payload or {}).get("deletes") or []
    if isinstance(updates, dict) and updates or deletes:
        try:
            from review.toml_preserve import TomlFile
            tf = TomlFile(p)
            ok, missing = 0, []
            for path, val in updates.items():
                if tf.set(path, val):
                    ok += 1
                elif tf.add(path, val):
                    ok += 1
                else:
                    missing.append(path)
            for d in deletes:
                if tf.delete(d):
                    ok += 1
                elif d not in missing:
                    missing.append(d)
            if not tf.is_dirty():
                return {"ok": True, "applied": 0, "unchanged": True}
            # 备份后保存（保留注释的行级写入）
            import shutil
            shutil.copy2(p, p + ".bak")
            tf.save(p)
            # 清 config 缓存
            try:
                import review.config as rcfg
                rcfg._PROJECT_CONFIG = None
            except Exception:
                pass
            return {"ok": True, "applied": ok,
                    "missing": missing[:20] if missing else None,
                    "backup": os.path.basename(p) + ".bak"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"补丁保存失败: {e}")
    raw = (payload or {}).get("raw_text")
    if not raw or not isinstance(raw, str):
        raise HTTPException(400, "缺少 updates 或 raw_text")
    try:
        import tomllib
        new_cfg = tomllib.loads(raw)
    except Exception as e:
        raise HTTPException(422, f"TOML 语法错误，拒绝保存: {e}")
    # 完整性防护：核心段缺失视为损坏，拒绝覆盖（防丢键写坏配置）
    if os.path.exists(_config_path()):
        try:
            with open(_config_path(), "r", encoding="utf-8") as f:
                old_cfg = tomllib.loads(f.read())
            core = [k for k in ("公用", "A类", "B类") if k in old_cfg]
            missing = [k for k in core if k not in new_cfg]
            if missing:
                raise HTTPException(422, f"配置缺少核心段 {missing}，拒绝保存（配置可能损坏）")
        except HTTPException:
            raise
        except Exception:
            pass
    p = _config_path()
    # 备份后写入（保留旧版可回滚）
    try:
        if os.path.exists(p):
            bak = p + ".bak"
            with open(p, "r", encoding="utf-8") as f, open(bak, "w", encoding="utf-8") as g:
                g.write(f.read())
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw)
        # 清空 config 缓存，使下次复核使用新参数
        try:
            import review.config as rcfg
            rcfg._PROJECT_CONFIG = None
        except Exception:
            pass
        return {"ok": True, "size": len(raw), "backup": os.path.basename(p) + ".bak"}
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")


def io_open(path, enc):
    return open(path, "r", encoding=enc)
