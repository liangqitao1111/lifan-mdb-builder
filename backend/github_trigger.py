#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · GitHub Actions MDB 生成触发脚本
================================================================
一条命令完成：触发 workflow → 轮询状态 → 下载 .mdb 产物

用法（Windows / macOS / Linux 通用，仅标准库，无需 pip install）:
  # 1) 触发 + 自动轮询到完成（默认用 sample/ 测试数据）
  python github_trigger.py --token <PAT> [--owner 账号 --repo lifan-mdb-builder]
                           [--sqlite sample/work.db --schema sample/schema.json]

  # 2) 只查看最近一次 run 状态
  python github_trigger.py --token <PAT> --status

  # 3) 下载最近一次成功产物到指定目录
  python github_trigger.py --token <PAT> --download output/

  # 4) 触发后指定具体 run_id 轮询（适合网页后端异步场景）
  python github_trigger.py --token <PAT> --poll <run_id>

token 获取: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained
  (需要 Actions: Read/Write 权限；仓库选 lifan-mdb-builder)
================================================================
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Windows 控制台默认 cp1252，打印中文会报错 —— 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.github.com"
EVENT = "build-mdb"          # 与 workflow 的 repository_dispatch types 对应


def api_req(url, token, method="GET", body=None):
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
            # POST /dispatches 成功返回 204 No Content（空响应体）
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"❌ HTTP {e.code}: {err[:400]}")
        sys.exit(1)


def trigger(token, owner, repo, sqlite, schema):
    """触发 workflow（repository_dispatch）并返回 run_id"""
    print(f"▶ 触发 workflow: {owner}/{repo} (event={EVENT})")
    body = {
        "event_type": EVENT,
        "client_payload": {"sqlite_path": sqlite, "schema_path": schema},
    }
    api_req(f"{API}/repos/{owner}/{repo}/dispatches", token, "POST", body)
    print("✓ 已提交触发请求（repository_dispatch）")
    # repository_dispatch 是异步的，等几秒让 GitHub 创建 run
    time.sleep(8)
    runs = api_req(
        f"{API}/repos/{owner}/{repo}/actions/runs?event=repository_dispatch&per_page=1",
        token,
    )
    runs_list = runs.get("workflow_runs") or []
    if not runs_list:
        print("⚠ 未找到新 run（可能还在排队，可用 --poll 指定 run_id 或稍后 --status 查看）")
        sys.exit(1)
    run = runs_list[0]
    print(f"→ run #{run['id']}  status={run['status']}  conclusion={run.get('conclusion')}")
    print(f"  {run['html_url']}")
    return run["id"]


def poll(token, owner, repo, run_id, timeout=480):
    """轮询 run 直到完成，返回 conclusion；超时返回 None"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = api_req(f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}", token)
        status, concl = run.get("status"), run.get("conclusion")
        print(f"  [{time.strftime('%H:%M:%S')}] {status}  conclusion={concl}")
        if status == "completed":
            return concl
        time.sleep(15)
    print("⏰ 轮询超时（workflow 可能仍在执行）")
    return None


def get_artifact(token, owner, repo, run_id):
    """列出 run 的 artifact，返回 (name, archive_download_url) 或 None"""
    arts = api_req(f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts", token)
    items = arts.get("artifacts") or []
    if not items:
        print("⚠ 该 run 没有 artifact（可能未成功或已过期）")
        return None
    art = items[0]
    print(f"✓ 找到产物: {art['name']}  ({art['size_in_bytes']/1024:.0f} KB)")
    return art["name"], art["archive_download_url"]


def download(token, owner, repo, run_id, out_dir):
    """下载 artifact（zip）到 out_dir 并解压；OAuth token 走不通时自动用 gh CLI"""
    res = get_artifact(token, owner, repo, run_id)
    if not res:
        return
    name, url = res
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"{name}.zip")
    headers = {"User-Agent": "lifan-web-builder"}
    req = urllib.request.Request(url, headers=headers)
    # 优先尝试 PAT token 方式
    if token.startswith(("ghp_", "github_pat_", "ghs_", "gho_")):
        req.add_header("Authorization", f"Bearer {token}")
    else:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(zip_path, "wb") as f:
            f.write(r.read())
    except urllib.error.HTTPError as e:
        # S3 重定向对 OAuth token 不友好（403），自动改用 gh CLI（已登录态）
        print(f"⚠ 直连下载失败 (HTTP {e.code})，改用 gh CLI 下载…")
        gh = os.environ.get("GH_CLI", "gh")
        rc = os.system(f'{gh} run download {run_id} -n "{name}" -D "{out_dir}" >nul 2>&1')
        if rc != 0:
            # Windows / Linux 兼容
            rc = os.system(f'{gh} run download {run_id} -n "{name}" -D "{out_dir}" 2>/dev/null')
        if rc != 0:
            print(f"❌ 自动下载也失败，请手动执行：gh run download {run_id} -n {name} -D {out_dir}")
            return None
        files = [f for f in os.listdir(out_dir) if not f.endswith(".zip")]
        print(f"✓ gh CLI 已下载到 {out_dir}/ → {files}")
        return out_dir
    print(f"✓ 已下载: {zip_path}")
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out_dir)
    files = os.listdir(out_dir)
    print(f"✓ 解压到 {out_dir}/ → {files}")
    return zip_path


def latest_successful_run(token, owner, repo):
    """找最近一次成功的 run id（用于 --download 不带 run_id）"""
    runs = api_req(
        f"{API}/repos/{owner}/{repo}/actions/runs?status=success&per_page=1",
        token,
    )
    items = runs.get("workflow_runs") or []
    if not items:
        print("⚠ 没有成功过的 run")
        sys.exit(1)
    return items[0]["id"]


def main():
    p = argparse.ArgumentParser(description="GitHub Actions MDB 生成触发/轮询/下载")
    p.add_argument("--token", default=os.environ.get("GH_TOKEN", ""), help="GitHub PAT（或设 GH_TOKEN 环境变量）")
    p.add_argument("--owner", default="liangqitao1111")
    p.add_argument("--repo", default="lifan-mdb-builder")
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
    else:
        run_id = trigger(args.token, args.owner, args.repo, args.sqlite, args.schema)

    concl = poll(args.token, args.owner, args.repo, run_id)
    if concl != "success":
        print(f"❌ workflow 未成功（结论: {concl}）→ 查看日志: gh run view {run_id} --log-failed")
        sys.exit(1)

    print("🎉 MDB 生成成功！")
    download(args.token, args.owner, args.repo, run_id, "output")
    print("完成 ✓")


if __name__ == "__main__":
    main()
