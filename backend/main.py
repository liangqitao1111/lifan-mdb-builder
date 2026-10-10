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
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
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
# 上线前安全收紧（差异清单 P2-10）：CORS 不再放行 *，仅同源部署 + 环境变量白名单
_CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS or [],   # 默认空 = 同源请求不受 CORS 限制，跨域一律拒绝
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# 复核/修正/统计 API（复用桌面版规则引擎）
app.include_router(review_api.router)
app.include_router(feature_api.router)

# ---------- 登录（含限流） ----------
from pydantic import BaseModel as _BM

# 上线前安全收紧（差异清单 P2-10）：登录失败限流 —— 同 IP 5 次失败锁定 10 分钟
import time as _time

_LOGIN_MAX_FAILS = int(os.environ.get("LOGIN_MAX_FAILS", "5"))
_LOGIN_LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_SECONDS", "600"))
_login_fails = {}          # ip -> [fail_count, locked_until_ts]
_login_lock = threading.Lock()

# Only accept X-Forwarded-For from explicitly configured reverse proxies.
# An arbitrary client header must never choose its own rate-limit bucket.
_TRUSTED_PROXY_NETWORKS = []
for _proxy in os.environ.get("TRUSTED_PROXY_IPS", "").split(","):
    _proxy = _proxy.strip()
    if not _proxy:
        continue
    try:
        _TRUSTED_PROXY_NETWORKS.append(ipaddress.ip_network(_proxy, strict=False))
    except ValueError:
        # A bad deployment setting should fail closed (ignore the proxy list),
        # while keeping the application available for local diagnostics.
        pass


