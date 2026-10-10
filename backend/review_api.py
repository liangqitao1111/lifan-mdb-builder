# -*- coding: utf-8 -*-
"""理反 Web · 复核与修正 API（复用桌面版规则引擎，SQLite 工作库）
================================================================
- POST /api/db/{db_id}/review         全库复核（返回按孔分组问题）
- GET  /api/db/{db_id}/review/{zkbh}  单孔复核
- POST /api/db/{db_id}/spt-scan       标贯批量修正建议扫描
- GET  /api/db/{db_id}/rules          规则清单（含说明）
- POST /api/db/{db_id}/cad-check      CAD 深度复核（A1，上传纵断面 DXF 比对）
================================================================
"""
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "review"))

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/db/{db_id}", tags=["review"])

# 上传和 DXF 生成均使用有限的输入边界，避免异常请求耗尽磁盘、线程或
# 生成器中的 range()/除法操作。可按部署环境通过环境变量收紧 CAD 上限。
MAX_CAD_BYTES = int(os.environ.get("MAX_CAD_MB", "512")) * 1024 * 1024


def _validate_optional_int(name, value, minimum, maximum):
    """校验可选查询参数；None 表示回退工程配置。"""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(422, f"{name} 必须是整数")
    if value < minimum or value > maximum:
        raise HTTPException(422, f"{name} 必须在 {minimum} 到 {maximum} 之间")

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
    # P1-⑩：显式传 project_type（模块级 dao.set_project_type 是全局态，
    # 多参数/并发请求会串 A/B 类动探杆长修正口径）
    dp = da.get_all_dpt(project_type)
    tt = da.get_all_test() if include_test else {}
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
        # P1-⑩：显式传 project_type，避免模块级全局串口径
        dpt = da.get_dpt_data(zkbh, project_type) if hasattr(da, "get_dpt_data") else []
        test = da.get_test_data(zkbh) if hasattr(da, "get_test_data") and include_test else []
        # 复查发现：孔只有土工试验数据（无地层/标贯/动探）时此前 404"无数据"，
        # 但全库复核该孔正常产出 R-PLS/R-GRS 问题——两入口不一致；test 须参与判断
        if not strata and not spt and not dpt and not test:
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


# =====================================================================
# CAD 深度复核（一致性清单 A1：桌面 dxf_depth_check.py 的 Web 移植）
# 上传纵断面 DXF → 纯文本解析层底深度/标贯 → 与 SQLite 工作库比对 → 文本报告
# =====================================================================
def _cad_check_dir(db_id):
    base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "..", "work", "cadcheck", db_id)
    os.makedirs(d, exist_ok=True)
    return d


