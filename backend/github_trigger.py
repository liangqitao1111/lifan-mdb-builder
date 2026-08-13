#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · GitHub Actions MDB 生成触发脚本（契约 §1/§4）
================================================================
一条命令完成：上传 payload → 触发 workflow → 轮询状态 → 下载 .lz 产物

核心接口（供 FastAPI 后端调用）:
  trigger_build(token, owner, repo, db_id, sqlite_path, schema_path)
    a. Contents API 上传 payload/{db_id}/work.db + schema.json（base64）
    b. repository_dispatch（client_payload: db_id/sqlite/schema/out）
    c. 轮询 run 至完成
    d. 成功 → 下载 artifact 缓存到 work/artifacts/{db_id}.lz
    e. 写 work/dbs/{db_id}_build.json（触发前写 running，结束写 success/failed）
  兼容旧接口：trigger() / poll()（供旧版 main.py 后台线程调用）

命令行用法（Windows / macOS / Linux 通用，仅标准库）:
  # 1) 触发 + 自动轮询到完成 + 下载产物
  python github_trigger.py --token <PAT> [--owner 账号 --repo lifan-mdb-builder
                            --db-id abc --sqlite sample/work.db --schema sample/schema.json]
  # 2) 只查看最近一次 run 状态
  python github_trigger.py --token <PAT> --status
  # 3) 下载最近一次成功产物到指定目录
  python github_trigger.py --token <PAT> --download output/
  # 4) 只轮询指定 run_id
  python github_trigger.py --token <PAT> --poll <run_id>

token: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained
  (需要 Actions: Read/Write + Contents: Read/Write 权限；仓库选 lifan-mdb-builder)
================================================================
"""
import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

# Windows 控制台默认 cp1252，打印中文会报错 —— 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.github.com"
EVENT = "build-mdb"          # 与 workflow 的 repository_dispatch types 对应
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根
ARTIFACT_DIR = os.path.join(ROOT, "work", "artifacts")
DB_STATUS_DIR = os.path.join(ROOT, "work", "dbs")


# ---------------------------------------------------------------- HTTP
def sanitize_db_id(db_id):
    """db_id 消毒：只允许 [A-Za-z0-9_-]，非法返回 None

    P1-2（安全）：此前 db_id 未校验直接拼进 artifacts/build.json 路径，
    CLI 传 '..\\..\\x' 可越界写文件（Web 路径有 main.resolve_db 保护，
    但本模块的 CLI/旧接口用法必须自防）。
    """
    if not db_id or not isinstance(db_id, str):
        return None
    if not all(c.isalnum() or c in "-_" for c in db_id):
        return None
    return db_id


def api_req(url, token, method="GET", body=None, quiet=False):
    """GitHub REST 调用；quiet=True 时 HTTP 错误返回 None（用于探测文件是否存在）"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "lifan-web-builder",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        if quiet:
            return None
        err = e.read().decode("utf-8", errors="replace")
        print(f"❌ HTTP {e.code}: {err[:400]}")
        sys.exit(1)


