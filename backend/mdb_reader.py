#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · .mdb 读取适配层
================================================================
统一读取 Access .mdb / 理正 .lz 数据库，自动探测可用后端：

  1) pyodbc + ACE OLEDB（Windows，首选）—— 完整读写能力
  2) mdbtools CLI（Linux 服务器）—— 只读，mdb-tables / mdb-export

用途：网页上传 .mdb 后，由服务端调用本模块解析并导入 SQLite 工作库。

用法（作为模块 import）:
  from mdb_reader import MdbReader
  reader = MdbReader()                 # 自动探测后端
  tables = reader.list_tables("a.lz")  # -> ['ZK', 'DZ', ...]
  rows, cols = reader.read_table("a.lz", "ZK", limit=50)
================================================================
"""
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ACE 驱动名候选（pyodbc.drivers() 里的名称）
ACE_DRIVER_NAMES = ["Microsoft Access Driver", "Microsoft Access Driver (*.mdb", "ACE ODBC"]

class MdbBackendError(Exception):
    """无法找到可用的 .mdb 读取后端"""

class MdbReader:
    def __init__(self):
        self.backend = self._detect()
        print(f"[mdb_reader] 后端: {self.backend}")

    # ---------- 后端探测 ----------
    def _detect(self):
        # 1) Windows：pyodbc + ACE
        try:
            import pyodbc
            drivers = pyodbc.drivers()
            for d in drivers:
                if "Access" in d or "ACE" in d:
                    return "ace"
        except Exception:
            pass
        # 2) Linux：mdbtools
        if shutil.which("mdb-tables") and shutil.which("mdb-export"):
            return "mdbtools"
        raise MdbBackendError(
            "未找到可用后端：Windows 需安装 Access Database Engine（ACE），"
            "Linux 需安装 mdbtools（apt install mdbtools）。"
        )

    # ---------- 列表 ----------
    def list_tables(self, mdb_path):
        if self.backend == "ace":
            return self._list_ace(mdb_path)
        return self._list_mdbtools(mdb_path)

    def _list_ace(self, mdb_path):
        import pyodbc
        conn_str = self._conn_str(mdb_path)
        # 优先 ADOX.Catalog 枚举（MSysObjects 无读取权限时报 -1907）
        try:
            import win32com.client
            cat = win32com.client.Dispatch("ADOX.Catalog")
            cat.ActiveConnection = conn_str
            names = []
            for t in cat.Tables:
                tn = t.Name
                if not tn.startswith("MSys") and str(t.Type).upper() != "SYSTEM TABLE":
                    names.append(tn)
            return sorted(names)
        except Exception:
            pass
        # fallback：直接查询 MSysObjects（管理员连接可用）
        conn = pyodbc.connect(conn_str)
        try:
            cur = conn.cursor()
            cur.execute("SELECT Name FROM MSysObjects WHERE Type=1 AND Flags=0 ORDER BY Name")
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def _list_mdbtools(self, mdb_path):
        out = subprocess.run(["mdb-tables", "-1", mdb_path],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(f"mdb-tables 失败: {out.stderr}")
        return [t for t in out.stdout.splitlines() if t.strip()]

    # ---------- 读表 ----------
    def read_table(self, mdb_path, table, limit=None):
        """返回 (columns:list[str], rows:list[list])"""
        if self.backend == "ace":
            return self._read_ace(mdb_path, table, limit)
        return self._read_mdbtools(mdb_path, table, limit)

    def _conn_str(self, mdb_path):
        import pyodbc
        drv = next(d for d in pyodbc.drivers() if "Access" in d or "ACE" in d)
        return rf"DRIVER={{{drv}}};DBQ={mdb_path};READONLY=TRUE;"

    def _read_ace(self, mdb_path, table, limit):
        import pyodbc
        conn = pyodbc.connect(self._conn_str(mdb_path))
        try:
            cur = conn.cursor()
            cols = [c[0] for c in cur.execute(f'SELECT TOP 1 * FROM [{table}]').description]
            sql = f'SELECT * FROM [{table}]'
            if limit:
                sql = f'SELECT TOP {int(limit)} * FROM [{table}]'
            cur.execute(sql)
            rows = [list(r) for r in cur.fetchall()]
            return cols, rows
        finally:
            conn.close()

    def _read_mdbtools(self, mdb_path, table, limit):
        out = subprocess.run(["mdb-export", "-D", "%Y-%m-%d %H:%M:%S", mdb_path, table],
                             capture_output=True, text=True, timeout=120, errors="replace")
        if out.returncode != 0:
            raise RuntimeError(f"mdb-export 失败: {out.stderr[:300]}")
        lines = out.stdout.splitlines()
        if not lines:
            return [], []
        cols = lines[0].split(",")
        rows = []
        for ln in lines[1:]:
            if not ln.strip():
                continue
            # 简单 CSV 解析（处理带引号字段）
            rows.append(self._parse_csv_line(ln))
            if limit and len(rows) >= limit:
                break
        return cols, rows

    @staticmethod
    def _parse_csv_line(line):
        out, cur, in_q = [], "", False
        for ch in line:
            if ch == '"':
                in_q = not in_q
            elif ch == "," and not in_q:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        out.append(cur.strip())
        return out

    # ---------- 全量导出为 dict ----------
    def read_all(self, mdb_path, limit_per_table=None):
        """返回 {表名: {"columns": [...], "rows": [[...]]}}"""
        result = {}
        for t in self.list_tables(mdb_path):
            try:
                cols, rows = self.read_table(mdb_path, t, limit_per_table)
                result[t] = {"columns": cols, "rows": rows}
            except Exception as e:
                print(f"[mdb_reader] 跳过表 {t}: {e}")
        return result


if __name__ == "__main__":
    # 命令行自检：python mdb_reader.py <file.mdb> [表名]
    path = sys.argv[1] if len(sys.argv) > 1 else "sample/lizheng_review_sample.lz"
    r = MdbReader()
    tables = r.list_tables(path)
    print(f"✓ 表列表 ({len(tables)}): {tables}")
    target = sys.argv[2] if len(sys.argv) > 2 else (tables[0] if tables else None)
    if target:
        cols, rows = r.read_table(path, target, limit=3)
        print(f"✓ 表 [{target}] 列: {cols}")
        for row in rows[:3]:
            print(f"   {row}")