@router.post("/cad-check")
async def cad_check(db_id: str, file: UploadFile = File(...)):
    """CAD 深度复核：multipart 上传 .dxf，返回 JSON 结果 + 文本报告文件名"""
    path = _resolve_db_path(db_id)
    fname = os.path.basename(file.filename or "")
    if not fname.lower().endswith(".dxf"):
        raise HTTPException(400, "仅支持 .dxf（请在 CAD 中执行 DXFOUT 导出）")
    if ".." in fname or "/" in fname or "\\" in fname:
        raise HTTPException(400, "非法文件名")
    dxf_dir = _cad_check_dir(db_id)
    dxf_path = os.path.join(dxf_dir, fname)
    # 不信任 Content-Length；代理可能不转发或客户端可以伪造，按流实际计数。
    request_size = None
    try:
        file_headers = getattr(file, "headers", None)
        request_size = int(file_headers.get("content-length")) if file_headers else None
    except (TypeError, ValueError):
        request_size = None
    if request_size is not None and request_size > MAX_CAD_BYTES:
        raise HTTPException(413, f"DXF 文件超过大小上限 {MAX_CAD_BYTES // (1024 * 1024)}MB")
    size = 0
    try:
        with open(dxf_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_CAD_BYTES:
                    raise HTTPException(413, f"DXF 文件超过大小上限 {MAX_CAD_BYTES // (1024 * 1024)}MB")
                f.write(chunk)
    except HTTPException:
        try:
            os.remove(dxf_path)
        except OSError:
            pass
        raise
    except Exception as e:
        try:
            os.remove(dxf_path)
        except OSError:
            pass
        raise HTTPException(500, f"保存上传文件失败: {e}")
    try:
        from review.dxf_depth_check import check_depth_da
        da = _get_da(path)
        (results, dxf_depths, all_bh, no_depth, report, spt_cad,
         spt_mismatch, v_scale_used, spt_warnings, spt_supplemented) = check_depth_da(dxf_path, da)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"CAD 复核失败: {e}")
    # 报告落盘（供下载留档）
    report_name = f"深度复核报告_{os.path.splitext(fname)[0]}.txt"
    try:
        with open(os.path.join(dxf_dir, report_name), "w", encoding="utf-8") as f:
            f.write(report)
    except Exception:
        report_name = None
    summary = {
        "dxf_boreholes": len(all_bh),
        "extracted": len(dxf_depths),
        "no_depth": len(no_depth),
        "mismatch": sum(1 for r in results.values() if r.get("status") == "mismatch"),
        "db_not_found": sum(1 for r in results.values() if r.get("status") == "db_not_found"),
        "spt_warning_holes": len(spt_warnings or {}),
        "spt_warning_count": sum(len(v) for v in (spt_warnings or {}).values()),
        "spt_supplemented_holes": len(spt_supplemented or {}),
        "spt_mismatch_holes": len(spt_mismatch or {}),
        "v_scale": v_scale_used,
    }
    return {"ok": True, "summary": summary,
            "results": {k: v for k, v in results.items()},
            "spt_warnings": spt_warnings or {},
            "spt_mismatch": spt_mismatch or {},
            "no_depth": no_depth,
            "report": report,
            "report_file": report_name}


@router.get("/cad-check/download")
def cad_check_download(db_id: str, file: str):
    if not file or ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")
    path = os.path.join(_cad_check_dir(db_id), file)
    if not os.path.exists(path):
        raise HTTPException(404, f"文件不存在: {file}")
    # 旧实现返回服务器路径字符串；调用方需要实际文件内容。
    return FileResponse(path, filename=file, media_type="application/octet-stream")