def _parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------- build.json
def write_build_status(db_id, status, run_id=None, conclusion=None):
    """写 work/dbs/{db_id}_build.json（契约 §1 e）：{status, run_id, conclusion, updated_at}"""
    db_id = sanitize_db_id(db_id)
    if db_id is None:
        raise ValueError("非法 db_id，拒绝写状态文件")
    payload = {
        "status": status,
        "run_id": run_id,
        "conclusion": conclusion,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    os.makedirs(DB_STATUS_DIR, exist_ok=True)
    path = os.path.join(DB_STATUS_DIR, f"{db_id}_build.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------- Contents API
def put_payload(token, owner, repo, db_id, sqlite_path, schema_path):
    """a) Contents API 上传 payload/{db_id}/work.db + schema.json（base64）"""
    base = f"payload/{db_id}"
    files = [("work.db", sqlite_path), ("schema.json", schema_path)]
    for name, local in files:
        if not os.path.exists(local):
            sys.exit(f"❌ 待上传文件不存在: {local}")
        with open(local, "rb") as f:
            content = base64.b64encode(f.read()).decode("ascii")
        path = f"{base}/{name}"
        url = f"{API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path, safe='/')}"
        body = {"message": f"lifan build {db_id}: update {name}", "content": content}
        existing = api_req(url, token, quiet=True)
        if existing and existing.get("sha"):
            body["sha"] = existing["sha"]          # 已存在需带 sha 才能覆盖
        api_req(url, token, "PUT", body)
        print(f"✓ 已上传 {path}（{len(content) // 4 * 3 // 1024} KB）")


# ---------------------------------------------------------------- 触发 + 轮询
def wait_for_run(token, owner, repo, after_ts, tries=8, sleep=5):
    """repository_dispatch 异步创建 run：找 created_at >= after_ts 的新 run

    P1-1（静默产错）：修复前带 30 秒向后容差 + 兜底取"最近一条"——30 秒内
    连续两次触发会命中上一次的 run；超时兜底可能取到几天前别的 db_id 的 run，
    若其结论 success，旧产物会被缓存到当前 db_id 名下（build.json 写 success）。
    现要求 created_at >= after_ts 精确匹配（dispatch 时间由调用方传入 UTC），
    找不到即失败退出，绝不拿旧 run 顶替。
    """
    for _ in range(tries):
        runs = api_req(
            f"{API}/repos/{owner}/{repo}/actions/runs?event=repository_dispatch&per_page=10",
            token)
        for run in (runs.get("workflow_runs") or []):
            created = _parse_ts(run.get("created_at") or "")
            if created and created >= after_ts:
                return run["id"]
        time.sleep(sleep)
    sys.exit("❌ 未找到新 run（可能还在排队，稍后可用 --poll 指定 run_id；拒绝用旧 run 顶替）")


def poll(token, owner, repo, run_id, timeout=900):
    """轮询 run 直到完成，返回 conclusion；超时返回 None（兼容旧签名）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = api_req(f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}", token)
        status, concl = run.get("status"), run.get("conclusion")
        print(f"  [{time.strftime('%H:%M:%S')}] run #{run_id}  {status}  conclusion={concl}")
        if status == "completed":
            return concl
        time.sleep(15)
    print("⏰ 轮询超时（workflow 可能仍在执行）")
    return None


# ---------------------------------------------------------------- artifact
class _NoAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """S3 artifact 签名直链拒绝 Authorization 头：重定向时剥离（GitHub API 302 → S3）"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newreq = super().redirect_request(req, fp, code, msg, headers, newurl)
        if newreq is not None:
            newreq.headers.pop("Authorization", None)
        return newreq


def fetch_artifact_zip(token, owner, repo, run_id):
    """下载 run 的 artifact（zip 字节）到临时文件，返回 (zip_path, tmpdir, art_name)"""
    arts = api_req(f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts", token)
    items = arts.get("artifacts") or []
    if not items:
        raise RuntimeError("该 run 没有 artifact（可能未成功或已过期）")
    art = items[0]
    print(f"✓ 找到产物: {art['name']}  ({art['size_in_bytes'] / 1024:.0f} KB)")
    url = art["archive_download_url"]
    headers = {"User-Agent": "lifan-web-builder", "Accept": "application/vnd.github+json"}
    if token.startswith(("ghp_", "github_pat_", "ghs_", "gho_")):
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoAuthRedirectHandler)
    try:
        with opener.open(req, timeout=180) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"下载 artifact 失败 HTTP {e.code}（可改用 gh CLI 手动下载）")
    tmpdir = tempfile.mkdtemp(prefix="lifan_art_")
    zip_path = os.path.join(tmpdir, "artifact.zip")
    with open(zip_path, "wb") as f:
        f.write(data)
    return zip_path, tmpdir, art["name"]


def extract_lz_from_artifact(zip_path):
    """artifact zip → 内部 .lz 字节

    P2-3：多 .lz 条目时排除含「备份」的（与 _extract_mdb_from_lz/verify_mdb 同口径），
    避免取到备份产物。
    """
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".lz")]
        names = [n for n in names if '备份' not in n and not n.lower().endswith('.ldb')]
        if not names:
            raise RuntimeError("artifact 内没有 .lz 文件（已排除备份/ldb）")
        return z.read(names[0])


def _safe_extract(z, out_dir):
    """P2-2（安全）：zip 解压防路径穿越——拒绝含 .. 段/绝对路径/盘符的条目"""
    for member in z.infolist():
        name = member.filename
        norm = name.replace("\\", "/")
        if norm.startswith("/") or ".." in norm.split("/") or (len(norm) >= 2 and norm[1] == ":"):
            raise RuntimeError(f"artifact 含非法路径条目，拒绝解压: {name}")
    z.extractall(out_dir)


