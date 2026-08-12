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


def test_dxf_profile(db_id):
    """纵断面 DXF 生成 + 下载"""
    r = client.post(f"/api/db/{db_id}/dxf/profile?holes=26-ZD-GZXT-0-1,26-ZD-GZXT-0-2&h_scale=500&v_scale=500", json={})
    if r.status_code == 400:
        pytest.skip("钻孔编号不适用于该库")
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j.get("file", "").endswith(".dxf")
    d = client.get(f"/api/db/{db_id}/dxf/download?file={j['file']}")
    assert d.status_code == 200
    assert d.content[:6] in (b"0\nSEC", b"0\r\nSEC", b"0\nSECT") or len(d.content) > 1000


def test_dxf_columns(db_id):
    r = client.post(f"/api/db/{db_id}/dxf/columns", json={})
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("file", "").endswith(".dxf")


def test_config_get_put(db_id):
    """参数中心：读取 TOML → 原样保存（备份 + 校验）"""
    g = client.get(f"/api/db/{db_id}/config")
    assert g.status_code == 200
    raw = g.json()["raw_text"]
    assert len(raw) > 500
    assert "公用" in raw
    # 非法 TOML 拒绝
    bad = client.put(f"/api/db/{db_id}/config", json={"raw_text": "not = = valid toml"})
    assert bad.status_code == 422, "非法 TOML 应拒绝"
    # 原样回写
    good = client.put(f"/api/db/{db_id}/config", json={"raw_text": raw})
    assert good.status_code == 200, good.text[:200]
    assert good.json()["ok"]


def test_review_include_test(db_id):
    """复核设置口径：include_test 开关影响问题数（与桌面端 enable_test_review 一致）"""
    off = client.post(f"/api/db/{db_id}/review?include_test=false&project_type=B", json={})
    on = client.post(f"/api/db/{db_id}/review?include_test=true&project_type=B", json={})
    assert off.status_code == 200 and on.status_code == 200
    t_off, t_on = off.json()["summary"]["total"], on.json()["summary"]["total"]
    assert t_off >= 0 and t_on >= t_off, f"含土工判别问题数应≥不含土工: {t_on} vs {t_off}"
    if IS_REAL:
        assert t_off == 50, f"B类无土工应 50（桌面端 enable_test_review=False 口径），实际 {t_off}"
        assert t_on == 147, f"B类含土工应 147（桌面端一致），实际 {t_on}"
