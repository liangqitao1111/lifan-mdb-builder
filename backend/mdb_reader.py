#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
理反 Web · .mdb 读取适配层
================================================================
统一读取 Access .mdb / 理正 .lz 数据库，自动探测可用后端：

  1) pyodbc / pypyodbc + ACE OLEDB（Windows，首选）—— 完整读写能力
  2) mdbtools CLI（Linux 服务器）—— 只读，mdb-tables / mdb-export / mdb-schema

用途：网页上传 .mdb 后，由服务端调用本模块解析并导入 SQLite 工作库。

密码库支持（契约 §5）：
  - 每次建连按「无密码 → 失败用理正库密码重试」的顺序打开；
    理正密码常量 LIZHENG_PWD = %2.3#5B.@8，通过连接串 PWD= 参数传入。
  - main.py 上传路径经 db.import_mdb → 本模块读取，自动获得重试逻辑。

类型保真（契约 §3）：
  - ACE 后端：读取 pyodbc/pypyodbc cursor.description 的 SQL 类型码，映射为
    mdb_type（LONG / DOUBLE / TEXT(n) / MEMO / LONGBINARY / DATETIME / BIT 等），
    禁止从数据值推断。
  - mdbtools 后端：解析 `mdb-schema -T <表>` 输出的列类型，归一化为同一套 mdb_type。

用法（作为模块 import）:
  from mdb_reader import MdbReader
  reader = MdbReader()                 # 自动探测后端
  tables = reader.list_tables("a.lz")  # -> ['ZK', 'DZ', ...]
  schema = reader.table_schema("a.lz", "ZK")  # -> [{"name":..., "mdb_type":...}]
  rows, cols = reader.read_table("a.lz", "ZK", limit=50)
