# 理反 Web · 交接文档（HANDOVER）

> 生成时间：2026-08-12 · 最新提交：`6ef6744`（main）· 分支：main
> 服务地址：http://127.0.0.1:8766/（本地已重启为新代码，200 OK）

---

## 1. 项目概览

**项目**：理反 Web — 将桌面版「理反 V3.0.4 理正地层复核工具」网页化。
**定位**：项目特色软件，围绕理正勘察数据库（.mdb/.lz）做针对性的复核/编辑/统计/导出，修复 bug 不得丧失特色优化，页面以**桌面版为口径**（列顺序/列名/完整性一致）。

| 项 | 值 |
|---|---|
| 仓库 | `C:\Users\神舟\WorkBuddy\2026-08-12-01-18-03\lifan_web` |
| 远端 | https://github.com/liangqitao1111/lifan-mdb-builder.git（main） |
| 技术栈 | 单文件前端 `index.html`（原生 JS，约 2400+ 行）+ FastAPI 后端（`backend/`，SQLite 工作库） |
| 账号 | admin / admin（全部 API 需 Bearer token） |
| 测试库 | `work/dbs/e2e_real.lz`（85 表 / 408 孔 / 土工 616 行） |

## 2. 运行方式

```powershell
# 一键启动（README/start.bat 同款，app-dir 必须指向 backend）
cd C:\Users\神舟\WorkBuddy\2026-08-12-01-18-03\lifan_web
python -m uvicorn main:app --host 127.0.0.1 --port 8766 --app-dir backend
# 浏览器打开 http://127.0.0.1:8766/ ，admin/admin 登录
```

**重要**：main.py 在 `backend/` 目录，启动必须带 `--app-dir backend`（README 里有，start.bat 也有）。改过后端代码必须重启服务（无热重载）。

## 3. 版本历史（main）

| 提交 | 内容 |
|---|---|
| `6ef6744` | **本轮**：数据表交互增强 — 表头排序(列名白名单)/列筛选/冻结前2列(sticky对齐padding)/长文本2行截断+hover全文/键盘导航/行高52px统一/CY状态色标签 |
| `5fbb3b7` | 数据工作台人性化排版 — 操作列固定90px不跨行/描述列后置/土工试验列补全17列/sticky表头+数字右对齐+斑马纹+空值占位 |
| `2e4529a` | 地层页排版/行内编辑 + 参数中心状态定义数据 + 下载鉴权 401 |
| `ac2463d` | P3 体验质量 — 深色模式/移动端/列显隐/E2E 固化/模块同步 |
| `e2cf6d0` | P2 部署运维 — 一键启动/Docker/Caddy/README 运维文档 |
| `62694e4` | P1 功能补齐 — 标贯一键应用/动探修正/问题导出/表头映射/统计一致性 |
| `ea2ed26` | P0 安全 — 后端 Token 鉴权 + 操作审计日志 |
| `fdd4c1c` | 工作台数据体验全面优化 + 参数中心 Web 化（保留注释）+ 选孔精确过滤 |

## 4. 本轮功能清单（6ef6744 + 5fbb3b7）

### 4.1 表格排版（5fbb3b7）
- 操作列固定 90px（head/body 共用 `gridColsReal` 模板，不跨行）
- 地层列序：层号/岩土名称/层底深度/颜色/密实度/湿度/可塑性/风化程度/层厚/**描述(末位)**/操作
- 土工试验列 6 → **17 列**：取样编号/深度/含水率/液限/塑限/**干密度/最大干密度/初始孔隙比/颗分6段**/综合定名（`COLUMN_CN` 已补中文名）
- sticky 表头 / 数字列右对齐 + tabular-nums / 斑马纹 / 空值占位 `—` / 行 hover

### 4.2 交互增强（6ef6744）
| 功能 | 说明 |
|---|---|
| 表头排序 | 点击列头循环 升序→降序→清除；后端 `order_by/order_dir`，**列名白名单校验**（防 SQL 注入） |
| 列筛选 | 表头 hover 出 ⌕ 图标 → 弹窗模糊筛选该列；与排序可叠加；筛选状态同步搜索框 |
| 冻结前 2 列 | `#`(left:20px) + 第一数据列(left:80px) sticky，滚动位置恒定无跳动 |
| 长文本截断 | 描述等宽列 2 行 `cell-clamp` + hover title 全文，行高统一 52px |
| 键盘导航 | ↑/↓ 高亮行、Enter 行内编辑、Esc 取消；输入框内不触发 |
| 行高统一 | 13px / 52px（移除 inline 覆盖，回归 CSS 类） |
| 状态色 | 水位表 CY 列渲染 参与/不参与 status-pill |