@router.get("/cad-check/report")
def cad_check_report(db_id: str, file: str):
    """下载复核报告文本"""
    if not file or ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")
    path = os.path.join(_cad_check_dir(db_id), file)
    if not os.path.exists(path):
        raise HTTPException(404, f"文件不存在: {file}")
    return FileResponse(path, filename=file, media_type="text/plain")


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
def stats_karst(db_id: str, project_type: str = "B", header_version: str = "new"):
    """岩溶统计报告（附表7/附表8），返回生成的文件名列表

    A2：header_version='new' → 新表头 岩溶发育统计表（15列，2026.09 版，桌面
    _ask_karst_header_version 三选一弹窗的 Web 实现）；'old' → 旧表头 附表7。
    仅 B 类支持新旧表头（A 类报告无此区分）。
    """
    path = _resolve_db_path(db_id)
    da = _get_da(path)
    out_dir = _reports_dir(db_id)
    try:
        if project_type.upper() == "A":
            from review.karst_report_a import generate_karst_report_a
            paths = generate_karst_report_a(da, out_dir)
        else:
            from review.karst_report import generate_karst_report
            hv = 'old' if str(header_version).lower() == 'old' else 'new'
            paths = generate_karst_report(da, out_dir, header_version=hv)
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
    """下载统计产物（防路径穿越）

    P0-①：db_id 必须经 _resolve_db_path 校验（此前未校验，URL 编码的
    %2E%2E%5C%2E%2E 可把 os.path.join 引到项目目录之外，任意文件读取）。
    """
    if not file or ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")
    _resolve_db_path(db_id)  # 校验 db_id 合法且工作库存在
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
def dxf_profile(db_id: str, holes: str = "", h_scale: int = None, v_scale: int = None,
                group_interval: int = None, draw_cave: bool = None,
                draw_label: bool = None, draw_label_text: bool = None):
    """纵断面 DXF：holes 为逗号分隔钻孔编号（≥2），按里程排序

    P2-7（C1/C2/C3）：比例/间隔/勾选未传（None）时回退 TOML「DXF_纵断面」，
    不再写死默认覆盖一切；前端 UI 后续补参数控件即可直接传值。
    """
    path = _resolve_db_path(db_id)
    _validate_optional_int("h_scale", h_scale, 100, 10000)
    _validate_optional_int("v_scale", v_scale, 100, 10000)
    _validate_optional_int("group_interval", group_interval, 1, 20)
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
    except ValueError as e:
        if "至少需要2个有效钻孔" in str(e):
            raise HTTPException(400, str(e).strip())
        raise HTTPException(422, f"纵断面参数错误: {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"纵断面生成失败: {e}")


@router.post("/dxf/columns")
def dxf_columns(db_id: str, interval: int = None, col_h: int = None, col_w: int = None):
    """综合小柱状图 DXF（全部钻孔）；未传参数时用 TOML「DXF_小柱状图」默认值"""
    path = _resolve_db_path(db_id)
    _validate_optional_int("interval", interval, 100, 2000)
    _validate_optional_int("col_h", col_h, 5, 50)
    _validate_optional_int("col_w", col_w, 3, 30)
    da = _get_da(path)
    out = os.path.join(_dxf_dir(db_id), f"columns_{db_id}.dxf")
    try:
        from review.column_dxf import generate_columns
        placed, msg = generate_columns(da, out, interval, col_h, col_w)  # None → 函数内读 TOML
        return {"ok": True, "file": os.path.basename(out), "placed": placed, "msg": msg,
                "download": f"/api/db/{db_id}/dxf/download?file=" + os.path.basename(out)}
    except ValueError as e:
        raise HTTPException(422, f"柱状图参数错误: {e}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"柱状图生成失败: {e}")


@router.get("/dxf/download")
def dxf_download(db_id: str, file: str):
    if not file or ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")
    _resolve_db_path(db_id)  # P0-①：同 stats/download，校验 db_id 防路径穿越
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
# D6（一致性清单）：手动修正_可塑性N值范围 已被 spt_corrector._load_plasticity_ranges
# 消费（TOML 优先于内置表，A/B 类分工程），从死键清单移除；
# 标贯N值范围（手动修正）仍无消费方（D6 手动改 N 的对话框 Web 未实现），保留标注。
_DEAD_CONFIG_KEYS = [
    "CAD复核",               # 一致性清单 A1 落地后已由 review.dxf_depth_check 消费（参数中心/工具页暂未暴露编辑），保留标注防误改
    "岩溶统计.洞高标签",      # 标签由「洞高阈值」动态生成，配置数组未消费
    "岩溶统计.埋深标签",      # 同上
    "岩溶_关键词.有效填充物",  # 填充物判定仅消费 无效填充物/跳过词/跳过前缀
    # 手动修正范围（桌面版「手动修正」功能参数；Web 端 D6 仅实现可塑性范围消费）
    "公用.手动修正_密实度N值范围",
    "A类.手动修正_标贯N值范围",
    "A类.手动修正_可塑性N值范围",
    "B类.手动修正_标贯N值范围",
    "B类.手动修正_可塑性N值范围",
]


def _reload_config_modules():
    """配置保存后重建全部派生常量（P0-③/P1-⑤）

    此前 v42 只清 config._PROJECT_CONFIG 模块缓存：
      a) review_api._engine_cache 里已构造的 RuleEngine 快照旧规则注册表/阈值，
         同参数复核永远用旧值（实测：关闭 R-PLS-002 后仍报 97 条）；
      b) config.py 及各消费模块的派生常量（区间表/关键词/杆长系数）在 import 期
         固化，清缓存也不重建。
    现统一：清引擎缓存 + 重载 config（注意顶层 config 与 review.config 可能是
    **两个独立模块对象**——sys.path 同时含 backend 与 backend/review 时同一文件
    被加载两次，各自持有独立 _PROJECT_CONFIG，必须双清双载）+ 各模块
    reload_from_config()（模块未提供该钩子时跳过，保证向后兼容）。
    """
    global _engine_cache
    _engine_cache.clear()
    import sys as _sys
    for _mod_name in ('config', 'review.config'):
        _mod = _sys.modules.get(_mod_name)
        if _mod is None:
            continue
        try:
            _mod._PROJECT_CONFIG = None
            _reload = getattr(_mod, 'reload_config', None)
            if callable(_reload):
                _reload()
        except Exception:
            pass
    for _mod_name in ('review.rule_engine', 'review.dao', 'review.karst_report',
                      'review.karst_report_a', 'review.soil_stats',
                      'review.soil_stats_v2', 'review.bearing_capacity',
                       'review.column_dxf', 'review.profile_dxf',
                       'review.dxf_depth_check',
                      'review.profile_strip'):
        try:
            _mod = __import__(_mod_name, fromlist=['x'])
            _fn = getattr(_mod, 'reload_from_config', None)
            if callable(_fn):
                _fn()
        except Exception:
            pass


@router.get("/config")
def get_config(db_id: str, include_dxf: bool = False):
    """读取工程配置.toml：返回原始文本 + 结构概览（段落/键数）。
    include_dxf=True 时保留 DXF 图形参数段（工具页「修正与成果」内嵌表单读取）。"""
    _resolve_db_path(db_id)
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
    _resolve_db_path(db_id)
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
            # 清 config 缓存 + 引擎缓存 + 重建派生常量（重要：config 与 review.config
            # 可能是两个模块对象，规则引擎等用顶层 config；v42 只双清模块缓存，
            # 已构造引擎与 import 期常量仍陈旧——P0-③/P1-⑤ 现统一重载）
            _reload_config_modules()
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
        # 清空 config 缓存 + 引擎缓存 + 重建派生常量（v42 只清模块缓存，
        # 已构造引擎与 import 期常量仍陈旧——P0-③/P1-⑤ 现统一重载）
        _reload_config_modules()
        return {"ok": True, "size": len(raw), "backup": os.path.basename(p) + ".bak"}
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")


def io_open(path, enc):
    return open(path, "r", encoding=enc)


# =====================================================================
# 参数中心增强（一致性清单 D5：差异导出 / 区间连续性校验 / 单项还原）
# =====================================================================
def _flatten_cfg(d, prefix=""):
    """把嵌套 dict 展平为 {'段.子段.键': 值}（段数组保留 [i] 下标）"""
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            path = f'{prefix}.{k}' if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flatten_cfg(v, path))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, dict):
                        out.update(_flatten_cfg(item, f'{path}[{i}]'))
                    else:
                        out[f'{path}[{i}]'] = item
            else:
                out[path] = v
    return out