def cache_artifact(token, owner, repo, run_id, db_id):
    """d) 下载 artifact 并缓存为 work/artifacts/{db_id}.lz，返回缓存路径"""
    db_id = sanitize_db_id(db_id)
    if db_id is None:
        raise RuntimeError("非法 db_id，拒绝缓存产物")
    zip_path, tmpdir, name = fetch_artifact_zip(token, owner, repo, run_id)
    try:
        data = extract_lz_from_artifact(zip_path)
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        dest = os.path.join(ARTIFACT_DIR, f"{db_id}.lz")
        with open(dest, "wb") as f:
            f.write(data)
        print(f"✓ 产物已缓存: {dest}（{len(data) / 1024:.0f} KB）")
        return dest
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def download(token, owner, repo, run_id, out_dir):
    """命令行下载：artifact zip 解压到 out_dir（兼容旧用法）"""
    zip_path, tmpdir, name = fetch_artifact_zip(token, owner, repo, run_id)
    try:
        os.makedirs(out_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            _safe_extract(z, out_dir)  # P2-2：防路径穿越
        files = os.listdir(out_dir)
        print(f"✓ 解压到 {out_dir}/ → {files}")
        return out_dir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def latest_successful_run(token, owner, repo):
    """找最近一次成功的 run id（用于 --download 不带 run_id）"""
    runs = api_req(f"{API}/repos/{owner}/{repo}/actions/runs?status=success&per_page=1", token)
    items = runs.get("workflow_runs") or []
    if not items:
        print("⚠ 没有成功过的 run")
        sys.exit(1)
    return items[0]["id"]


# ---------------------------------------------------------------- 主入口
def trigger_build(token, owner, repo, db_id, sqlite_path, schema_path, timeout=900):
    """契约 §1 触发生成全链路：上传 payload → dispatch → 轮询 → 缓存产物 → build.json"""
    db_id = sanitize_db_id(db_id)  # P1-2：入口统一消毒（CLI 直接传参路径）
    if db_id is None:
        raise ValueError("非法 db_id（仅允许字母/数字/-/_）")
    write_build_status(db_id, "running")                       # 触发前先写 running
    print(f"▶ 触发 workflow: {owner}/{repo} (event={EVENT}, db_id={db_id})")
    put_payload(token, owner, repo, db_id, sqlite_path, schema_path)

    body = {
        "event_type": EVENT,
        "client_payload": {
            "db_id": db_id,
            "sqlite": f"payload/{db_id}/work.db",
            "schema": f"payload/{db_id}/schema.json",
            "out": f"payload/{db_id}/output.lz",
        },
    }
    dispatch_ts = datetime.now(timezone.utc)
    api_req(f"{API}/repos/{owner}/{repo}/dispatches", token, "POST", body)
    print("✓ 已提交 repository_dispatch")
    time.sleep(3)
    run_id = wait_for_run(token, owner, repo, dispatch_ts)
    write_build_status(db_id, "running", run_id=run_id)
    print(f"→ run #{run_id}")
    run = api_req(f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}", token)
    print(f"  {run.get('html_url', '')}")

    concl = poll(token, owner, repo, run_id, timeout=timeout)
    if concl == "success":
        try:
            cache_artifact(token, owner, repo, run_id, db_id)
        except Exception as e:  # P2-1：修复前只捕 RuntimeError，BadZipFile/OSError
            # 会逃逸且 build.json 停在 running；现统一写 failed
            write_build_status(db_id, "failed", run_id=run_id, conclusion=concl)
            sys.exit(f"❌ {e}")
        write_build_status(db_id, "success", run_id=run_id, conclusion=concl)
        print(f"🎉 生成成功：work/artifacts/{db_id}.lz")
        return {"status": "success", "run_id": run_id, "conclusion": concl,
                "artifact": os.path.join(ARTIFACT_DIR, f"{db_id}.lz")}
    write_build_status(db_id, "failed", run_id=run_id, conclusion=concl)
    print(f"❌ workflow 未成功（结论: {concl}）→ 查看日志: gh run view {run_id} --log-failed")
    return {"status": "failed", "run_id": run_id, "conclusion": concl}


def trigger(token, owner, repo, sqlite, schema):
    """兼容旧接口（旧版 main.py 后台线程调用）：db_id 取 sqlite 文件名去扩展名"""
    db_id = os.path.splitext(os.path.basename(sqlite))[0]
    result = trigger_build(token, owner, repo, db_id, sqlite, schema)
    return result["run_id"]


def main():
    p = argparse.ArgumentParser(description="GitHub Actions MDB 生成触发/轮询/下载")
    p.add_argument("--token", default=os.environ.get("GH_TOKEN", ""), help="GitHub PAT（或设 GH_TOKEN 环境变量）")
    p.add_argument("--owner", default="liangqitao1111")
    p.add_argument("--repo", default="lifan-mdb-builder")
    p.add_argument("--db-id", default=None, help="工程标识（默认取 sqlite 文件名去扩展名）")
    p.add_argument("--sqlite", default="sample/work.db")
    p.add_argument("--schema", default="sample/schema.json")
    p.add_argument("--status", action="store_true", help="只查看最近 run 状态")
    p.add_argument("--download", metavar="DIR", help="下载最近一次成功产物到目录")
    p.add_argument("--poll", metavar="RUN_ID", help="只轮询指定 run")
    args = p.parse_args()

    if not args.token:
        print("❌ 缺少 token：用 --token 传入或设置环境变量 GH_TOKEN")
        sys.exit(1)

    if args.status:
        run_id = latest_successful_run(args.token, args.owner, args.repo)
        run = api_req(f"{API}/repos/{args.owner}/{args.repo}/actions/runs/{run_id}", args.token)
        print(f"最近 run #{run['id']}: {run['status']} / {run.get('conclusion')}")
        print(f"  {run['html_url']}")
        return

    if args.download:
        run_id = latest_successful_run(args.token, args.owner, args.repo)
        download(args.token, args.owner, args.repo, run_id, args.download)
        return

    if args.poll:
        run_id = int(args.poll)
        concl = poll(args.token, args.owner, args.repo, run_id)
        if concl != "success":
            sys.exit(1)
        print("✓ 该 run 成功")
        return

    db_id = args.db_id or os.path.splitext(os.path.basename(args.sqlite))[0]
    trigger_build(args.token, args.owner, args.repo, db_id, args.sqlite, args.schema)


if __name__ == "__main__":
    main()
