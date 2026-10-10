# -*- coding: utf-8 -*-
"""Focused regression tests for configuration, numeric input, and proxy trust."""
import ipaddress
import os
import sys
import sqlite3

from starlette.requests import Request

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.join(BACKEND, "review"))

import main  # noqa: E402
import dao  # noqa: E402
from sqlite_dao import SqliteConn  # noqa: E402


def _request(remote, forwarded=None):
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode("ascii")))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/login",
        "headers": headers,
        "client": (remote, 1234),
        "server": (remote, 8000),
        "scheme": "http",
    })


def test_forwarded_for_is_ignored_without_trusted_proxy(monkeypatch):
    monkeypatch.setattr(main, "_TRUSTED_PROXY_NETWORKS", [])
    assert main._client_ip(_request("10.0.0.9", "198.51.100.4")) == "10.0.0.9"


def test_forwarded_for_uses_client_after_trusted_proxy_chain(monkeypatch):
    monkeypatch.setattr(main, "_TRUSTED_PROXY_NETWORKS", [ipaddress.ip_network("10.0.0.0/8")])
    # XFF is ordered client -> inner proxy; app peer is the outer proxy.
    assert main._client_ip(_request("10.0.0.9", "198.51.100.4, 10.1.2.3")) == "198.51.100.4"


def test_rod_config_reload_clears_removed_or_invalid_section(monkeypatch):
    saved_l, saved_c = dao._SPT_ROD_LENGTH, dao._SPT_COEFF
    try:
        monkeypatch.setattr(dao, "load_project_config", lambda: {
            "公用": {"标贯杆长修正_B类": {"3m": 0.9, "6m": 0.8}}
        })
        dao._build_rod_config()
        assert dao._SPT_ROD_LENGTH == [3.0, 6.0]

        monkeypatch.setattr(dao, "load_project_config", lambda: {"公用": {}})
        dao._build_rod_config()
        assert dao._SPT_ROD_LENGTH == [] and dao._SPT_COEFF == []
        assert dao._spt_correction_coefficient(6) == 1.0

        monkeypatch.setattr(dao, "load_project_config", lambda: {
            "公用": {"标贯杆长修正_B类": {"bad": "oops", "9m": "nan"}}
        })
        dao._build_rod_config()
        assert dao._SPT_ROD_LENGTH == [] and dao._SPT_COEFF == []
    finally:
        dao._SPT_ROD_LENGTH, dao._SPT_COEFF = saved_l, saved_c


def test_invalid_soil_numeric_value_is_skipped_without_type_error(tmp_path):
    db_path = str(tmp_path / "bad-values.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE z_c_QuYang (ZKBH TEXT, QYBH TEXT, QYSD REAL, "
        "QYHSL REAL, QYYX REAL, QYSY REAL, QYZLMD TEXT, QYBZ REAL, QYDC TEXT)"
    )
    conn.execute(
        "INSERT INTO z_c_QuYang VALUES ('ZK-1', 'QY-1', 1, 20, 30, 10, 'bad', 2.7, '')"
    )
    conn.commit()
    conn.close()

    wrapped = SqliteConn(db_path)
    try:
        da = dao.DataAccess(wrapped)
        rows = da.get_all_test_full("B")
        assert rows["ZK-1"][0]["gmd"] is None
        assert rows["ZK-1"][0]["hsl"] == 20.0
    finally:
        wrapped.close()