@router.get("/config/diff")
def config_diff(db_id: str):
    """当前 TOML vs 仓库基线（git HEAD 版本）逐键差异 → Markdown 报告（D5）

    无 git 时回退对比内置 LEGACY 默认表涉及的关键键。返回 {diff_count, report_md}。
    """
    _resolve_db_path(db_id)
    p = _config_path()
    if not os.path.exists(p):
        raise HTTPException(404, f"配置文件不存在: {p}")
    import tomllib
    import subprocess
    with open(p, "rb") as f:
        cur = tomllib.load(f)
    # 基线: git show HEAD:参数/工程配置.toml（无 git 仓库时为 None）
    base_raw = None
    try:
        r = subprocess.run(["git", "show", f"HEAD:参数/工程配置.toml"],
                           cwd=os.path.dirname(p) + "/..", capture_output=True,
                           timeout=10)
        if r.returncode == 0:
            base_raw = r.stdout.decode("utf-8", errors="replace")
    except Exception:
        base_raw = None
    base = tomllib.loads(base_raw) if base_raw else {}
    cur_f, base_f = _flatten_cfg(cur), _flatten_cfg(base)
    rows = []
    for k in sorted(set(cur_f) | set(base_f)):
        cv, bv = cur_f.get(k, "<缺失>"), base_f.get(k, "<缺失>")
        if cv != bv:
            kind = "新增" if k not in base_f else ("删除" if k not in cur_f else "修改")
            rows.append((k, kind, bv, cv))
    lines = ["# 参数差异报告（当前 vs git 基线）", "",
             f"- 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 差异键数: **{len(rows)}**", "",
             "| 参数路径 | 类型 | 基线值 | 当前值 |", "|---|---|---|---|"]
    for k, kind, bv, cv in rows:
        lines.append(f"| {k} | {kind} | `{bv}` | `{cv}` |")
    if not rows:
        lines.append("（与基线完全一致）")
    md = "\n".join(lines)
    # 落盘供下载
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "reports", db_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "参数差异报告.md")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception:
        out = None
    return {"ok": True, "diff_count": len(rows), "report_md": md,
            "file": os.path.basename(out) if out else None}


