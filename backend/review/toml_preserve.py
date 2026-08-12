# -*- coding: utf-8 -*-
"""理反参数中心 — 保留注释的 TOML 行级编辑器（基础模块）

背景
----
参数\\工程配置.toml 含大量中文注释（用户的文档，不能丢）。标准库 tomllib 只读；
tomli-w 全量重写会丢注释。本模块提供行级补丁式编辑：

- 按行扫描建立「路径 → 行号」索引（段头 / 键行 / 注释行 / 空行）；
- set 只替换原键值行（值区段），行首缩进、键原文、行尾注释全部原样保留；
- add / delete 只增删键行，注释行、空行、段头永不触碰；
- __init__ 记录全文件快照；diff / restore / is_dirty 均相对该快照；
- save 用临时文件 + os.replace 原子替换，写后 tomllib 校验，
  失败抛 ValueError（目标文件保持原状，等价于回滚）。

路径语义
--------
- 点分隔：``"A类.标贯N_可塑性.≥30"`` = 段 A类 → 段 标贯N_可塑性 → 键 ≥30；
- 键名含点（如 ``"0.075mm粒组去重"``）时按贪婪最长匹配解析，无歧义；
- 数组表段（``[["试验指标_域规则"]]``）只读：get/keys 可用，set/add/delete 返回 False
  （多元素共享同一路径，行级编辑语义不成立）；
- 不存在的路径：set/delete 返回 False 不抛异常。

依赖：仅标准库（Python 3.11+ 用 tomllib；3.8-3.10 自动回退 tomli）。
"""
import datetime as _dt
import os
import re
import tempfile

try:  # Python 3.11+
    import tomllib
except ImportError:  # Python 3.8-3.10
    import tomli as tomllib  # type: ignore

_LOAD_ERROR = getattr(tomllib, 'TOMLDecodeError', Exception)
_LINE_RE = re.compile(r'at line (\d+)')

__all__ = ['TomlFile']

_MISSING = object()  # 哨兵：区分「取不到」与「默认值」


