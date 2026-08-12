# -*- coding: utf-8 -*-
"""理反 Web · SQLite 兼容连接层（Access DAO 无侵入适配）

桌面版 dao.py 期望 pyodbc 风格连接：conn.cursor() → cursor.execute(sql, params)
→ cursor.fetchall() → 行对象支持属性访问 r.ZKBH。本层用 sqlite3 实现同接口，
并做 Access→SQLite 的 SQL 翻译（SELECT TOP n → LIMIT n；[] 标识符 SQLite 原生支持；
? 参数占位符两库一致）。

用法：
    from sqlite_dao import connect_sqlite
    from dao import DataAccess
    da = DataAccess(connect_sqlite("work.db"))
"""
import re
import sqlite3

_TOP_RE = re.compile(r"SELECT\s+TOP\s+(\d+)", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def translate_sql(sql):
    """Access SQL → SQLite：'SELECT TOP n ...' → 'SELECT ... LIMIT n'（若未含 LIMIT）"""
    m = _TOP_RE.search(sql)
    if not m:
        return sql
    n = m.group(1)
    out = _TOP_RE.sub("SELECT", sql, count=1)
    if not _LIMIT_RE.search(out):
        out = out.rstrip().rstrip(";") + f" LIMIT {n}"
    return out


class AttrRow:
    """pyodbc 风格行：支持 r.ZKBH（大小写不敏感）与 r[0] 索引访问"""
    __slots__ = ("_keys", "_values")

    def __init__(self, keys, values):
        self._keys = [str(k) for k in keys]
        self._values = list(values)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        up = name.upper()
        for k, v in zip(self._keys, self._values):
            if k.upper() == up:
                return v
        raise AttributeError(name)

    def __getitem__(self, i):
        return self._values[i]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"AttrRow({dict(zip(self._keys, self._values))})"


class SqliteCursor:
    """sqlite3 游标包装：execute 返回 self（pyodbc 链式）、fetchall 返回 AttrRow"""
    def __init__(self, conn):
        self._conn = conn
        self._cur = None
        self.description = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(translate_sql(sql), list(params or []))
        self.description = [(d[0],) for d in self._cur.description] if self._cur.description else None
        return self

    def fetchall(self):
        if self._cur is None:
            return []
        rows = self._cur.fetchall()
        keys = [d[0] for d in self._cur.description] if self._cur.description else []
        return [AttrRow(keys, list(r)) for r in rows]

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def close(self):
        self._cur = None


class SqliteConn:
    """sqlite3 连接包装：pyodbc 风格接口"""
    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def cursor(self):
        return SqliteCursor(self._conn)

    def execute(self, sql, params=None):
        return SqliteCursor(self._conn).execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    @property
    def autocommit(self):
        return False

    @autocommit.setter
    def autocommit(self, v):
        pass


def connect_sqlite(db_path):
    return SqliteConn(db_path)
