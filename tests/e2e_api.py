# -*- coding: utf-8 -*-
"""理反 Web · 自动化回归测试（FastAPI TestClient，免起服务）

覆盖目标验收：
  1) 上传 .lz/.mdb → SQLite 工作库（100% 解析）
  2) 全库复核 / 单孔复核 / 标贯扫描（功能还原）
  3) 统计产物（岩溶 A/B、土工）生成与下载（功能还原）
  4) 在线 CRUD → 修改落库（写入链路）
  5) 桌面版一致性（规则引擎 IDENTICAL，需真实库 fixture）

运行：python -m pytest tests/e2e_api.py -v  或  python tests/e2e_api.py
"""
import os
import sys
import json

import pytest

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "review"))

from fastapi.testclient import TestClient  # noqa: E402
import main as app_main  # noqa: E402

client = TestClient(app_main.app)

# fixture：真实库重建产物（85 表 / 408 孔），存在则用，否则用 sample
FIXTURE_LZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "work", "dbs", "e2e_real.lz")
SAMPLE_LZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sample", "lizheng_review_sample.lz")
UPLOAD_SRC = FIXTURE_LZ if os.path.exists(FIXTURE_LZ) else SAMPLE_LZ
IS_REAL = os.path.exists(FIXTURE_LZ)


@pytest.fixture(scope="module")
def db_id():
    with open(UPLOAD_SRC, "rb") as f:
        r = client.post("/api/upload", files={"file": (os.path.basename(UPLOAD_SRC), f, "application/octet-stream")})
    assert r.status_code == 200, f"上传失败: {r.text[:300]}"
    data = r.json()
    assert data.get("db_id")
    assert data.get("tables")
    return data["db_id"]


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_upload_tables(db_id):
    r = client.get(f"/api/db/{db_id}/tables")
    assert r.status_code == 200
    tables = r.json()["tables"]
    assert len(tables) > 0


def test_table_read_paged(db_id):
    t = "z_ZuanKong" if "z_ZuanKong" in client.get(f"/api/db/{db_id}/tables").json()["tables"] else "ZK"
    r = client.get(f"/api/db/{db_id}/table/{t}?page=1&page_size=20")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] > 0
    assert len(j["rows"]) <= 20


def test_review_all(db_id):
    r = client.post(f"/api/db/{db_id}/review", json={})
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    s = j["summary"]
    assert s["holes"] > 0
    assert s["total"] == s["h"] + s["m"], "H+M 与 total 不一致"
    if IS_REAL:
        assert s["holes"] == 408, f"真实库应 408 孔，实际 {s['holes']}"
        assert s["total"] == 147, f"真实库应 147 问题，实际 {s['total']}"


def test_review_single(db_id):
    # 取第一个有数据的孔
    r = client.get(f"/api/db/{db_id}/table/z_ZuanKong?page_size=1")
    if r.status_code != 200:
        pytest.skip("无 z_ZuanKong 表")
    zk = r.json()["rows"][0].get("ZKBH") or r.json()["rows"][0].get("钻孔编号")
    rr = client.get(f"/api/db/{db_id}/review/{zk}")
    assert rr.status_code in (200, 404), rr.text[:200]


def test_spt_scan(db_id):
    r = client.post(f"/api/db/{db_id}/spt-scan", json={})
    assert r.status_code == 200, r.text[:300]
    assert "suggestions" in r.json()


def test_stats_karst(db_id):
    r = client.post(f"/api/db/{db_id}/stats/karst?project_type=B", json={})
    assert r.status_code == 200, r.text[:300]
    files = r.json().get("files") or []
    assert files, "岩溶统计未生成文件"


def test_stats_soil(db_id):
    r = client.post(f"/api/db/{db_id}/stats/soil?project_type=A", json={})
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("file")


def test_stats_download(db_id):
    r = client.post(f"/api/db/{db_id}/stats/soil?project_type=A", json={})
    f = r.json()["file"]
    d = client.get(f"/api/db/{db_id}/stats/download?file={f}")
    assert d.status_code == 200
    assert len(d.content) > 0


def test_crud_write_roundtrip(db_id):
    """在线 CRUD：修改一行 → 回读确认落库（写入链路核心）"""
    t = "z_ZuanKong"
    r = client.get(f"/api/db/{db_id}/table/{t}?page_size=1")
    if r.status_code != 200:
        pytest.skip("无表")
    j = r.json()
    row = j["rows"][0]
    row_id = row.get("id")
    if not row_id:
        pytest.skip("无 id 列")
    field = "ZKSD" if "ZKSD" in row else None
    if not field:
        pytest.skip("无 ZKSD 字段")
    old = row[field]
    new = float(old or 0) + 1.0
    u = client.put(f"/api/db/{db_id}/table/{t}/{row_id}", json={"data": {field: new}})
    assert u.status_code == 200, u.text[:200]
    r2 = client.get(f"/api/db/{db_id}/table/{t}?page=1&page_size=200")
    updated = next((x for x in r2.json()["rows"] if x.get("id") == row_id), None)
    assert updated is not None, "修改后行未找到"
    assert abs(float(updated[field]) - new) < 1e-6, f"回读不一致: {updated[field]} vs {new}"


def test_desktop_consistency():
    """桌面版 vs Web 规则引擎一致性（真实库）：两版复核结果逐孔 IDENTICAL"""
    if not IS_REAL:
        pytest.skip("需要真实库 fixture")
    import subprocess
    import tempfile
    web_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runner = os.path.join(web_root, "work", "cmp_runner.py")
    if not os.path.exists(runner):
        pytest.skip("一致性脚本不存在（work/cmp_runner.py）")
    r = subprocess.run([sys.executable, "-X", "utf8", runner], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=web_root, timeout=900)
    assert "RESULT: IDENTICAL" in r.stdout, f"一致性失败:\n{r.stdout[-800:]}\n{r.stderr[-800:]}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