# =====================================================================
# 行级扫描工具
# =====================================================================
def _scan_top(line):
    """返回 (首个顶层 '=' 的位置, 首个顶层 '#' 的位置)，均 -1 表示无。

    顶层 = 不在引号内；引号支持 "…"（含 \\ 转义）与 '…'。键值行形如
    ``"键" = 值 [# 行尾注释]``，值字符串里可能含 = 或 #（引号内不算）。
    """
    eq = -1
    hash_pos = -1
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in ('"', "'"):
            quote = c
            i += 1
            while i < n:
                if quote == '"' and line[i] == '\\' and i + 1 < n:
                    i += 2
                    continue
                if line[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if c == '=' and eq < 0:
            eq = i
        elif c == '#' and hash_pos < 0:
            hash_pos = i
            break
        i += 1
    return eq, hash_pos


def _value_span(line, eq):
    """返回原始值在行内的 (start, end) 区间（含两端，不含行尾注释）。"""
    i = eq + 1
    n = len(line)
    while i < n and line[i] in ' \t':
        i += 1
    start = i
    end = n
    _, hash_pos = _scan_top(line)
    if hash_pos >= 0:
        end = hash_pos
    while end > start and line[end - 1] in ' \t':
        end -= 1
    return start, end


def _split_dotted(s):
    """按 '.' 分割段头内容（引号内的 '.' 不分割）。"""
    out, cur = [], []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in ('"', "'"):
            q = c
            cur.append(c)
            i += 1
            while i < n:
                cur.append(s[i])
                if q == '"' and s[i] == '\\' and i + 1 < n:
                    cur.append(s[i + 1])
                    i += 2
                    continue
                if s[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == '.':
            out.append(''.join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    out.append(''.join(cur))
    return [x.strip() for x in out]


_ESCAPE_MAP = {'n': '\n', 't': '\t', 'r': '\r', '"': '"',
               '\\': '\\', 'b': '\b', 'f': '\f', '/': '/'}


def _unescape_basic(s):
    """反解基本字符串（不含两端引号）的转义。"""
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '\\' and i + 1 < n:
            nxt = s[i + 1]
            if nxt in _ESCAPE_MAP:
                out.append(_ESCAPE_MAP[nxt])
                i += 2
                continue
            if nxt == 'u' and i + 6 <= n:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if nxt == 'U' and i + 10 <= n:
                try:
                    out.append(chr(int(s[i + 2:i + 10], 16)))
                    i += 10
                    continue
                except ValueError:
                    pass
        out.append(c)
        i += 1
    return ''.join(out)


def _parse_key_part(part):
    """解析键/段头的一个组成部分：'…' / "…" / 裸键 → 字符串；失败返回 None。"""
    part = part.strip()
    if not part:
        return None
    if part[0] == '"':
        return _unescape_basic(part[1:-1]) if len(part) >= 2 and part[-1] == '"' else None
    if part[0] == "'":
        return part[1:-1] if len(part) >= 2 and part[-1] == "'" else None
    return part


def _parse_table_header(line):
    """解析段头 ``[a.b]`` / ``[["a"."b"]]`` → (path_tuple, is_array)；失败返回 None。"""
    s = line.strip()
    if s.startswith('[['):
        if not s.endswith(']]'):
            return None
        inner = s[2:-2]
        is_array = True
    elif s.startswith('['):
        if not s.endswith(']'):
            return None
        inner = s[1:-1]
        is_array = False
    else:
        return None
    segs = []
    for p in _split_dotted(inner):
        k = _parse_key_part(p)
        if not k:
            return None
        segs.append(k)
    return tuple(segs), is_array


# =====================================================================
# 值序列化（tomli 风格：双引号、中文不转义、数组/内联表单行）
# =====================================================================
def _escape_basic(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\r':
            out.append('\\r')
        elif o < 0x20 or o == 0x7F:
            out.append('\\u%04X' % o)
        else:
            out.append(ch)  # 中文等按 UTF-8 原样输出
    return ''.join(out)


def _quote(s):
    return '"' + _escape_basic(s) + '"'


def _serialize_value(v):
    """把 Python 值序列化为单行 TOML；结果保证可被 tomllib 回读且语义一致。

    支持 str / int / float / bool / list / dict(内联表) / date / datetime / time。
    其余类型抛 ValueError（编程错误，由调用方处理）。
    """
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, str):
        return _quote(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return 'nan'
        if v == float('inf'):
            return 'inf'
        if v == float('-inf'):
            return '-inf'
        return repr(v)
    if isinstance(v, list):
        return '[' + ', '.join(_serialize_value(x) for x in v) + ']'
    if isinstance(v, dict):
        if not v:
            return '{}'
        inner = ', '.join('%s = %s' % (_quote(str(k)), _serialize_value(x))
                          for k, x in v.items())
        return '{ ' + inner + ' }'
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    raise ValueError('无法序列化为 TOML 的值类型: %s' % type(v).__name__)


# =====================================================================
# TomlFile
# =====================================================================
class TomlFile:
    """保留注释的 TOML 行级编辑器。

    用法::

        tf = TomlFile('参数/工程配置.toml')
        tf.set('A类.标贯N_可塑性.≥30', '密实2')   # 键存在 → True
        tf.add('A类.标贯N_可塑性.30~40', '坚硬2')  # 新增键 → True
        tf.diff()                                # [(path, old, new), ...]
        tf.save()                                # 原子写 + 校验
    """

    def __init__(self, path):
        self._path = os.path.abspath(path)
        if not os.path.isfile(self._path):
            raise ValueError('TOML 文件不存在: %s' % self._path)
        try:
            with open(self._path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            raise ValueError('读取 TOML 失败: %s: %s' % (self._path, e))
        self._bom = raw.startswith(b'\xef\xbb\xbf')
        try:
            text = raw.decode('utf-8-sig' if self._bom else 'utf-8')
        except UnicodeDecodeError as e:
            raise ValueError('TOML 文件不是合法 UTF-8: %s: %s' % (self._path, e))
        # 换行符检测：与源文件保持一致（\r\n 或 \n）
        self._newline = '\r\n' if '\r\n' in text else '\n'
        self._ends_newline = text.endswith(('\n', '\r'))
        self._orig_text = text
        self._orig_lines = self._split_text(text)
        self._lines = list(self._orig_lines)
        self._data = self._parse_or_raise('\n'.join(self._lines))
        # 行索引
        self._key_lines = {}        # path_tuple -> 当前行号（可编辑键行）
        self._section_headers = {}  # path_tuple -> 首个段头行号
        self._array_sections = set()  # 数组表段 path（只读）
        self._rebuild_index()
        # __init__ 快照（diff/restore/is_dirty 均相对此快照）
        self._snap_line_no = {}
        self._snap_line_text = {}
        self._snap_values = {}
        for tup, ln in self._key_lines.items():
            self._snap_line_no[tup] = ln
            self._snap_line_text[tup] = self._lines[ln]
        for tup in self._key_lines:
            v = self._walk(self._data, tup, _MISSING)
            if v is not _MISSING:
                self._snap_values[tup] = v

    # ---- 基础工具 ----------------------------------------------------
    @staticmethod
    def _split_text(text):
        lines = text.split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        return [ln[:-1] if ln.endswith('\r') else ln for ln in lines]

    @staticmethod
    def _parse_or_raise(text):
        try:
            return tomllib.loads(text)
        except _LOAD_ERROR as e:
            msg = str(e)
            m = _LINE_RE.search(msg)
            if m:
                raise ValueError('TOML 解析失败（行 %s）: %s' % (m.group(1), msg)) from e
            raise ValueError('TOML 解析失败: %s' % msg) from e

    @staticmethod
    def _decode_error(e, prefix):
        msg = str(e)
        m = _LINE_RE.search(msg)
        if m:
            return ValueError('%s（行 %s）: %s' % (prefix, m.group(1), msg))
        return ValueError('%s: %s' % (prefix, msg))

    def _refresh_data(self):
        self._data = self._parse_or_raise('\n'.join(self._lines))

    def _rebuild_index(self):
        key_lines = {}
        section_headers = {}
        array_sections = set()
        cur = ()
        cur_is_array = False
        for i, line in enumerate(self._lines):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('['):
                parsed = _parse_table_header(stripped)
                if parsed is None:
                    continue
                cur, cur_is_array = parsed
                section_headers.setdefault(cur, i)
                if cur_is_array:
                    array_sections.add(cur)
                continue
            eq, _ = _scan_top(stripped)
            if eq < 0:
                continue  # 其他行（如多行值续行）原样保留，不索引
            key = _parse_key_part(stripped[:eq].strip())
            if not key:
                continue
            if cur_is_array:
                continue  # 数组表内键不可行级编辑，跳过
            key_lines[cur + (key,)] = i
        self._key_lines = key_lines
        self._section_headers = section_headers
        self._array_sections = array_sections

    @staticmethod
    def _walk(node, path, default=_MISSING):
        cur = node
        for seg in path:
            if isinstance(cur, dict):
                if seg in cur:
                    cur = cur[seg]
                else:
                    return default
            elif isinstance(cur, list) and seg.isdigit():
                idx = int(seg)
                if idx < len(cur):
                    cur = cur[idx]
                else:
                    return default
            else:
                return default
        return cur

    def _split_path(self, path):
        """点分隔路径 → (path_tuple, 末节点)；贪婪最长匹配支持键名含点。"""
        parts = path.split('.')
        result = []
        node = self._data
        i = 0
        while i < len(parts):
            found = False
            for j in range(len(parts), i, -1):
                cand = '.'.join(parts[i:j])
                if isinstance(node, dict) and cand in node:
                    result.append(cand)
                    node = node[cand]
                    i = j
                    found = True
                    break
            if not found:
                return None
        return tuple(result), node

    def _resolve_key(self, path):
        r = self._split_path(path)
        if r is None:
            return None
        tup = r[0]
        return tup if tup in self._key_lines else None

    @staticmethod
    def _tuple_matches(tup, parts):
        i = 0
        for seg in tup:
            consumed = None
            for j in range(i, len(parts)):
                if '.'.join(parts[i:j + 1]) == seg:
                    consumed = j
                    break
            if consumed is None:
                return False
            i = consumed + 1
        return i == len(parts)

    def _match_snap(self, path):
        """按快照匹配路径（含已删除键；键名含点场景按快照元组精确对齐）。"""
        parts = path.split('.')
        for tup in self._snap_line_no:
            if self._tuple_matches(tup, parts):
                return tup
        return None

    # ---- 只读 --------------------------------------------------------
    def data(self):
        """完整解析结果（嵌套 dict，编辑后为最新解析值）。"""
        self._refresh_data()
        return self._data

    def get(self, path, default=None):
        r = self._split_path(path)
        return r[1] if r is not None else default

    def keys(self, path=''):
        """段或键的子键列表（按文件出现顺序）；数组表返回各元素键并集。"""
        if not path:
            return list(self._data.keys())
        r = self._split_path(path)
        if r is None:
            return []
        node = r[1]
        if isinstance(node, dict):
            return list(node.keys())
        if isinstance(node, list):
            seen = []
            for item in node:
                if isinstance(item, dict):
                    for k in item:
                        if k not in seen:
                            seen.append(k)
            return seen
        return []

    # ---- 编辑 --------------------------------------------------------
    def set(self, path, value):
        """行级替换（保留缩进、键原文、上方注释与行尾注释）；键存在返回 True。"""
        tup = self._resolve_key(path)
        if tup is None:
            return False
        ln = self._key_lines[tup]
        line = self._lines[ln]
        eq, _ = _scan_top(line)
        if eq < 0:
            return False
        start, end = _value_span(line, eq)
        serialized = _serialize_value(value)  # 先序列化，失败不落行
        new_line = line[:start] + serialized + line[end:]
        self._lines[ln] = new_line
        try:
            self._refresh_data()
        except Exception:
            self._lines[ln] = line  # 回滚该行
            raise
        return True

    def add(self, path, value):
        """新增键：插入到所属段最后一个键行之后（段不存在则新建段头）。

        数组表段 / 隐式表段 / 键已存在 → 返回 False。
        """
        parts = path.split('.')
        if not parts or any(p == '' for p in parts):
            return False
        key = parts[-1]
        section = tuple(parts[:-1])
        # 数组表段内不可安全插入
        for i in range(1, len(section) + 1):
            if section[:i] in self._array_sections:
                return False
        serialized = _serialize_value(value)
        if not section:
            # 顶层键：必须位于所有段头之前，插到首个段头前（紧跟文件头注释块）
            if key in self._data:
                return False
            pos = 0
            while pos < len(self._lines):
                s = self._lines[pos].strip()
                if s == '' or s.startswith('#'):
                    pos += 1
                else:
                    break
            self._lines.insert(pos, _quote(key) + ' = ' + serialized)
            self._rebuild_index()
            self._refresh_data()
            return True
        # 定位段
        node = self._data
        resolved = True
        for seg in section:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                resolved = False
                break
        if isinstance(node, list):
            return False
        if isinstance(node, dict) and key in node:
            return False  # 键已存在：不重复插入
        header_ln = self._section_headers.get(section)
        if header_ln is not None:
            # 段末尾：最后一个直属于该段的键行之后（无键则段头之后）
            last = header_ln
            for tup, ln in self._key_lines.items():
                if tup[:-1] == section and ln > last:
                    last = ln
            header = self._lines[header_ln]
            indent = header[:len(header) - len(header.lstrip())]
            self._lines.insert(last + 1, indent + _quote(key) + ' = ' + serialized)
        else:
            if resolved:
                return False  # 段存在于 data 但无显式段头（dotted key 隐式表）→ 不插入
            # 新建段头：追加到文件末尾（前置空行分隔）
            if len(self._lines) and self._lines[-1].strip() != '':
                self._lines.append('')
            self._lines.append('[' + '.'.join(_quote(p) for p in section) + ']')
            self._lines.append(_quote(key) + ' = ' + serialized)
        self._rebuild_index()
        self._refresh_data()
        return True

    def delete(self, path):
        """删除键行（上方注释行保留）；键不存在返回 False。"""
        tup = self._resolve_key(path)
        if tup is None:
            return False
        del self._lines[self._key_lines[tup]]
        self._rebuild_index()
        self._refresh_data()
        return True

    # ---- 差异 / 还原 -------------------------------------------------
    def diff(self):
        """相对 __init__ 快照的差异列表 [(path, old_value, new_value), ...]。

        - 修改：old=快照值, new=当前值；
        - 删除：new=None；新增：old=None。
        按文件出现顺序输出。
        """
        self._refresh_data()
        out = []
        for tup in sorted(self._snap_line_no, key=self._snap_line_no.get):
            old = self._snap_values.get(tup, _MISSING)
            new = self._walk(self._data, tup, _MISSING)
            if new is _MISSING:
                out.append(('.'.join(tup), None if old is _MISSING else old, None))
            elif old is _MISSING or old != new:
                out.append(('.'.join(tup), None if old is _MISSING else old, new))
        for tup in sorted((t for t in self._key_lines if t not in self._snap_line_no),
                          key=self._key_lines.get):
            new = self._walk(self._data, tup, _MISSING)
            if new is not _MISSING:
                out.append(('.'.join(tup), None, new))
        return out

    def restore(self, path=None):
        """还原单键（path）或全部（None）到 __init__ 快照；无改动返回 False。"""
        if path is None:
            if not self.is_dirty():
                return False
            self._lines = list(self._orig_lines)
            self._rebuild_index()
            self._refresh_data()
            return True
        tup = self._match_snap(path)
        if tup is not None:
            orig = self._snap_line_text[tup]
            ln = self._key_lines.get(tup)
            if ln is not None:
                if self._lines[ln] == orig:
                    return False
                self._lines[ln] = orig
            else:
                # 已删除 → 插回快照位置
                self._lines.insert(min(self._snap_line_no[tup], len(self._lines)), orig)
            self._rebuild_index()
            self._refresh_data()
            return True
        # 新增键（快照中不存在）→ 还原即删除该键行
        tup2 = self._resolve_key(path)
        if tup2 is not None:
            del self._lines[self._key_lines[tup2]]
            self._rebuild_index()
            self._refresh_data()
            return True
        return False

    def is_dirty(self):
        """相对 __init__ 快照是否有改动（含增删行）。"""
        return self._lines != self._orig_lines

    # ---- 保存 --------------------------------------------------------
    def save(self, path=None):
        """写回（UTF-8，沿用源文件换行符），原子替换；写后 tomllib 校验。

        校验失败抛 ValueError 且目标文件保持原状（临时文件在 replace 前
        校验，等价于最强的回滚）；内存中的行保留以便修复后重试。
        """
        target = os.path.abspath(path or self._path)
        content = self._newline.join(self._lines)
        if self._ends_newline:
            content += self._newline
        payload = (b'\xef\xbb\xbf' if self._bom else b'') + content.encode('utf-8')
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix='.toml_preserve_', suffix='.tmp',
                                       dir=os.path.dirname(target) or '.')
            with os.fdopen(fd, 'wb') as f:
                f.write(payload)
            try:
                with open(tmp, 'rb') as f:
                    tomllib.load(f)
            except _LOAD_ERROR as e:
                raise self._decode_error(e, '保存后 TOML 校验失败') from e
            os.replace(tmp, target)
            tmp = None
        finally:
            if tmp is not None and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

