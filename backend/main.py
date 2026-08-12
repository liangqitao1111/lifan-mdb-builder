#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · FastAPI 后端
================================================================
理正 .mdb 闭环后端：上传 .mdb/.lz → 解析 → SQLite 工作库 → 在线 CRUD →
触发生成 .mdb（GitHub Actions）→ 下载产物（契约 V2）

启动（开发）:
  uvicorn main:app --host 0.0.0.0 --port 8000

API 一览（契约 §2）:
  POST  /api/upload                        上传 .lz/.mdb/.accdb → 解析入库 → {db_id, tables}
  GET   /api/db/{db_id}/tables             列出工作库表
  GET   /api/db/{db_id}/table/{t}          读表（分页/搜索，page_size≤200）
  POST  /api/db/{db_id}/table/{t}          新增一行 {data:{...}}
  PUT   /api/db/{db_id}/table/{t}/{row_id} 更新一行
  DELETE/api/db/{db_id}/table/{t}/{row_id} 删除一行
  POST  /api/db/{db_id}/build              触发生成 .mdb（后台线程）
  GET   /api/db/{db_id}/build/status       构建状态（读 {db_id}_build.json）
  GET   /api/db/{db_id}/artifact           下载产物 work/artifacts/{db_id}.lz
  GET   /api/db/{db_id}/download           下载 SQLite 工作库
  GET   /api/health                        健康检查