@router.get("/config/check-intervals")
def config_check_intervals(db_id: str):
    """区间连续性校验（D5，桌面 config_studio.check_interval_continuity 同语义）：

    校验 TOML 内 N 值分档区间表相邻档衔接性。三种表语义不同（勿混用）：
      - 字符串【浮点】闭区间表（"0.5~1.0" 类，首中即返）：端点相等 = 衔接；
        仅报内含重叠 lo2 < hi1 与真缺口 lo2 > hi1。
      - 字符串【整数】闭区间表（"4~7"/"8~14"，实测整数 N，TOML 注释明示
        "闭区间整数衔接写法"）：相邻档须 hi+1 == lo（4~7 与 8~14 衔接，非缺口）。
      - 整数表（最小/最大 dict，实测整数 N）：同整数衔接语义。
    表语义自动判定：端点全为整数的字符串表按整数衔接；含小数按浮点闭区间。
    返回 {ok, issues:[{path, kind, detail}], checked}。
    """
    _resolve_db_path(db_id)
    import tomllib
    p = _config_path()
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    issues = []
    # (路径, 标签) — 语义按表内容自动判定（见 docstring）
    targets = [
        ('公用.标贯N_密实度', '密实度(N)'),
        ('公用.动探N63_5_密实度', '动探密实度(N63.5)'),
        ('A类.标贯N_可塑性', 'A类 标贯N→可塑性'),
        ('B类.标贯N_可塑性', 'B类 标贯N→可塑性'),
        ('A类.标贯N_风化程度', 'A类 标贯N→风化程度'),
        ('B类.标贯N_风化程度', 'B类 标贯N→风化程度'),
        ('公用.手动修正_密实度N值范围', '手动修正·密实度'),
        ('A类.手动修正_可塑性N值范围', 'A类 手动修正·可塑性'),
        ('B类.手动修正_可塑性N值范围', 'B类 手动修正·可塑性'),
        ('A类.手动修正_标贯N值范围', 'A类 手动修正·标贯'),
        ('B类.手动修正_标贯N值范围', 'B类 手动修正·标贯'),
    ]
    import re as _re
    checked = 0
    for path, label in targets:
        node = cfg
        ok_path = True
        for part in path.split('.'):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok_path = False
                break
        if not ok_path or not isinstance(node, dict):
            continue
        checked += 1
        spans = []
        has_float = False
        has_int_dict = False
        for state, rng in node.items():
            if state == '用途':
                continue
            if isinstance(rng, dict):
                # 整数表: {最小, 最大}
                try:
                    lo = float(rng.get('最小'))
                    hi = float(rng.get('最大'))
                    if lo != int(lo) or hi != int(hi):
                        has_float = True
                    else:
                        has_int_dict = True
                    spans.append((state, lo, hi))
                except (TypeError, ValueError):
                    pass
                continue
            if isinstance(rng, str):
                m = _re.match(r'^(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)$', rng.strip())
                if m:
                    lo, hi = float(m.group(1)), float(m.group(2))
                    if lo != int(lo) or hi != int(hi):
                        has_float = True
                    spans.append((state, lo, hi))
        if len(spans) < 2:
            continue
        # 语义判定: 整数端点（且存在 dict 整数表或全整数字符串表）→ 整数衔接；否则浮点闭区间
        all_int = all(lo == int(lo) and hi == int(hi) for _s, lo, hi in spans)
        semantics = 'decimal' if (has_float or not all_int) else 'int'
        spans.sort(key=lambda t: t[1])
        for (s1, lo1, hi1), (s2, lo2, hi2) in zip(spans, spans[1:]):
            if semantics == 'decimal':
                # 浮点闭区间: 端点相等=衔接
                if lo2 < hi1:
                    issues.append({"path": path, "label": label, "kind": "overlap",
                                   "detail": f'{s1}({lo1:g}~{hi1:g}) 与 {s2}({lo2:g}~{hi2:g}) 内部重叠 {lo2:g}~{hi1:g}'})
                elif lo2 > hi1:
                    issues.append({"path": path, "label": label, "kind": "gap",
                                   "detail": f'{s1}({lo1:g}~{hi1:g}) 与 {s2}({lo2:g}~{hi2:g}) 之间存在缺口 {hi1:g}~{lo2:g}'})
            else:
                # 整数衔接: hi+1 == lo 为衔接；hi == lo（共享整数端点）同为刻意的闭区间
                # 衔接写法（TOML 注释明示"≤10 与 10~15 相邻，N=10→松散、N=10.1→稍密"，
                # 端点归属由规则引擎"升序左界+首中即返"确定，不属重叠）；
                # 仅 lo2 > hi1+1（缺口）或 lo2 < hi1（内部重叠）告警
                if lo2 > hi1 + 1:
                    issues.append({"path": path, "label": label, "kind": "gap",
                                   "detail": f'{s1}({lo1:g}~{hi1:g}) 与 {s2}({lo2:g}~{hi2:g}) 之间存在整数缺口 {hi1+1:g}~{lo2-1:g}'})
                elif lo2 < hi1:
                    issues.append({"path": path, "label": label, "kind": "overlap",
                                   "detail": f'{s1}({lo1:g}~{hi1:g}) 与 {s2}({lo2:g}~{hi2:g}) 内部重叠 {lo2:g}~{hi1:g}'})
    return {"ok": len(issues) == 0, "issues": issues, "checked": checked}