================================================================
"""
import os
import re
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 理正库密码（契约 §5）：无密码打开失败后重试
LIZHENG_PWD = "%2.3#5B.@8"

try:
    import pyodbc
except ImportError:
    import pypyodbc as pyodbc

# ACE 驱动名候选（pyodbc.drivers() 里的名称）
ACE_DRIVER_NAMES = ["Microsoft Access Driver", "Microsoft Access Driver (*.mdb", "ACE ODBC"]


class MdbBackendError(Exception):
    """无法找到可用的 .mdb 读取后端"""


# ---------------------------------------------------------------------------
# ACE 类型码 -> mdb_type（契约 §3 映射；禁止从值推断）
# ---------------------------------------------------------------------------
def _ace_mdb_type(type_code, internal_size):
    """由 pyodbc/pypyodbc description 的 (type_code, internal_size) 映射 mdb_type"""
    name = getattr(type_code, "__name__", str(type_code))
    if name in ("str",):
        if internal_size is None or internal_size <= 0:
            return "TEXT"
        if internal_size > 255:
            return "MEMO"
        return f"TEXT({int(internal_size)})"
    if name in ("int",):
        return "LONG"
    if name in ("float",):
        return "DOUBLE"
    if name == "bool":
        return "BIT"
    if name in ("datetime", "date", "time"):
        return "DATETIME"
    if name in ("bytes", "bytearray"):
        return "LONGBINARY"
    if name in ("Decimal", "decimal"):
        return "CURRENCY"
    return "TEXT"


class MdbReader:
    def __init__(self):
        self.backend = self._detect()
        print(f"[mdb_reader] 后端: {self.backend}")

    # ---------- 后端探测 ----------
    def _detect(self):
        # 1) Windows：pyodbc/pypyodbc + ACE
        try:
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

    # ---------- 连接串 ----------
    def _conn_str(self, mdb_path, pwd=None):
        drv = next(d for d in pyodbc.drivers() if "Access" in d or "ACE" in d)
        cs = rf"DRIVER={{{drv}}};DBQ={mdb_path};READONLY=TRUE;"
        if pwd:
            cs += f"PWD={pwd};"
        return cs

    def _open(self, mdb_path):
        """按契约 §5 顺序建连：无密码 → 失败用理正密码重试"""
        last_exc = None
        for pwd in (None, LIZHENG_PWD):
            try:
                return pyodbc.connect(self._conn_str(mdb_path, pwd))
            except Exception as e:
                last_exc = e
        raise last_exc

    # ---------- 列表 ----------
    def list_tables(self, mdb_path):
        if self.backend == "ace":
            return self._list_ace(mdb_path)
        return self._list_mdbtools(mdb_path)

    def _list_ace(self, mdb_path):
        # 1) SQLTables 枚举（pyodbc/pypyodbc 均可用，无 MSysObjects 权限问题）
        conn = self._open(mdb_path)
        try:
            cur = conn.cursor()
            rows = cur.tables(tableType="TABLE")
            names = []
            for r in rows:
                tn = r[2]
                if tn and not tn.startswith(("MSys", "~", "Sys")):
                    names.append(tn)
            if names:
                return sorted(names)
        except Exception:
            pass
        finally:
            conn.close()
        # 2) ADOX.Catalog 枚举
        try:
            import win32com.client
            cat = win32com.client.Dispatch("ADOX.Catalog")
            for pwd in (None, LIZHENG_PWD):
                try:
                    cat.ActiveConnection = self._conn_str(mdb_path, pwd)
                    break
                except Exception:
                    continue
            names = []
            for t in cat.Tables:
                tn = t.Name
                if not tn.startswith("MSys") and str(t.Type).upper() != "SYSTEM TABLE":
                    names.append(tn)
            return sorted(names)
        except Exception:
            pass
        # 3) 兜底：MSysObjects（管理员连接可用）
        conn = self._open(mdb_path)
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

    # ---------- 表结构（mdb_type，契约 §3） ----------
    def table_schema(self, mdb_path, table):
        """返回 [{"name": 列名, "mdb_type": 原始字段类型}]"""
        if self.backend == "ace":
            return self._schema_ace(mdb_path, table)
        return self._schema_mdbtools(mdb_path, table)

    def _schema_ace(self, mdb_path, table):
        conn = self._open(mdb_path)
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT TOP 1 * FROM [{table}]")
            return [
                {"name": d[0], "mdb_type": _ace_mdb_type(d[1], d[3])}
                for d in cur.description
            ]
        finally:
            conn.close()

    # mdbtools mdb-schema 类型归一化：{匹配正则: mdb_type}
    _MDBTOOLS_TYPE_RULES = [
        (re.compile(r"^TEXT\s*\(\s*(\d+)\s*\)$", re.I), None),   # 长度<=255 -> TEXT(n)
        (re.compile(r"^(?:CHAR|CHARACTER|VARCHAR)\s*\(\s*(\d+)\s*\)$", re.I), None),
        (re.compile(r"^TEXT$", re.I), "MEMO"),
        (re.compile(r"^MEMO$", re.I), "MEMO"),
        (re.compile(r"^LONG\s+INTEGER$", re.I), "LONG"),
        (re.compile(r"^INTEGER$", re.I), "LONG"),
        (re.compile(r"^COUNTER$", re.I), "COUNTER"),
        (re.compile(r"^SMALLINT$", re.I), "SHORT"),
        (re.compile(r"^BYTE$", re.I), "BYTE"),
        (re.compile(r"^SINGLE$", re.I), "SINGLE"),
        (re.compile(r"^(DOUBLE|REAL|FLOAT)$", re.I), "DOUBLE"),
        (re.compile(r"^CURRENCY$", re.I), "CURRENCY"),
        (re.compile(r"^(DECIMAL|NUMERIC)", re.I), "DECIMAL"),
        (re.compile(r"^(DATETIME|DATE|TIME)$", re.I), "DATETIME"),
        (re.compile(r"^(BIT|LOGICAL|YESNO|BOOLEAN)$", re.I), "BIT"),
        (re.compile(r"^(LONGBINARY|OLE|BINARY|IMAGE)$", re.I), "LONGBINARY"),
        (re.compile(r"^GUID$", re.I), "GUID"),
    ]

    def _normalize_mdbtools_type(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return "TEXT"
        for pat, fixed in self._MDBTOOLS_TYPE_RULES:
            m = pat.match(raw)
            if m:
                if fixed:
                    return fixed
                # TEXT(n) / CHAR(n) / VARCHAR(n)：n<=255 -> TEXT(n)，更长 -> MEMO
                n = int(m.group(1))
                return f"TEXT({n})" if n <= 255 else "MEMO"
        return "TEXT"

    def _schema_mdbtools(self, mdb_path, table):
        out = subprocess.run(["mdb-schema", "-T", table, mdb_path],
                             capture_output=True, text=True, timeout=60,
                             errors="replace")
        if out.returncode != 0:
            raise RuntimeError(f"mdb-schema 失败: {out.stderr[:300]}")
        cols = []
        # 解析形如：CREATE TABLE `表` ( `col` LONG INTEGER, ... );
        in_create = False
        for ln in out.stdout.splitlines():
            s = ln.strip()
            if s.upper().startswith("CREATE TABLE"):
                in_create = True
                continue
            if not in_create or not s:
                continue
            if s.startswith(")"):
                break
            s = s.rstrip(",").strip()
            m = re.match(r"^`?([^`\s]+)`?\s+(.+)$", s)
            if not m:
                m = re.match(r'^"([^"]+)"\s+(.+)$', s)
            if m:
                cols.append({
                    "name": m.group(1).strip("`").strip('"'),
                    "mdb_type": self._normalize_mdbtools_type(m.group(2)),
                })
        return cols

    # ---------- 读表 ----------
    def read_table(self, mdb_path, table, limit=None):
        """返回 (columns:list[str], rows:list[list])"""
        if self.backend == "ace":
            return self._read_ace(mdb_path, table, limit)
        return self._read_mdbtools(mdb_path, table, limit)

    def _read_ace(self, mdb_path, table, limit):
        conn = self._open(mdb_path)
        try:
            cur = conn.cursor()
            sql = f"SELECT * FROM [{table}]"
            if limit:
                sql = f"SELECT TOP {int(limit)} * FROM [{table}]"
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
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
        """简单 CSV 行解析：支持带引号字段与转义引号（"" → 字面引号）"""
        out, cur, in_q = [], "", False
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                if in_q and i + 1 < len(line) and line[i + 1] == '"':
                    cur += '"'
                    i += 2
                    continue
                in_q = not in_q
            elif ch == "," and not in_q:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
            i += 1
        out.append(cur.strip())
        return out

    # ---------- 全量导出为 dict（含 mdb 原始类型） ----------
    def read_all(self, mdb_path):
        """返回 {表名: {"columns": [...], "rows": [[...]], "mdb_types": {列: mdb_type}}}"""
        if self.backend == "ace":
            return self._read_all_ace(mdb_path)
        result = {}
        for t in self.list_tables(mdb_path):
            try:
                cols, rows = self.read_table(mdb_path, t)
                schema = self.table_schema(mdb_path, t)
                result[t] = {
                    "columns": cols,
                    "rows": rows,
                    "mdb_types": {c["name"]: c["mdb_type"] for c in schema},
                }
            except Exception as e:
                print(f"[mdb_reader] 跳过表 {t}: {e}")
        return result

    def _read_all_ace(self, mdb_path):
        """ACE 单连接遍历：每表一次 SELECT 同时取列定义（类型码）与数据，避免重复建连"""
        result = {}
        conn = self._open(mdb_path)
        try:
            for t in self.list_tables(mdb_path):
                try:
                    cur = conn.cursor()
                    cur.execute(f"SELECT * FROM [{t}]")
                    cols = [d[0] for d in cur.description]
                    mdb_types = {d[0]: _ace_mdb_type(d[1], d[3]) for d in cur.description}
                    rows = [list(r) for r in cur.fetchall()]
                    result[t] = {"columns": cols, "rows": rows, "mdb_types": mdb_types}
                except Exception as e:
                    print(f"[mdb_reader] 跳过表 {t}: {e}")
        finally:
            conn.close()
        return result


if __name__ == "__main__":
    # 命令行自检：python mdb_reader.py <file.mdb> [表名]
    path = sys.argv[1] if len(sys.argv) > 1 else "sample/lizheng_review_sample.lz"
    r = MdbReader()
    tables = r.list_tables(path)
    print(f"✓ 表列表 ({len(tables)}): {tables[:10]} ...")
    target = sys.argv[2] if len(sys.argv) > 2 else (tables[0] if tables else None)
    if target:
        schema = r.table_schema(path, target)
        print(f"✓ 表 [{target}] schema: {schema[:6]} ...")
        cols, rows = r.read_table(path, target, limit=3)
        print(f"✓ 表 [{target}] 列: {cols[:6]} ... 行示例: {rows[:1]}")

