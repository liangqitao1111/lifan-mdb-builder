# 理反 Web · 前后端 + GitHub Actions 端到端契约（V2）

> 本契约是后端 / 前端 / Actions 三方实现的准绳。改动任何一方必须同步本文件。

## 1. 端到端数据流

```
用户上传 .mdb/.lz → POST /api/upload
  后端保存原件 work/uploads/{db_id}.{ext}
  .lz 为 ZIP 包 → 解压取内嵌 MDB（找 *.mdb 条目，排除含"备份"的条目）
  读取（ACE 或 mdbtools）→ 导入 work/dbs/{db_id}.db（SQLite）
  同时保存原始字段类型 → work/dbs/{db_id}_schema.json（v2，含 mdb_type）
  返回 {db_id, file, size, tables:{表:行数}}

网页浏览/编辑 → GET/POST/PUT/DELETE /api/db/{db_id}/table/{table}...
  编辑只作用于 SQLite 工作库

触发生成 → POST /api/db/{db_id}/build
  后端:
    a. 导出最新 schema.json（v2）到 work/dbs/{db_id}_schema.json
    b. github_trigger.trigger_build() 完成:
       - Contents API 上传 work.db+schema.json → 仓库 payload/{db_id}/
       - repository_dispatch 触发 build-mdb（payload: db_id/sqlite/schema/out）
       - 轮询 run 至完成
       - 成功 → 下载 artifact 缓存到 work/artifacts/{db_id}.lz
       - 写 work/dbs/{db_id}_build.json（status=success/failed, run_id, conclusion）
    c. 接口立即返回 {db_id, message, poll}

查询状态 → GET /api/db/{db_id}/build/status → 读 {db_id}_build.json
下载产物 → GET /api/db/{db_id}/artifact → 返回缓存的 .lz（真 ZIP 包）
下载工作库 → GET /api/db/{db_id}/download → work.db
```

## 2. API 清单（FastAPI，挂 /api 前缀）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | /api/upload | multipart file(.mdb/.lz/.accdb) | {db_id, file, size, tables} |
| GET | /api/db/{db_id}/tables | - | {db_id, tables:[..]} |
| GET | /api/db/{db_id}/table/{t} | page,page_size≤200,keyword,search_col | {columns, rows:[{..}], total, page, page_size} |
| POST | /api/db/{db_id}/table/{t} | {data:{列:值}} | {ok, row_id} |
| PUT | /api/db/{db_id}/table/{t}/{row_id} | {data:{列:值}} | {ok, row_id} |
| DELETE | /api/db/{db_id}/table/{t}/{row_id} | - | {ok} |
| POST | /api/db/{db_id}/build | - | {db_id, message, poll} |
| GET | /api/db/{db_id}/build/status | - | {status, run_id, conclusion, updated_at} |
| GET | /api/db/{db_id}/artifact | - | .lz 文件（ZIP） |
| GET | /api/db/{db_id}/download | - | work.db |
| GET | /api/health | - | {status, mdb_backend} |

## 3. schema.json v2 格式（唯一权威类型来源）

```json
{
  "version": 2,
  "db_id": "abc12345",
  "tables": {
    "z_g_TuCeng": {
      "columns": [
        {"name": "ZKBH", "sqlite_type": "TEXT", "mdb_type": "TEXT(50)", "primary_key": false},
        {"name": "id",   "sqlite_type": "INTEGER", "mdb_type": "LONG", "injected_id": true}
      ]
    }
  }
}
```

- `mdb_type`：**从原始 .mdb 读取的字段类型**（ACE 用 SQL 类型映射；mdbtools 解析 mdb-schema），禁止从数据值推断。
- `injected_id: true`：db.import_mdb 为无 id 列的表注入的自增列，**重建 .mdb 时跳过此列**；verify 行数比对时 SQLite 侧同样排除。
- 字段类型映射（Access）：
  - INTEGER → LONG；REAL → DOUBLE；TEXT 长度≤255 → TEXT(n)；更长 → MEMO
  - 二进制（bytearray）→ LONGBINARY
  - 日期 → DATETIME；布尔 → BIT

## 4. GitHub Actions 契约（build-mdb.yml）

- 触发：`repository_dispatch` types=[build-mdb]，payload:
  ```json
  {"event_type":"build-mdb","client_payload":{"db_id":"abc","sqlite":"payload/abc/work.db","schema":"payload/abc/schema.json","out":"payload/abc/output.lz"}}
  ```
- 步骤：checkout → setup-python 3.11 → 装 ACE x64（下载 accessdatabaseengine_X64.exe /quiet）→ pip install pyodbc pypyodbc pywin32 → 运行 `python backend/sqlite_to_mdb.py --sqlite <rel> --schema <rel> --out <rel>` → `python backend/verify_mdb.py --mdb <rel> --sqlite <rel>` → upload-artifact（name=lizheng_review_mdb，保留 30 天）
- 产物是**真 .lz（ZIP）**，内部结构模仿理正：
  ```
  {工程名}/database{时间戳}/LZGICAD1.mdb
  {工程名}/ProjectInfo.ini
  ```
  ProjectInfo.ini 模板（GBK 编码写入）：
  ```ini
  [GCInfo]
  GCSY=27
  GCBH={工程名}
  GCMC={工程名}
  GCPATH=\Files{时间戳}\{工程名}
  [DataBase]
  CURMDB=LZGICAD1.mdb
  CURDB=database{时间戳}
  [Files]
  CURFILE=Files{时间戳}
  ```
  工程名用 ASCII（如 lifan_{db_id}），ZIP 条目名用 UTF-8。

## 5. 密码库支持

- 上传时读取顺序：无密码打开 → 失败用理正库密码 `%2.3#5B.@8`（PWD= 连接串）重试。
- 重建的新 .mdb **无密码**（理正可正常打开，密码只影响 .lz 包内 A 类库）。

## 6. 前端接线要求（index.html）

- 新增 API 客户端（fetch 封装，含错误提示），页面接入：
  - 数据库接入页：选择 .mdb/.lz → 上传 → 显示表清单/行数
  - 数据管理页：选库选表 → 分页浏览 → 行编辑（新增/修改/删除）
  - 导出下载页：触发构建 → 轮询 /build/status → 成功后下载 .lz 产物 + work.db
- 保持现有 IndexedDB 本地功能可用（未上传时可继续本地演示）；上传后进入"在线库模式"。
- 接口地址：`/api` 相对路径（同源部署）；本地 file:// 打开时自动回退纯前端模式。

## 7. 安全

- 所有 GitHub 交互 token 只存在于服务端环境变量 GH_TOKEN；前端不接触。
- artifact 按 db_id 隔离；未构建成功不提供下载。
- 上传/产物文件命名均用 uuid db_id，避免路径穿越。