@router.post("/config/restore-key")
def config_restore_key(db_id: str, payload: dict):
    """单项还原（D5，桌面 config_studio 单项还原语义）：

    {path: "段.子段.键"} → 从 git 基线取该键值写回当前 TOML（TomlFile 保留注释）。
    无基线/键在基线缺失时 422。"""
    _resolve_db_path(db_id)
    p = _config_path()
    path = (payload or {}).get("path") or ""
    if not path or ".." in path:
        raise HTTPException(400, "缺少 path")
    import subprocess
    import tomllib
    base_raw = None
    try:
        r = subprocess.run(["git", "show", f"HEAD:参数/工程配置.toml"],
                           cwd=os.path.dirname(p) + "/..", capture_output=True, timeout=10)
        if r.returncode == 0:
            base_raw = r.stdout.decode("utf-8", errors="replace")
    except Exception:
        base_raw = None
    if not base_raw:
        raise HTTPException(422, "无 git 基线可还原（请先建立基线提交）")
    base = tomllib.loads(base_raw)
    node = base
    for part in path.split('.'):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise HTTPException(422, f"基线中不存在该键: {path}")
    from review.toml_preserve import TomlFile
    tf = TomlFile(p)
    if not (tf.set(path, node) or tf.add(path, node)):
        raise HTTPException(422, f"还原失败（路径无法定位）: {path}")
    import shutil
    shutil.copy2(p, p + ".bak")
    tf.save(p)
    _reload_config_modules()
    return {"ok": True, "path": path, "restored_value": node, "backup": os.path.basename(p) + ".bak"}
