#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · FastAPI 后端骨架
================================================================
MVP 闭环的服务器端：上传 .mdb → 解析 → SQLite 工作库 → 在线 CRUD → 触发生成 .mdb

启动（开发）:
  uvicorn main:app --host 0.0.0.0 --port 8000

API 一览:
  POST  /api/upload                上传 .lz/.mdb → 解析入库 → 返回 db_id
  GET   /api/db/{db_id}/tables     列出工作库表
  GET   /api/db/{db_id}/table/{t}  读表（分页/搜索）
  POST  /api/db/{db_id}/table/{t}  新增一行
  PUT   /api/db/{db_id}/table/{t}/{row_id}  更新一行
  DELETE/api/db/{db_id}/table/{t}/{row_id}  删除一行
  POST  /api/db/{db_id}/build      触发生成 .mdb（GitHub Actions）
  GET   /api/db/{db_id}/download   下载 SQLite 工作库
  GET   /api/health                健康检查
================================================================
"""
import os
import shutil
import sys
import threading
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import db as wdb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "..", "work", "uploads")   # 上传的 .mdb 原件
DB_DIR = os.path.join(BASE_DIR, "..", "work", "dbs")           # SQLite 工作库
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DB_DIR, exist_ok=True)

app = FastAPI(title="理反 Web API", version="0.1.0",
              description="理反 V3.0.4 网页化 · .mdb 闭环后端")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ---------- 数据模型 ----------
class RowPayload(BaseModel):
    data: dict

class BuildResponse(BaseModel):
    db_id: str
    run_id: int
    status: str

# ---------- 工具 ----------
def resolve_db(db_id: str):
    """校验 db_id 合法且工作库存在，返回 db 路径"""
    if not db_id or ".." in db_id or not db_id.replace("-", "").isalnum():
        raise HTTPException(400, "非法 db_id")
    path = os.path.join(DB_DIR, f"{db_id}.db")
    if not os.path.exists(path):
        raise HTTPException(404, f"工作库 {db_id} 不存在")
    return path

# ---------- 上传：.mdb → SQLite ----------
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".mdb", ".lz", ".accdb"):
        raise HTTPException(400, f"仅支持 .mdb/.lz/.accdb，收到 {ext or '未知'}")
    db_id = str(uuid.uuid4())[:8]
    mdb_path = os.path.join(UPLOAD_DIR, f"{db_id}{ext}")
    with open(mdb_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    db_path = os.path.join(DB_DIR, f"{db_id}.db")
    try:
        stats = wdb.import_mdb(db_path, mdb_path)
    except Exception as e:
        raise HTTPException(422, f"解析 .mdb 失败: {e}")
    if not stats:
        raise HTTPException(422, "未解析到任何表")
    return {
        "db_id": db_id,
        "file": file.filename,
        "size": os.path.getsize(mdb_path),
        "tables": stats,
    }

# ---------- 查询 ----------
@app.get("/api/db/{db_id}/tables")
def list_tables(db_id: str):
    path = resolve_db(db_id)
    return {"db_id": db_id, "tables": wdb.list_tables(path)}

@app.get("/api/db/{db_id}/table/{table}")
def read_table(db_id: str, table: str, page: int = 1, page_size: int = 50,
               keyword: str = "", search_col: str = ""):
    path = resolve_db(db_id)
    try:
        return wdb.get_rows(path, table, page, min(page_size, 200),
                            keyword or None, search_col or None)
    except Exception as e:
        raise HTTPException(404, f"读表失败: {e}")

# ---------- 写操作 ----------
@app.post("/api/db/{db_id}/table/{table}")
def insert_row(db_id: str, table: str, payload: RowPayload):
    path = resolve_db(db_id)
    try:
        return wdb.upsert_row(path, table, payload.data)
    except Exception as e:
        raise HTTPException(400, f"新增失败: {e}")

@app.put("/api/db/{db_id}/table/{table}/{row_id}")
def update_row(db_id: str, table: str, row_id: int, payload: RowPayload):
    path = resolve_db(db_id)
    try:
        return wdb.upsert_row(path, table, payload.data, row_id)
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
@app.post("/api/db/{db_id}/build")
def build_mdb(db_id: str, owner: str = "liangqitao1111", repo: str = "lifan-mdb-builder"):
    path = resolve_db(db_id)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HTTPException(500, "服务端未配置 GH_TOKEN，无法触发 GitHub Actions")
    # 导出 schema.json 到工作库同目录，供 workflow 使用
    schema = wdb.export_schema(path)
    schema_path = path.replace(".db", "_schema.json")
    with open(schema_path, "w", encoding="utf-8") as f:
        import json
        json.dump(schema, f, ensure_ascii=False, indent=2)

    # 后台线程触发（GitHub 生成需 3-5 分钟，不阻塞请求）
    run_box = {}

    def _run():
        try:
            import github_trigger as gt
            run_id = gt.trigger(token, owner, repo, path, schema_path)
            run_box["run_id"] = run_id
            run_box["conclusion"] = gt.poll(token, owner, repo, run_id, timeout=600)
        except Exception as e:
            run_box["error"] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return JSONResponse({"db_id": db_id, "message": "已触发生成，后台执行中",
                         "poll": f"/api/db/{db_id}/build/status"})

@app.get("/api/db/{db_id}/build/status")
def build_status(db_id: str):
    path = resolve_db(db_id)
    status_file = path.replace(".db", "_build.json")
    if os.path.exists(status_file):
        import json
        with open(status_file, encoding="utf-8") as f:
            return json.load(f)
    return {"db_id": db_id, "status": "unknown"}

# ---------- 下载 ----------
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