def _is_trusted_proxy(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        return False
    return any(addr in network for network in _TRUSTED_PROXY_NETWORKS)


def _client_ip(request: Request) -> str:
    remote = request.client.host if request.client else "?"
    if not _is_trusted_proxy(remote):
        return remote

    # Walk the forwarded chain from the app-facing proxy toward the client.
    # Only trusted proxy hops are consumed; the first remaining address is the
    # client identity used for throttling. Invalid entries are ignored.
    forwarded = []
    for raw in (request.headers.get("x-forwarded-for") or "").split(","):
        raw = raw.strip()
        try:
            ipaddress.ip_address(raw)
        except ValueError:
            continue
        forwarded.append(raw)
    while forwarded and _is_trusted_proxy(forwarded[-1]):
        forwarded.pop()
    return (forwarded[-1] if forwarded else remote)


def _login_blocked(ip: str) -> bool:
    with _login_lock:
        rec = _login_fails.get(ip)
        if not rec:
            return False
        if rec[1] and rec[1] > _time.time():
            return True
        if rec[1] and rec[1] <= _time.time():
            _login_fails.pop(ip, None)  # 锁定过期重置
        return False


def _login_record_fail(ip: str):
    with _login_lock:
        rec = _login_fails.setdefault(ip, [0, 0])
        rec[0] += 1
        if rec[0] >= _LOGIN_MAX_FAILS:
            rec[1] = _time.time() + _LOGIN_LOCK_SECONDS
            rec[0] = 0


def _login_clear(ip: str):
    with _login_lock:
        _login_fails.pop(ip, None)


class LoginPayload(_BM):
    username: str = ""
    password: str = ""


@app.post("/api/login")
def login(payload: LoginPayload, request: Request = None):
    ip = _client_ip(request) if request is not None else "?"
    if _login_blocked(ip):
        auth.audit(payload.username or "?", "login_locked", f"POST /api/login ip={ip}", 429)
        raise HTTPException(429, "失败次数过多，账号已临时锁定，请 10 分钟后重试")
    token = auth.login(payload.username, payload.password)
    if not token:
        _login_record_fail(ip)
        raise HTTPException(401, "用户名或密码错误")
    _login_clear(ip)
    return {"token": token, "user": payload.username.strip()}


@app.post("/api/logout")
def logout(request: Request):
    hdr = request.headers.get("Authorization", "")
    token = hdr[7:] if hdr.startswith("Bearer ") else None
    if token:
        auth.logout(token)
    return {"ok": True}


# ---------- 鉴权中间件（拦 /api/*，放行 login/health/静态） ----------
_PUBLIC_PATHS = ("/api/login", "/api/health")


@app.middleware("http")
async def auth_middleware(request, call_next):
    from starlette.responses import JSONResponse
    path = request.url.path
    # CORS 预检不携带 Bearer token；必须交给 CORSMiddleware 处理，不能被
    # 业务鉴权提前返回 401。实际请求仍按原规则鉴权。
    if request.method == "OPTIONS" or not path.startswith("/api") or path in _PUBLIC_PATHS:
        return await call_next(request)
    auth_hdr = request.headers.get("Authorization", "")
    token = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else None
    user = auth.check_token(token)
    if not user:
        return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
    # 让需要维护用户级临时状态的路由（例如单层撤销栈）复用已验证身份，
    # 避免不同登录用户在同一工作库上互相覆盖撤销记录。
    request.state.user = user
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
            # 先检查 ZIP 元数据，再以有界分块读取。copyfileobj 会在检查完成前
            # 将压缩炸弹完整写入磁盘，导致服务所在卷被耗尽。
            if target.file_size < 0 or target.file_size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"解压后 .mdb 超过大小上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
            out = os.path.join(UPLOAD_DIR, f"{db_id}.mdb")
            written = 0
            try:
                with z.open(target) as src, open(out, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_UPLOAD_BYTES:
                            raise HTTPException(413, f"解压后 .mdb 超过大小上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
                        dst.write(chunk)
            except Exception:
                try:
                    os.remove(out)
                except OSError:
                    pass
                raise
            print(f"[upload] .lz 解压取 MDB: {target.filename} -> {out}")
            return out
    except zipfile.BadZipFile:
        print(f"[upload] .lz 非 ZIP（裸 MDB 兼容），直接按 MDB 读取: {lz_path}")
        return lz_path


# ---------- 上传：.mdb/.lz → SQLite ----------
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "512")) * 1024 * 1024  # 默认 512MB


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".mdb", ".lz", ".accdb"):
        raise HTTPException(400, f"仅支持 .mdb/.lz/.accdb，收到 {ext or '未知'}")
    db_id = str(uuid.uuid4())[:8]
    orig_path = os.path.join(UPLOAD_DIR, f"{db_id}{ext}")
    # P2-6：流式写入 + 大小上限（此前无限制，超大文件可耗尽磁盘）
    size = 0
    with open(orig_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                f.close()
                os.remove(orig_path)
                raise HTTPException(413, f"文件超过大小上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
            f.write(chunk)
    # .lz：解压取内嵌 MDB（含密码重试逻辑由 mdb_reader 提供）。失败时连同
    # 原始压缩包一起清理，避免反复上传坏包/炸弹包累积占满上传目录。
    try:
        mdb_path = _extract_mdb_from_lz(orig_path, db_id) if ext == ".lz" else orig_path
    except HTTPException:
        try:
            os.remove(orig_path)
        except OSError:
            pass
        raise
    except ValueError as e:
        try:
            os.remove(orig_path)
        except OSError:
            pass
        raise HTTPException(422, f"解析 .lz 失败: {e}")
    # 解压后的 .mdb 同样受大小上限约束（ZIP 炸弹防护：压缩包小、解压后巨大）
    if mdb_path != orig_path and os.path.exists(mdb_path):
        if os.path.getsize(mdb_path) > MAX_UPLOAD_BYTES:
            for _p in (mdb_path, orig_path):
                try:
                    os.remove(_p)
                except OSError:
                    pass
            raise HTTPException(413, f"解压后 .mdb 超过大小上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    db_path = os.path.join(DB_DIR, f"{db_id}.db")
    try:
        stats = wdb.import_mdb(db_path, mdb_path, db_id=db_id)
    except Exception as e:
        for _p in {db_path, mdb_path, orig_path}:
            try:
                os.remove(_p)
            except OSError:
                pass
        raise HTTPException(422, f"解析 .mdb 失败: {e}")
    if not stats:
        for _p in {db_path, mdb_path, orig_path}:
            try:
                os.remove(_p)
            except OSError:
                pass
        raise HTTPException(422, "未解析到任何表")
    return {
        "db_id": db_id,
        "file": file.filename,
        "size": size,
        "tables": stats,
    }


# ---------- 查询 ----------
@app.get("/api/db/{db_id}/tables")
def list_tables(db_id: str):
    path = resolve_db(db_id)
    return {"db_id": db_id, "tables": wdb.list_tables(path)}

@app.get("/api/db/{db_id}/table/{table}")
def read_table(db_id: str, table: str, page: int = 1, page_size: int = 50,
               keyword: str = "", search_col: str = "", exact: bool = False,
               order_by: str = "", order_dir: str = "asc"):
    path = resolve_db(db_id)
    # P1-⑪：双端钳制——此前只钳上限（min(page_size, 200)），page_size=-1 时
    # SQLite LIMIT -1 = 无限制，单请求拉全表（实测 2904 行全量返回）；page<1 同理
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    try:
        return wdb.get_rows(path, table, page, page_size,
                            keyword or None, search_col or None, exact,
                            order_by or None, order_dir)
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


# ---------- 批量保存 + 撤销（一致性清单 D2 / D1 / A3-Web 版） ----------
# 单事务批量应用 + 写后回读校验（D1, fail-closed）+ 服务端单层撤销栈（D2）
_UNDO_STACKS = {}          # (db_id, user) -> undo_ops（单层栈，对齐桌面 _undo 语义）
_UNDO_STACKS_LOCK = threading.Lock()
_UNDO_STACK_MAX = 1        # 桌面端为单层撤销栈；保持同语义


def _undo_key(db_id: str, request: Request = None):
    """撤销记录按工作库和登录用户隔离；直接调用函数时使用稳定的内部键。"""
    user = getattr(getattr(request, "state", None), "user", None) if request else None
    return (db_id, user or "__direct__")


@app.post("/api/db/{db_id}/batch")
def batch_apply_route(db_id: str, payload: dict, request: Request = None):
    """批量保存：{ops:[{op:update|insert|delete, table, row_id?, data}]}
    单事务 + 写后回读校验（任一失败整体回滚）。成功后记录撤销栈。"""
    path = resolve_db(db_id)
    ops = (payload or {}).get("ops") or []
    if not isinstance(ops, list) or not ops:
        raise HTTPException(400, "缺少 ops")
    if len(ops) > 2000:
        raise HTTPException(400, "单批操作数超限（≤2000）")
    # D1 是服务端不变量，客户端不得关闭回读校验；明确拒绝 false，避免
    # 调用方误以为已获得“成功”而实际未验证写入。
    verify = (payload or {}).get("verify", True)
    if verify is not True:
        raise HTTPException(400, "批量写回必须启用回读校验（verify=true）")
    res, undo_ops = wdb.batch_apply(path, ops, verify=True)
    if not res.get("ok"):
        raise HTTPException(422, json.dumps(res, ensure_ascii=False))
    with _UNDO_STACKS_LOCK:
        _UNDO_STACKS[_undo_key(db_id, request)] = undo_ops
    return res


@app.post("/api/db/{db_id}/undo")
def undo_route(db_id: str, request: Request = None):
    """撤销最近一次批量保存（服务端单层栈；无可撤销时 404）"""
    path = resolve_db(db_id)
    key = _undo_key(db_id, request)
    # 持锁执行撤销，避免同一用户并发 batch/undo 时读到过期记录。只有成功
    # 后才清栈；失败保留记录，允许修复外部冲突后重试。
    with _UNDO_STACKS_LOCK:
        undo_ops = _UNDO_STACKS.get(key)
        if not undo_ops:
            raise HTTPException(404, "没有可撤销的批量保存")
        res = wdb.apply_undo_ops(path, undo_ops, verify=True)
        if res.get("ok"):
            _UNDO_STACKS.pop(key, None)
    if not res.get("ok"):
        raise HTTPException(422, f"撤销失败: {res.get('error') or res.get('failures')}")
    return res


@app.get("/api/db/{db_id}/undo/status")
def undo_status(db_id: str, request: Request = None):
    with _UNDO_STACKS_LOCK:
        n = len(_UNDO_STACKS.get(_undo_key(db_id, request)) or [])
    return {"db_id": db_id, "can_undo": n > 0, "ops": n}


# ---------- 触发生成 .lz（双通道：本地 ADOX / GitHub Actions）----------
def _write_build_status(db_id: str, **kw):
    status_file = os.path.join(DB_DIR, f"{db_id}_build.json")
    data = {"db_id": db_id, "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    data.update(kw)
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_LOCAL_BUILD_CACHE = None


def _detect_local_build():
    """探测本机是否具备本地重建能力（Windows + pywin32 + pyodbc + ACE 驱动）

    返回 (可否本地构建, 原因说明)；结果按进程缓存。
    """
    global _LOCAL_BUILD_CACHE
    if _LOCAL_BUILD_CACHE is not None:
        return _LOCAL_BUILD_CACHE
    if sys.platform != "win32":
        _LOCAL_BUILD_CACHE = (False, "非 Windows 平台（Linux/Docker 无 ACE OLEDB 驱动）")
        return _LOCAL_BUILD_CACHE
    try:
        import win32com.client  # noqa: F401  ADOX.Catalog 建库必需
    except ImportError:
        _LOCAL_BUILD_CACHE = (False, "缺少 pywin32（ADOX 建库依赖）")
        return _LOCAL_BUILD_CACHE
    try:
        import pyodbc  # noqa: F401  建表/灌数据走 pyodbc
    except ImportError:
        _LOCAL_BUILD_CACHE = (False, "缺少 pyodbc")
        return _LOCAL_BUILD_CACHE
    try:
        drivers = [d for d in pyodbc.drivers()
                   if "Microsoft Access" in d and ("mdb" in d or "accdb" in d)]
        if not drivers:
            _LOCAL_BUILD_CACHE = (False, "未安装 Microsoft Access Database Engine（ACE ODBC）驱动")
            return _LOCAL_BUILD_CACHE
        _LOCAL_BUILD_CACHE = (True, f"本地重建可用（驱动: {drivers[0]}）")
    except Exception as e:
        _LOCAL_BUILD_CACHE = (False, f"pyodbc 驱动枚举失败: {e}")
    return _LOCAL_BUILD_CACHE


def _build_local(db_path: str, schema_path: str, db_id: str) -> dict:
    """本地通道：sqlite_to_mdb 重建 → verify_mdb 子进程回读校验（fail-closed）

    COM 注意：ADOX.Catalog 是 COM 组件，**每个线程必须先 CoInitialize**——
    FastAPI 后台工作线程默认未初始化 COM（报"尚未调用 CoInitialize"），此处显式初始化。
    校验不过 → 删除产物并抛 RuntimeError（绝不落假产物）。
    """
    out_lz = os.path.join(ARTIFACT_DIR, f"{db_id}.lz")
    sys.path.insert(0, BASE_DIR)
    # COM 线程初始化（Windows only；失败不影响后续——create_database 自会给出明确错误）
    if sys.platform == "win32":
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
    try:
        import sqlite_to_mdb as s2m
        import sqlite3 as _sq
        conn = _sq.connect(db_path)
        try:
            conn.row_factory = _sq.Row
            tables = {}
            for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                tables[name] = [dict(r) for r in conn.execute(f'SELECT * FROM "{name}"')]
            with open(schema_path, encoding="utf-8") as f:
                schema = json.load(f)
            project = f"lifan_{s2m.sanitize_db_id(db_id)}"
            ts = datetime.datetime.now().strftime(s2m.TS_FMT)
            import tempfile
            tmpdir = tempfile.mkdtemp(prefix="lifan_local_build_")
            mdb_path = os.path.join(tmpdir, project, f"database{ts}", "LZGICAD1.mdb")
            os.makedirs(os.path.dirname(mdb_path), exist_ok=True)
            try:
                s2m.create_database(mdb_path)
                n_tables = s2m.build_schema(mdb_path, tables, schema, sqlite_conn=conn)
                n_rows = s2m.fill_data(mdb_path, tables, schema, sqlite_conn=conn)
                s2m.pack_lz(project, ts, mdb_path, out_lz)
            finally:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            conn.close()
    finally:
        if sys.platform == "win32":
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
    # verify_mdb 走子进程（与 CLI/验收脚本同路径，避免模块级 sys.exit 干扰服务）
    r = subprocess.run([sys.executable, os.path.join(BASE_DIR, "verify_mdb.py"),
                        "--mdb", out_lz, "--sqlite", db_path, "--schema", schema_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0:
        try:
            os.remove(out_lz)
        except OSError:
            pass
        raise RuntimeError(
            f"verify_mdb 回读校验未通过（产物已删除）: {(r.stdout or '')[-300:]} {(r.stderr or '')[-200:]}")
    summary = {"tables": n_tables, "rows": n_rows, "artifact": os.path.basename(out_lz),
               "verify": "passed"}
    _write_build_status(db_id, status="success", channel="local", **summary)
    return summary


@app.post("/api/db/{db_id}/build")
def build_mdb(db_id: str, channel: str = "auto", owner: str = BUILD_OWNER_DEFAULT, repo: str = BUILD_REPO_DEFAULT):
    """触发生成 .lz（双通道）

    channel=auto（默认）：本机具备 Windows+ACE 能力时走【本地构建】——同步逻辑、
    后台线程执行、免 GH_TOKEN、产物带 verify_mdb fail-closed 校验；
    否则回退 GitHub Actions（异步轮询，需 GH_TOKEN）。
    channel=local / github：强制指定通道（不可用时 422/500 并说明原因）。
    """
    path = resolve_db(db_id)
    schema_path = wdb.schema_path_for(path)
    schema = wdb.export_schema(path)
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)

    local_ok, local_reason = _detect_local_build()
    ch = (channel or "auto").lower()
    if ch in ("local", "auto") and local_ok:
        _write_build_status(db_id, status="running", channel="local")

        def _run_local():
            try:
                _build_local(path, schema_path, db_id)
            except Exception as e:
                _write_build_status(db_id, status="failed", channel="local", error=str(e))

        threading.Thread(target=_run_local, daemon=True).start()
        return JSONResponse({"db_id": db_id, "channel": "local",
                             "message": "本地构建已启动（ADOX 重建 + verify_mdb 校验，预计数十秒）",
                             "poll": f"/api/db/{db_id}/build/status"})

    if ch == "local":
        raise HTTPException(422, f"本机不具备本地构建能力: {local_reason}")
    # GitHub 通道（auto 回退 或 强制 github）
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HTTPException(500, f"本机不具备本地构建能力（{local_reason}），且服务端未配置 GH_TOKEN，无法走 GitHub Actions")
    try:
        import github_trigger as gt
        trigger_build = getattr(gt, "trigger_build", None)
    except Exception as e:
        raise HTTPException(500, f"加载 github_trigger 失败: {e}")
    if trigger_build is None:
        raise HTTPException(500, "github_trigger.trigger_build 尚未实现（等待 Agent B 落地）")

    def _run():
        try:
            trigger_build(token, owner, repo, db_id, path, schema_path)
        except BaseException as e:  # github_trigger 内部 sys.exit() 抛 SystemExit，同样算失败
            _write_build_status(db_id, status="failed", channel="github", error=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"db_id": db_id, "channel": "github",
                         "message": "已触发生成（GitHub Actions），后台执行中",
                         "poll": f"/api/db/{db_id}/build/status"})


@app.get("/api/db/{db_id}/build/capability")
def build_capability(db_id: str):
    """构建能力探测（前端据此显示"本地生成"或"GitHub Actions"通道提示）"""
    resolve_db(db_id)
    local_ok, local_reason = _detect_local_build()
    has_gh = bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    return {"db_id": db_id,
            "local": {"available": local_ok, "reason": local_reason},
            "github": {"available": has_gh,
                       "reason": None if has_gh else "未配置 GH_TOKEN/GITHUB_TOKEN"}}


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