## 5. 验证体系

| 脚本 | 内容 | 状态 |
|---|---|---|
| `work/e2e_ui5.mjs` | 本轮交互增强 24 项（排序/筛选/冻结/键盘/CY/下载） | ALL PASS |
| `work/e2e_ui4.mjs` | 排版专项 18 项（列序/土工17列/sticky/下载） | ALL PASS |
| `tests/e2e_ui.mjs` | 固化 UI 基线 12 项（登录/统计/深色/列设置/导出） | ALL PASS |
| `tests/e2e_api.py` | API 回归 **16 项**（含新增 `test_table_sort_order` 排序+注入用例） | 16 passed |

运行：`node work/e2e_uiX.mjs` / `node tests/e2e_ui.mjs` / `python -m pytest tests/e2e_api.py -q`

## 6. 已知陷阱（重要，改代码前必读）

1. **inlineEdit 拼参**：`onclick="inlineEdit(this, ' + idJs + ', \'' + c + '\')"` 用**单引号包裹列名**，不要改回 `JSON.stringify(c)`（曾导致 HTML 双引号断裂）。
2. **表头断言用 innerText**：表头含隐藏图标（⇅/⌕，`display:none`），`textContent` 会包含图标字符导致断言失败——测试脚本已统一用 `innerText`。
3. **筛选与搜索框同步**：`loadOnlinePage` 会用 `#onlineSearch` 值覆盖 `OData.keyword`；`filterApply` 必须同步 `si.value`（已修复），否则列筛选被孔号覆盖。
4. **冻结列 left 偏移**：t-row 有 `padding:0 20px`，sticky 单元格必须 `left:20px`（#列）/ `left:80px`（第一数据列），否则滚动时跳动 20px。
5. **下载鉴权**：`<a href download>` 直链会 401，必须走 `downloadApiFile`/`dlStats`/`dlDxf`（fetch+blob 带 Bearer）。
6. **登录等待**：登录后必须等 `localStorage.getItem('lifan_token')` 存在再操作；上传后等 `wsS0` 统计渲染。
7. **土工数据真相**：测试库 616 行中干密度/最大干密度/初始孔隙比/颗分 6 段**源 MDB 本身为空（0/616）**，含水率/液限/塑限有 200 条——不是前端缺失；列已全部就位，源库有值即显示。
8. **保留注释**：参数中心采用 TomlFile 补丁模式，`参数/工程配置.toml` 恢复为桌面版基线，**不要重写整个文件**。
9. **列名去重**：TCMC/TCYMC 同义列只显示 TCMC（去重逻辑在 `renderOnlineRows` 内，勿删）。
10. **push 重试**：GitHub 443 不稳定，用循环重试（`1..10 | ForEach-Object { git push origin main; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 15 }`）。

## 7. 核心文件地图

| 文件 | 说明 |
|---|---|
| `index.html` | 前端主体：路由/登录/数据工作台/复核/参数中心/工具页 |
| `backend/main.py` | FastAPI 入口：鉴权/上传/表 CRUD（含排序参数）/复核/统计/DXF |
| `backend/db.py` | SQLite 工作库：`get_rows`（分页/搜索/**排序白名单**）/upsert/delete |
| `backend/review_api.py` | 复核/统计/参数中心 API |
| `backend/auth.py` | Bearer Token 鉴权 |
| `参数/工程配置.toml` | 桌面版基线配置（只读口径，补丁式修改） |
| `tests/` | pytest API 回归 + 固化 UI 基线 |
| `work/` | 迭代脚本/专项 E2E/诊断脚本（可清理，`e2e_ui4/5.mjs` 建议保留） |

## 8. 待办 / 建议（下一步可做）

1. 用真实项目 MDB 复测土工颗分/干密度导入映射（若真实库有值但导入后为空，需查 `backend/mdb_reader.py` 列映射）。
2. 列宽自适应：描述列 max-width + hover 展开（已截断，可进一步做点击展开全文）。
3. 移动端适配复核：冻结列 + 键盘导航在窄屏下的表现。
4. 排序/筛选状态持久化（可选：localStorage 记住用户偏好）。
5. `screenshots/ui5_*.png` 为本轮截图（未提交，可留作视觉验收参考）。

---
*交接完毕：代码已提交推送（main = 6ef6744），本地服务已运行新代码。*