================================================================
"""
import datetime
import json
import os
import shutil
import sys
import threading
import uuid
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import db as wdb
import review_api
import feature_api
import auth

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "..", "work", "uploads")   # 上传原件（.mdb/.lz/.accdb）
DB_DIR = os.path.join(BASE_DIR, "..", "work", "dbs")           # SQLite 工作库 + schema/build.json
ARTIFACT_DIR = os.path.join(BASE_DIR, "..", "work", "artifacts")  # 构建产物 .lz
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(ARTIFACT_DIR, exist_ok=True)

app = FastAPI(title="理反 Web API", version="0.2.0",
              description="理反 V3.0.4 网页化 · .mdb 闭环后端（契约 V2）")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# 复核/修正/统计 API（复用桌面版规则引擎）
app.include_router(review_api.router)
app.include_router(feature_api.router)

# ---------- 登录 ----------
from pydantic import BaseModel as _BM


class LoginPayload(_BM):
    username: str = ""
    password: str = ""


@app.post("/api/login")
def login(payload: LoginPayload):
    token = auth.login(payload.username, payload.password)
    if not token:
        raise HTTPException(401, "用户名或密码错误")
    return {"token": token, "user": payload.username.strip()}


@app.post("/api/logout")
def logout():
    hdr = _bearer_token()
    if hdr:
        auth.logout(hdr)
    return {"ok": True}


# ---------- 鉴权中间件（拦 /api/*，放行 login/health/静态） ----------
_PUBLIC_PATHS = ("/api/login", "/api/health")


def _bearer_token():
    from starlette.requests import Request
    return None  # placeholder，中间件内直接读 header


@app.middleware("http")
async def auth_middleware(request, call_next):
    from starlette.responses import JSONResponse
    path = request.url.path
    if not path.startswith("/api") or path in _PUBLIC_PATHS:
        return await call_next(request)
    auth_hdr = request.headers.get("Authorization", "")
    token = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else None
    user = auth.check_token(token)
    if not user:
        return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
    response = await call_next(request)
    try:
        auth.audit(user, request.method, path, response.status_code)
    except Exception:
        pass
    return response

BUILD_OWNER_DEFAULT = "liangqitao1111"
BUILD_REPO_DEFAULT = "lifan-mdb-builder"


# ---------- 工具 ----------
def resolve_db(db_id: str):
    """校验 db_id 合法且工作库存在，返回 db 路径"""
    if not db_id or ".." in db_id or not db_id.replace("-", "").isalnum():
        raise HTTPException(400, "非法 db_id")
    path = os.path.join(DB_DIR, f"{db_id}.db")
    if not os.path.exists(path):
        raise HTTPException(404, f"工作库 {db_id} 不存在")
    return path


def _extract_mdb_from_lz(lz_path: str, db_id: str) -> str:
    """
    契约 §1：.lz 为 ZIP 包 → 解压取内嵌 *.mdb（排除含「备份」条目、*.ldb）。
    兼容旧版非 ZIP 的 .lz（实为裸 MDB）直接返回原文件。
    """
    try:
        with zipfile.ZipFile(lz_path) as z:
            candidates = [
                i for i in z.infolist()
                if not i.is_dir()
                and i.filename.lower().endswith(".mdb")
                and "备份" not in i.filename
                and not i.filename.lower().endswith(".ldb")
            ]
            if not candidates:
                raise ValueError("压缩包内未找到 *.mdb 条目（已排除备份/ldb）")
            target = sorted(candidates, key=lambda i: i.filename)[0]
            out = os.path.join(UPLOAD_DIR, f"{db_id}.mdb")
            with z.open(target) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"[upload] .lz 解压取 MDB: {target.filename} -> {out}")
            return out
    except zipfile.BadZipFile:
        print(f"[upload] .lz 非 ZIP（裸 MDB 兼容），直接按 MDB 读取: {lz_path}")
        return lz_path


# ---------- 上传：.mdb/.lz → SQLite ----------
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".mdb", ".lz", ".accdb"):
        raise HTTPException(400, f"仅支持 .mdb/.lz/.accdb，收到 {ext or '未知'}")
    db_id = str(uuid.uuid4())[:8]
    orig_path = os.path.join(UPLOAD_DIR, f"{db_id}{ext}")
    with open(orig_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # .lz：解压取内嵌 MDB（含密码重试逻辑由 mdb_reader 提供）
    mdb_path = _extract_mdb_from_lz(orig_path, db_id) if ext == ".lz" else orig_path
    db_path = os.path.join(DB_DIR, f"{db_id}.db")
    try:
        stats = wdb.import_mdb(db_path, mdb_path, db_id=db_id)
    except Exception as e:
        raise HTTPException(422, f"解析 .mdb 失败: {e}")
    if not stats:
        raise HTTPException(422, "未解析到任何表")
    return {
        "db_id": db_id,
        "file": file.filename,
        "size": os.path.getsize(orig_path),
        "tables": stats,
    }


# ---------- 查询 ----------
@app.get("/api/db/{db_id}/tables")
def list_tables(db_id: str):
    path = resolve_db(db_id)
    return {"db_id": db_id, "tables": wdb.list_tables(path)}

@app.get("/api/db/{db_id}/table/{table}")
def read_table(db_id: str, table: str, page: int = 1, page_size: int = 50,
               keyword: str = "", search_col: str = "", exact: bool = False):
    path = resolve_db(db_id)
    try:
        return wdb.get_rows(path, table, page, min(page_size, 200),
                            keyword or None, search_col or None, exact)
    except Exception as e:
        raise HTTPException(404, f"读表失败: {e}")

# ---------- 写操作 ----------
@app.post("/api/db/{db_id}/table/{table}")
def insert_row(db_id: str, table: str, payload: dict):
    path = resolve_db(db_id)
    try:
        return wdb.upsert_row(path, table, (payload or {}).get("data") or {})
    except Exception as e:
        raise HTTPException(400, f"新增失败: {e}")

@app.put("/api/db/{db_id}/table/{table}/{row_id}")
def update_row(db_id: str, table: str, row_id: int, payload: dict):
    path = resolve_db(db_id)
    try:
        return wdb.upsert_row(path, table, (payload or {}).get("data") or {}, row_id)
    except Exception as e:
        raise HTTPException(400, f"更新失败: {e}")

@app.delete("/api/db/{db_id}/table/{table}/{row_id}")
def delete_row(db_id: str, table: str, row_id: int):
    path = resolve_db(db_id)
    try:
        return wdb.delete_row(path, table, row_id)
    except Exception as e:
        raise HTTPException(400, f"删除失败: {e}")


# ---------- 触发生成 .mdb（GitHub Actions）----------
def _write_build_status(db_id: str, **kw):
    status_file = os.path.join(DB_DIR, f"{db_id}_build.json")
    data = {"db_id": db_id, "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    data.update(kw)
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.post("/api/db/{db_id}/build")
def build_mdb(db_id: str, owner: str = BUILD_OWNER_DEFAULT, repo: str = BUILD_REPO_DEFAULT):
    path = resolve_db(db_id)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HTTPException(500, "服务端未配置 GH_TOKEN，无法触发 GitHub Actions")
    # a. 导出最新 schema.json（v2）到 work/dbs/{db_id}_schema.json
    schema = wdb.export_schema(path)
    schema_path = wdb.schema_path_for(path)
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    # b. 调 github_trigger.trigger_build(token, owner, repo, db_id, sqlite_path, schema_path)
    try:
        import github_trigger as gt
        trigger_build = getattr(gt, "trigger_build", None)
    except Exception as e:
        raise HTTPException(500, f"加载 github_trigger 失败: {e}")
    if trigger_build is None:
        raise HTTPException(500, "github_trigger.trigger_build 尚未实现（等待 Agent B 落地）")

    # c. 后台线程触发（GitHub 生成需 3-5 分钟，不阻塞请求）
    def _run():
        try:
            trigger_build(token, owner, repo, db_id, path, schema_path)
        except BaseException as e:  # github_trigger 内部 sys.exit() 抛 SystemExit，同样算失败
            _write_build_status(db_id, status="failed", error=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"db_id": db_id, "message": "已触发生成，后台执行中",
                         "poll": f"/api/db/{db_id}/build/status"})


@app.get("/api/db/{db_id}/build/status")
def build_status(db_id: str):
    path = resolve_db(db_id)
    status_file = os.path.join(DB_DIR, f"{db_id}_build.json")
    if os.path.exists(status_file):
        with open(status_file, encoding="utf-8") as f:
            return json.load(f)
    return {"db_id": db_id, "status": "unknown"}


# ---------- 下载 ----------
@app.get("/api/db/{db_id}/artifact")
def artifact(db_id: str):
    resolve_db(db_id)
    lz_path = os.path.join(ARTIFACT_DIR, f"{db_id}.lz")
    if not os.path.exists(lz_path):
        raise HTTPException(404, f"产物 {db_id}.lz 不存在，请先构建")
    return FileResponse(lz_path, filename=f"{db_id}.lz", media_type="application/zip")


@app.get("/api/db/{db_id}/download")
def download(db_id: str):
    path = resolve_db(db_id)
    return FileResponse(path, filename=f"{db_id}_work.db",
                        media_type="application/octet-stream")


# ---------- 健康检查 ----------
@app.get("/api/health")
def health():
    return {"status": "ok", "title": app.title, "version": app.version,
            "mdb_backend": _mdb_backend_name()}

def _mdb_backend_name():
    try:
        from mdb_reader import MdbReader
        return MdbReader().backend
    except Exception as e:
        return f"unavailable: {e}"

# ---------- 前端静态托管（同源部署：FastAPI 即前端即后端）----------
try:
    from fastapi.staticfiles import StaticFiles
    _web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    if os.path.exists(os.path.join(_web_dir, "index.html")):
        app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
except Exception as e:
    print(f"[main] 静态托管未启用: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

