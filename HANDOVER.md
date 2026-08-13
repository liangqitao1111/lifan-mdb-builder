# 理反 Web · 交接文档（HANDOVER）

> 更新：2026-08-14 05:00 · 服务地址：http://127.0.0.1:8766/
> 最新提交：见下方 git 历史（main，v39 已含 v1~v38）· 工作区无未提交代码改动
> 代码规模：`index.html` 2749 行（v31 清理后）/ 后端 FastAPI

---

## 1. 项目概览

**项目**：理反 Web — 将桌面版「理反 V3.0.4 理正地层复核工具」网页化。
**定位**：围绕理正勘察数据库（.mdb/.lz）做复核/编辑/统计/导出，页面以**桌面版为口径**（列顺序/列名/完整性一致），修复 bug 不得丧失特色优化。

| 项 | 值 |
|---|---|
| 仓库 | `C:\Users\神舟\WorkBuddy\2026-08-12-01-18-03\lifan_web` |
| 远端 | https://github.com/liangqitao1111/lifan-mdb-builder.git（main） |
| 技术栈 | 单文件前端 `index.html`（原生 JS）+ FastAPI 后端（`backend/`，SQLite 工作库） |
| 账号 | admin / admin（全部 API 需 Bearer token） |
| 测试库 | `work/dbs/e2e_real.lz`（85 表 / 408 孔 / 土工 616 行 / 颗分 z_c_KeFen 214 行） |
| 桌面入口 | `C:\Users\神舟\Desktop\理反Web 一键启动.bat` |

## 2. 运行方式

```powershell
# ⚠️ 必须用 envs/default 环境（托管裸解释器 versions\3.13.12 没有 uvicorn！）
cd C:\Users\神舟\WorkBuddy\2026-08-12-01-18-03\lifan_web
"C:\Users\神舟\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8766 --app-dir backend
# 浏览器打开 http://127.0.0.1:8766/ ，admin/admin 登录
```

- main.py 在 `backend/` 目录，启动必须带 `--app-dir backend`。
- **改过后端代码必须重启**（uvicorn 无热重载）；前端 `index.html` 是静态文件，刷新即生效。
- 健康检查：`curl http://127.0.0.1:8766/api/health` → `{"status":"ok","mdb_backend":"ace"}`。
- 桌面双击 `理反Web 一键启动.bat` = 启动服务 + 自动打开浏览器（start.bat 已修：envs/default + 8766 端口）。

## 3. 版本历史（8/12 基础版 → 8/13 v1~v32）

### Git 提交（8/12，基础功能）
| 提交 | 内容 |
|---|---|
| `b120f35` | 交接文档 HANDOVER（首版） |
| `6ef6744` | 数据表交互增强 — 表头排序(白名单)/列筛选/冻结前2列/长文本截断/键盘导航/行高52px/CY状态色 |
| `5fbb3b7` | 人性化排版 — 操作列固定90px/描述列后置/土工17列/sticky表头+右对齐+斑马纹 |
| `2e4529a` | 地层页排版/行内编辑 + 参数中心状态定义 + 下载鉴权 401 |
| `ac2463d` | P3 体验 — 深色模式/移动端/列显隐/E2E 固化 |
| `e2cf6d0` | P2 部署 — 一键启动/Docker/Caddy/README |
| `62694e4` | P1 功能 — 标贯一键应用/动探修正/问题导出/表头映射 |
| `ea2ed26` | P0 安全 — Token 鉴权 + 操作审计日志 |

### 迭代版本（8/12 晚 ~ 8/13，**尚未提交**，见 §9）
| 版本 | 内容 |
|---|---|
| **v1** | 地层页/整体 UI 优化：统计卡 hover 动效+语义色圆点、三栏 270/1fr/340、深色模式表格 hover 适配、Cmd/Ctrl+K 聚焦搜索 + Esc 清空、skeleton 骨架屏、seg-tabs 分段控件、toast.info、btn.loading、CSS 设计令牌（--space/--fs/--radius/--shadow/--ease） |
| **v2** | 冻结列默认关闭（列随滚动）+ 列设置加"冻结前两列"开关；表头/数据统一居中 |
| **v3** | 6 项：地层序号单孔化（TCXH）；列宽按类型 COL_WIDTH 表；操作列去"编辑"（双击行内编辑）；干密度/孔隙比空值诊断（源库空，非 bug）；水位 CY 改下拉选择栏（cyChange 保存 1/0）；层号显示主-亚（TCZCBH-TCYCBH） |
| **v4** | 地层序号按单孔层位埋深排序（ZKBH + TCCDSD 升序，# 列孔内 1..N 重算，空值沉底） |
| **v5** | 三栏瘦身 190/1fr/270 + 土工颗分独立页签（伪表 `z_c_QuYang__kf`）+ 表头换行 + **伪表机制 `wsRealTable()`**（解析 `__` 前缀，6 处 API URL 兼容） |
| **v6** | 弹性列宽 `minmax(min,1fr)`（窄屏不滚动/宽屏拉伸）+ 描述/定名 2fr + 操作列固定 50px + 复核问题列 270→240；修复 `pxpx` 模板 bug |
| **v7** | 左侧 sidebar 可折叠：默认 56px 只显图标（main 多 184px）、hover 临时展开、click 持久展开、localStorage 记忆 |
| **v8** | 属性列固定紧凑 + 描述列独占剩余（描述 `minmax(156,1fr)`，属性列不参与弹性） |
| **v9** | 层厚列（TCHD）默认隐藏（列设置可恢复）+ 描述列封顶 `minmax(156,260px)` |
| **v10** | 修复单孔复核问题不响应「土工判别」开关——`wsSelectHole` 裸路径 → `wsReviewHoleQuery(zk)`（带全部参数） |
| **v11** | 开关组件双圆点 bug：旧 `.switch::after` 限定 `.switch-row .switch::after`（仅备份页 div 结构） |
| **v12** | **所有页签单孔维度**（WS.zkbh 主控，wsSetTab 强制 ZKBH 精确筛选）+ 钻孔列表单选修复（data-zk 精确匹配，修复 ZK-1/ZK-10 前缀误高亮） |
| **v13** | 所有页签序号单孔化（新增 DEPTH_COL 表）+ 颗分链路诊断（当时误判数据缺失） |
| **v14** | 修复「切换库」不跳上传页：`clearApiState();location.hash='#workspace';renderWorkspace()` |
| **v15** | 钻孔列表展示全部孔（去掉 `clean.slice(0,40)` 截断） |
| **v16** | **颗分映射修复（重要）**：真实颗分在独立表 **`z_c_KeFen`**（214 行真实数据），不在 z_c_QuYang 的 QYZY 列（源库空）。颗分 tab 从伪表改指真表；g_TableName 有中文表名索引 |
| **v17** | 清理 v3~v16 版本注释 + 钻孔列表自然序（去 risky/clean 分段，全部孔 `localeCompare(numeric)` 一次排序，风险用徽标标注） |
| **v18** | **问题卡片→表格行定位高亮**：后端 `_issue_to_dict` 透传 `layer_index/ref_value`；前端卡片加 data-idx + `jumpToIssue()`（await 加载 + 轮询 + scrollIntoView + 闪烁动画 rowFlashAnim，桌面/移动端） |
| **v19** | 取消复核页（工作台）搜索框（与顶部 globalSearch 重复）+ 钻孔列表「仅问题孔」开关 + KPI 卡点击跳转（wsKpiJump） |
| **v20** | KPI 4→3 卡去重（删"高风险问题"卡，H/M 细分并入 sub）+ 钻孔列表 UI（渐变 active/风险色条/圆角）+ 列设置弹窗过滤系统列与 `_` 标志位列、中文映射全覆盖 |
| **v21** | 标贯修正深度浮点 `fmtNum` + 钻孔列表防跨行拥挤（ellipsis+nowrap）+ **问题卡片按规则类型跳页签**（RULE_TAB 映射 + ref_value 定位，标贯→z_y_BiaoGuan 等） |
| **v22** | 表头不居中修复：`.t-head .col-sort` 加 `justify-content:center`（flex:1 子项无视父容器 justify-content） |
| **v23** | 标贯修正击数标注：后端 `scan_all` **激活 compute_suggestions**（此前从未调用，new_n 恒等 old_n）+ 分类处理（R-DEN-007 可塑性走 compute_clay_corrections N 不变）；前端"原始击数→修正击数"标注 |
| **v24** | 「修正击数」改「**修改后击数**」+ 双击编辑（sptEditN）；**跨层标贯规则 R-SPT-001**（标贯深度不在任何地层 (层顶,层底] 区间→H 级，优先级高于击数规则，单测 7 用例全过）；参数中心卡片化 |
| **v25** | 参数中心系统性排版（UI 专家）：设计系统 cfg-panel/cfg-sub/cfg-card/cfg-note/cfg-state-table，段色点 nth-child 轮换、计数徽标、响应式 720px 单列 |
| **v26** | 参数中心「合理填满不留空」：auto-fill→**auto-fit** + 段网格 2 列 minmax(520px) + 字段 3 列 minmax(200px)（段/字段最小宽需协同设计） |
| **v27** | 用途字段只读（disabled + saveConfig 跳过）+ 卡片布局灵活（默认横排，textarea 自动 .col 竖向）+ 注释清理 |
| **v28** | 段网格改**瀑布流 `columns:2`**（根治 grid 行高留白，break-inside:avoid + margin-bottom 14px） |
| **v29** | 岩溶统计按项目类型自动选择（`WS_SETTINGS.project_type` 决定 karst_a/karst_b，不再并列） |
| **v30** | 全局注释专业化清理（19 处版本号注释，保留简洁模块说明） |
| **v31** | 移除**全部注释** + 删除 215 行视觉粗糙 dead code（renderReview/renderBorehole/renderSpt 假数据页，route 归一化后不可达）；3023→2759 行 |
| **v32** | 生成 MDB 页删除教学性文字（顶部副标题/4 步流程指示器/日志 API 路径说明），保留核心按钮与状态 |
| **v33** | **可塑性修正闭环**：R-DEN-007 建议前端展示「可塑性 流塑→软塑（状态修正）」+ 一键应用写回地层 TCKSX（后端 spt-apply 定位深度所在层，新增 s_updated 计数）；列设置重名修复：TCYMC 显示「岩土名称（TCYMC）」默认不勾选 |
| **v34** | **全量复核修复（6 项）**：① db.py search_col 列白名单（防 SQL 注入面）；② 参数中心状态映射 key 编辑真实生效（updates+deletes 原子替换，此前静默失效）；③ logout API 真实吊销 token（此前 placeholder 无效）；④ toolsIssues/toolsSpt 传 WS_SETTINGS 复核参数（此前默认 B/硬塑 与设置脱节）；⑤ 土工统计 project_type 跟随设置（此前固定 A）；⑥ globalSearch 恢复绑定（Enter 定位钻孔 + Ctrl+K/Esc 修复） |
| **v35** | 参数中心移除冗余装饰点（字段•/子段横条/段标题色点），回归干净文字层级 |
| **v36** | DXF 参数移入所属页面（纵断面/柱状图卡片内嵌参数设置，保存即生效）；生成时运行时读 TOML；API 默认值不覆盖配置 |
| **v37** | 桌面版 vs Web 全量功能一致性验证（8 项 IDENTICAL，报告 docs/桌面一致性对比报告.md）；补 ezdxf 依赖 |
| **v40** | **数据源/前端复核**：①mdb_reader CSV 解析支持转义引号（Linux mdbtools 后端）；②数据源层审查通过（ACE 密码重试/类型码映射/SQLTables 枚举）；③config.py 区间/条件解析层审查通过（≤/≥/~ /IL键/>=优先）；④前端联动审查通过（wsLoadReview/wsSaveSettings 保存即重复核/wsKpiJump/上传流程）；⑤清理 renderDataOnline 死代码 62 行 |
| **v39** | **规则/数据层复核**：①R-CRS-012 密实度空隙不再跳过同条标贯风化对照（防御性）；②清理 renderDataOnline 死代码 62 行；③规则函数全量审查（R-PLS/R-CRS/R-GRS/R-DEN-010 正确）；④dao/sqlite_dao 字段映射与 SQL 适配审查通过（表名自动发现/TOP→LIMIT 翻译/AttrRow 大小写不敏感/每请求新建连接无跨线程问题）；⑤问题导出内容抽查（148 行=147 问题+表头、H/M 计数一致、自然排序） |
| **v38** | **参数逐项复核**：①A类标贯N_可塑性区间空隙误判修复（浮点修正击数落入整数边界空隙→None 跳过，对齐 density 口径，测试库 119 条潜在误报消除；B类/风化无空隙不受影响）；②补动探杆长修正 UI（桌面版有 Web 缺失，GB50021 重型修正全链路验证 DTXZJS=14）；③参数中心死参数标注 10 处（CAD复核/手动修正范围4段/洞高标签/埋深标签/有效填充物 标"未使用"防误改）；④复核设置 4 参数组合验证（include_test/use_std_stratum/max_plasticity/project_type 全部正确生效）；⑤边界测试（page 负数/超大 page_size 钳制 200/通配符%） |

## 4. 当前功能清单（v32 状态）

### 4.1 数据工作台
- **KPI 3 卡**：总孔数 / 风险钻孔（sub: H 级孔 x · M 级孔 y）/ 问题总数（sub: H 级 x · M 级 y），点击跳转对应问题孔（wsKpiJump）
- **钻孔列表**：全部孔自然序（localeCompare numeric）、单选（data-zk 精确）、「仅问题孔」过滤开关、H 红条/M 黄条/徽标、hover 右滑、active 渐变
- **6 个页签**：地层 / 土工试验 / **颗分试验（z_c_KeFen）** / 动探 / 标贯 / 水位，**全部单孔维度**（WS.zkbh 联动）
- 表格：# 列孔内序号（1..N 跨孔重置）、层号主-亚、列数据居中、属性列紧凑+描述弹性（封顶 260）、操作列固定 46px、无横向滚动（1440）
- 行内编辑：双击/点击单元格 → input 保存（inlineEdit）；水位 CY 下拉；列设置（过滤系统列/标志位列，中文映射全覆盖）
- 搜索：顶部 globalSearch；表格搜索框已删（v19）；搜索 Enter 全局、清空/Esc 恢复单孔

### 4.2 复核问题面板
- 右侧问题卡片：H/M 等级底色、rule_id + message + field
- **点击卡片 → 自动跳对应页签**（RULE_TAB 映射）+ 定位行（地层按 layer_index，标贯/动探按 ref_value 深度容差 0.011，土工按 QYBH 精确）+ 闪烁高亮（1.2s×2 次 → 停留 → 自动消退，再点重触发）
- 单孔查询走 `wsReviewHoleQuery(zk)`（带 project_type/max_plasticity/use_std_stratum/include_test）

### 4.3 修正与成果（tools）
- 标贯批量修正：`toolsSpt` 扫描 → 列表显示「原始击数 X → 修改后击数 Y（双击可改）」→ `toolsSptApply` 一键写回；可塑性条目（R-DEN-007）显示「击数 N=X（无需修正）」
- 岩溶统计：**按项目类型动态 1 卡**（A 类→karst_a / B 类→karst_b）

### 4.4 参数中心
- 16 段卡片瀑布流（columns:2，零留白）、段色点（公用蓝/A紫/B绿）、计数徽标
- 字段卡片横排（label 左/控件右 中心对齐）、用途/说明**只读蓝底跨整行**、判定表（cfg-state-table）行式
- 保存：saveConfig 补丁式写 TOML（自动 .bak），disabled 元素跳过

### 4.5 生成 MDB
- 触发生成 → GitHub Actions 转换 → 下载 .lz 产物 / work.db（v32 已删教学性文字，保留状态横幅/任务卡/日志）

### 4.6 全局
- sidebar 可折叠（56px/hover/click/localStorage）；深色模式；Cmd+K / Esc 快捷键；登录 admin/admin

## 5. 关键机制（改代码前必读）

| 机制 | 说明 |
|---|---|
| **单孔主控 WS.zkbh** | 所有页签 + 复核问题面板联动主控；wsSetTab 强制 `keyword=WS.zkbh&search_col=ZKBH&exact=true`；统计卡保持库级概览 |
| **伪表 wsRealTable(t)** | 解析 `__` 前缀 → 真实表名；loadOnlinePage/inlineEdit/cyChange/增删改 6 处 API URL 全部经它（当前仅剩颗分真表 z_c_KeFen，伪表机制保留） |
| **DEPTH_COL** | 各表深度排序列（地层 TCCDSD/土工 QYSD/标贯 BGDSD/动探 DTDSD/水位 SWSD），所有页签默认按 ZKBH+深度排序 |
| **RULE_TAB** | 规则 ID → 页签映射：标贯 R-DEN-001/007/008、R-CRS-012、**R-SPT-001**；动探 R-DEN-002；土工 R-PLS-001/002、R-CRS-002/003、R-GRS-001；其余地层 |
| **jumpToIssue** | 问题卡片定位：`await loadOnlinePage` 后再找行（否则旧行 flash 被重建 DOM 清掉）+ 轮询 2.5s + scrollIntoView(smooth,center) + flash 动画 |
| **WS_SETTINGS** | 复核设置（project_type A/B、max_plasticity、use_std_stratum、include_test），localStorage 持久化，全站共用 |
| **COL_WIDTH / WS_COLS / COLUMN_CN** | 列宽表 / 每页签业务列 / 中文映射（35+ 列全覆盖）；列设置优先用 WS_COLS 过滤 |
| **颗分数据位置** | 颗分在 **z_c_KeFen**（KL 筛孔列），**不在** z_c_QuYang 的 QYZY 列（源库空壳）；带 `_` 后缀列=“是否有该试验”标志位 |

## 6. 验证体系

| 脚本 | 内容 | 说明 |
|---|---|---|
| `work/e2e_ui5.mjs` | 交互增强 26 项 | 已同步 v2 冻结开关断言（默认不冻结+开关开启冻结）、v3 CY 下拉断言 |
| `work/e2e_v33.mjs` | v33 专项 9 项（可塑性展示/写回/列设置重名） | ALL PASS |
| `work/e2e_v34.mjs` | v34 专项 8 项（注入防护/logout吊销/配置编辑/参数透传/全局搜索） | ALL PASS |
| `work/e2e_ui4.mjs` | 排版专项 | 已同步 v5 土工核心列、v16 颗分 z_c_KeFen |
| `tests/e2e_ui.mjs` | UI 基线 12 项 | 未动 |
| `tests/e2e_api.py` | API 回归 **17 项**（v33 可塑性写回用例） | 17 passed |

运行：`node work/e2e_uiX.mjs` / `node tests/e2e_ui.mjs` / `python -m pytest tests/e2e_api.py -q`
**注意**：v18 后 E2E 断言后端 `_issue_to_dict` 新增了 `layer_index/ref_value` 字段（纯新增，不破坏既有断言）；v24 新增 R-SPT-001 规则（测试库无跨层数据 → 0 条）。

## 7. 已知陷阱（改代码前必读）

1. **启动环境**：托管裸解释器 `versions\3.13.12\python.exe` **没有 uvicorn**，必须用 `binaries\python\envs\default\Scripts\python.exe`（uvicorn 0.52.1 + fastapi 0.141.1）。
2. **inlineEdit 拼参**：`onclick="inlineEdit(this, ' + idJs + ', \'' + c + '\')"` 用**单引号包裹列名**，不要改回 `JSON.stringify(c)`（曾致 HTML 双引号断裂）。
3. **loadOnlinePage 覆盖 keyword**：会用 `#onlineSearch` 值覆盖 `OData.keyword`——恢复单孔必须**同步设 `si.value = WS.zkbh`**（v12 踩过）。
4. **flex:1 子项无视父容器 justify-content**：居中必须在子项自身做（`.col-sort` justify-content:center，v22）。
5. **下载鉴权**：`<a href download>` 直链会 401，必须走 `downloadApiFile`/`dlStats`/`dlDxf`（fetch+blob 带 Bearer）。
6. **数据表头断言用 innerText**：表头含隐藏图标（⇅/⌕），`textContent` 含图标字符致断言失败。
7. **颗分/干密度空值**：测试库 z_c_QuYang 的 QYZXMD/QYSTXS/QYZY* 源库为空（0/616）是**正常现象**（真实数据在 z_c_KeFen / 其他列）；显示"—"非 bug。
8. **参数中心 TOML**：TomlFile 补丁模式，`参数/工程配置.toml` 不要重写整个文件（保存自动 .bak）。
9. **列名去重**：TCMC/TCYMC 同义列只显示 TCMC（去重逻辑在 renderOnlineRows 内，勿删）。
10. **grid 模板单位**：`minmax(52px,1fr)` 拼字符串时别漏写/重复 `px`（v6 出现过 `pxpx` 使整个 grid 失效）。
11. **CSS columns 瀑布流**：段卡必须 `break-inside:avoid`；响应式 @media 720px 切 `columns:1`。
12. **jumpToIssue 时序**：必须先 `await loadOnlinePage` 再查行，否则旧 DOM 的 flash 被重建清掉（v18 修复）。
13. **push 重试**：GitHub 443 不稳定，用循环重试（`1..10 | ForEach-Object { git push origin main; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 15 }`）。

## 8. 核心文件地图

| 文件 | 说明 |
|---|---|
| `index.html` | 前端主体（2749 行）：路由/登录/数据工作台/复核/参数中心/工具页/生成 MDB；**所有 UI 改动集中在此** |
| `backend/main.py` | FastAPI 入口：鉴权/上传/表 CRUD（排序白名单）/复核/统计/DXF |
| `backend/db.py` | SQLite 工作库：get_rows（分页/搜索/排序白名单）/upsert/delete/import_mdb（原样拷贝无映射） |
| `backend/review_api.py` | 复核/统计/参数中心 API；`_issue_to_dict` 已透传 layer_index/ref_value（v18） |
| `backend/review/rule_engine.py` | 复核规则引擎：review_strata 先按深度排序后遍历（layer_index 为排序后索引）；含 R-SPT-001 跨层规则（v24） |
| `backend/review/spt_corrector.py` | 标贯修正：scan_all 已激活 compute_suggestions + compute_clay_corrections 分类处理（v23/v24） |
| `backend/auth.py` | Bearer Token 鉴权 |
| `参数/工程配置.toml` | 桌面版基线配置（只读口径，补丁式修改；含 试验指标_统计.N 范围供标贯钳位） |
| `start.bat` | 一键启动（envs/default + 8766 + 自动开浏览器） |
| `work/dbs/e2e_real.lz` | 测试库（408 孔） |
| `work/` | 迭代/E2E/诊断脚本（e2e_ui4/5.mjs 建议保留） |
| `.workbuddy/` | 截图/脚本/备份（backup_index_v30.html 为 v31 清理前备份）/memory 日志（已 gitignore） |

## 9. 当前未提交改动（重要！）

`git status` 显示 5 个文件**未提交**，本次交接前应 commit + push：

| 文件 | 改动 |
|---|---|
| `index.html` | v1~v32 全部前端改动（UI/交互/单孔联动/问题跳转/参数中心等） |
| `backend/review_api.py` | `_issue_to_dict` 透传 layer_index/ref_value（v18） |
| `backend/review/rule_engine.py` | R-SPT-001 跨层标贯规则（v24） |
| `backend/review/spt_corrector.py` | scan_all 激活 compute_suggestions + 分类处理（v23/v24） |
| `start.bat` | 环境修正（envs/default + 8766）+ 自动开浏览器 |

提交建议：`git add -A && git commit -m "feat: 数据工作台单孔联动/问题卡片跳转高亮/标贯修正激活/颗分映射修复/参数中心设计系统 + UI 全面优化（v1-v32）"`，再按陷阱 13 循环 push。

## 10. 待办 / 建议

1. ~~提交并推送未提交文件~~ ✅ 已提交 `15356b4`（v1~v32）+ v33 提交。
2. **跑全套 E2E** 确认 v1~v32 无回归：`node work/e2e_ui5.mjs && node work/e2e_ui4.mjs && node tests/e2e_ui.mjs && python -m pytest tests/e2e_api.py -q`。
3. **真实项目库验证**：上传真实 .lz/.mdb，确认颗分（z_c_KeFen）与干密度/孔隙比（源库若有值）正常显示；若真实库颗分仍空，才需查 mdb_reader 列映射。
4. ~~可塑性条目（R-DEN-007）的状态写回~~ ✅ **v33 已完成**：前端展示+确认文案+一键应用写回 TCKSX（后端定位深度所在层）。
5. ~~地层列设置"岩土名称"出现 2 次~~ ✅ **v33 已完成**：TCYMC 显示「岩土名称（TCYMC）」默认不勾选。
6. `renderReview/renderBorehole/renderSpt` 已删除（v31）——若未来需要独立复核/标贯页，需基于 renderTools/renderWorkspace 重建。
7. **复核后遗留（低危，可后续处理）**：inlineEdit 的 idJs 主键若含引号可注入 onclick（文本主键场景，当前主键为数字）；CORS `*`/上传无大小限制/token 内存表（部署公网前需收紧）。

---
*交接完毕：v1~v32 改动已归档；5 个文件待提交；本地服务已运行最新代码。*

## 11. 生成 MDB 功能配置（GH_TOKEN）
- 后端 build API 需要环境变量 `GH_TOKEN`（GitHub PAT，需 Actions: Read/Write + Contents: Read/Write，仓库 lifan-mdb-builder）
- 配置方式（任选其一）：
  1. 系统环境变量设置 `GH_TOKEN`（start.bat 启动的 uvicorn 自动继承）
  2. 仓库根目录放 `.gh_token` 文件（内容为 token，已加入 .gitignore）
- 未配置时触发构建返回 500（前端有明确提示，不会静默失败）
- 注意：GitHub Actions 每次构建会向仓库 `payload/{db_id}/` 提交 work.db + schema.json（约 2MB/次），历史产物会堆积仓库体积——如需清理可在 workflow 完成后删除该目录（当前保留不影响功能）

## 12. 复核目标完成状态（v34→v46）
- 全部功能运行验证通过（含生成 MDB 全链路：触发→Actions→产物→verify_mdb 85 表 11356 行一致）
- 446 个 TOML 参数全审计；修复 14 项 bug；桌面 8 项 IDENTICAL；死代码清理 1060 行
- 全套回归：pytest 17 passed + E2E 7 套全绿
- 剩余可选：真实项目库复测（需用户提供 .lz/.mdb）

## 13. 全面代码审核 + 修复记录（2026-08-14，17 个文件）

**审核方法**：全量逐文件审读 + 子代理深度审核（config.py 1022 行 / dao.py 917 行）+ 实机验证 + 回归（pytest 17 passed、e2e_ui5 26 项 ALL PASS、真实库江村西 658 孔验证）。

### 修复清单（除「# 列分页重排」展示问题外全部修复）

| 级别 | 问题 | 修复 |
|---|---|---|
| P0-安全 | `stats/download`/`dxf/download` 未校验 db_id，URL 编码 `%2E%2E%5C%2E%2E` 可越界读任意文件（已实证 22921B） | 两端点复用 `_resolve_db_path` 校验（实测 400 封堵） |
| P0-功能 | `get_all_test` 不读颗分表 → 全库复核 R-GRS-001 恒漏（单孔复核却有） | 批量联查 z_c_KeFen（实测全库 147→251 条，单孔/全库 5/5 一致） |
| P0-功能 | 参数中心保存后复核不生效：`_engine_cache` 未清 + config/review.config 双模块缓存 + import 期派生常量固化 | `_reload_config_modules()`：清引擎缓存 + 双模块 reload_config + 10 个模块 reload_from_config 钩子（实测 674→436→674） |
| P0-健壮 | config.py TOML 语法错误未捕获 → 整模块 import 崩溃、后端无法启动 | `_load_toml` 捕获全部异常返回 None；失败不缓存可重试 |
| P1 | spt-apply 可塑性地层定位 SQL 与引擎 `(层顶,层底]` 口径不一致（层界深度归错层） | 按引擎口径逐层匹配 + 浮点容差（BGJS 更新 ABS<0.005） |
| P1 | spt_to_weathering None→0 误判"残积土"；il_to_plasticity 空隙兜底伪造状态；IL≥ 中文键静默丢弃 | None→返回 None（_coerce 口径统一）；空隙→None；`≥`/`<` 开闭边界解析 |
| P1 | 前端登出不吊销 token（未调 /api/logout、未清 localStorage） | 登出调 /api/logout + setToken('') + 清 session；401 同步清理 |
| P1 | dao.set_project_type 模块级全局态并发串 A/B 口径 | review_api/feature_api 显式传 project_type |
| P1 | page_size 只钳上限（-1 → LIMIT -1 全表返回） | 双端钳制 `max(1, min(page_size,200))` |
| P2 | onclick 单引号注入（escAttr 不转义 `'`） | 新增 escJs，wsSelectHole/sortBy/filterBy/inlineEdit 全部应用 |
| P2 | 前端死代码（IndexedDB 层/seed/sha256/PERMISSIONS/pendingMdbFile/openOnlineEdit） | 删除（can() 恒真兼容）；登出改后端吊销 |
| P2 | upsert_row 无 id 列时静默 INSERT | 明确报错 |
| P2 | dpt-correct 重型判定/数值比较与 dao 不一致 | 共用 `_is_heavy_dpt` + 容差比较 |
| P2 | 上传无大小限制 / ZIP 炸弹 | 流式写入 512MB 上限（MAX_UPLOAD_MB 可配）+ 解压后校验 |
| P2 | mdbtools CSV 逐行解析遇 memo 换行错行 | 改 csv.reader 全文解析 |
| P1-⑤ | 配置派生常量 import 期固化（区间表/关键词/杆长系数/岩溶阈值/DXF 参数等） | config.py 构建器重构 + 全部消费模块 reload_from_config 钩子 |
| 杂项 | GJKXBP0 变体探测、get_all_test_full 回退列名/kxb A-B 统一、钻孔列探测、类级列名缓存改实例级、get_test_data N+1 批量化、R-CHK-003 零深度层防护、_parse_toml_range_to_single int 防御、软土 None 防御/末档顺序无关、同值双档除零、动探轴单调校验、ALLOWED_FIELDS 补 3 列、insert_stratum tcmc 键、_retry_read 死代码、login 过期 token 清理、`_bearer_token` 占位 | 全部修复 |

### 测试基线更新（行为变化，非回归）
- `test_review_all` / `test_review_include_test`：147 → **251**（含 R-GRS-001 104 条，P0 修复的正确口径）
- `cmp_runner.py`：一致性判定对 R-GRS-001 豁免（桌面版旧代码同样漏颗分，Web 修复后更正确；实测差异 100% 为 GRS）

### 已知保留项
- `#` 列孔内序号分页重排（纯展示，用户指定不修）
- CORS `*` / token 内存表 / 默认 admin:admin（公网部署前收紧）
- 真实库验证库：`C:\Users\神舟\Desktop\数据库再备份\江村西2026-0803-15.19备份.mdb`（db_id=ee7d0c77 已上传验证）

## 14. 第二轮复核（2026-08-14，代码+数据全量）

**数据全量验证**：e2e_real 408 孔 + 江村西 658 孔逐孔单孔复核 vs 全库复核对比 **0 不一致**（1066 孔全部一致）；spt-scan 建议抽查合理；R-GRS-001 定名抽样合理；A/B 岩溶统计、土工统计、柱状图、纵断面（有效孔）生成全部通过。

### 第二轮新发现并修复
| 级别 | 问题 | 修复 |
|---|---|---|
| P1-功能 | review_hole 对"只有土工数据"的孔误判 404"无数据"（全库复核却有 R-PLS/GRS 问题）——全量对比暴露 | test 参与"无数据"判断（实测 26-ZD-GZXT-0-1 404→200） |
| P1-功能 | github_trigger wait_for_run：30s 向后容差 + 兜底取"最近一条"可匹配到上一次/别的 db 的 run → 旧产物静默缓存到当前 db_id | 精确 `created_at>=after_ts` 匹配 + 找不到即失败，绝不顶替 |
| P1-安全 | github_trigger db_id 未消毒直接拼 artifacts/build.json 路径（CLI 用法可穿越） | 新增 sanitize_db_id，入口/写状态/缓存三处统一消毒 |
| P1-功能 | karst_report_a `_natural_key` int/str 混合键 TypeError（孔号数字/字母开头混排崩溃） | 排序键改 (type_tag, value) 二元组 |
| P1-功能 | karst_report_a 溶洞行缺 `thick>0` 过滤，附表 7A 与 8A 条数分裂（父模块 K1 修复回潮） | 与父模块同口径 `and thick>0` |
| P1-功能 | karst_report_a 绑定顶层 karst_report 实例，配置保存后附表 8A 分档阈值陈旧 | reload_from_config 对 'karst_report'/'review.karst_report' 双实例重建 |
| P1-功能 | profile_strip 单孔/同里程（min_lc==max_lc）无分段生成失败 | while 改 `<=` 保证至少一段；末段边界孔含端点归入 |
| P1-功能 | verify_mdb 混合日期/普通字符串列排序 TypeError 整个校验崩溃 | _as_dt 仅接受完整日期格式（防 '2024' 假一致）+ _safe_sort_key 类型前缀 |
| P2 | github_trigger cache_artifact 只捕 RuntimeError（BadZipFile 逃逸、状态卡 running）/zip 解压无穿越防护/多 .lz 取第一个 | except Exception 统一写 failed + _safe_extract 成员校验 + 排除"备份"条目 |
| P2 | karst_report_a 模板残留数据行混入输出 / 见洞率替换判定 round 到 0 漏替换 | 填充前清空数据区旧值 / 改用"溶洞顶板深度 is None"判定 |
| P2 | verify_mdb MDB 缺列崩溃 / zip extract 穿越 | try 记差异继续 + 条目路径校验 |
| P2 | profile_strip prev 倒退重复计票（地层倒序数据） | prev 只前进不后退 |
| P2 | sqlite_to_mdb TEXT(n) 数据超长 ACE 截断报错 / MEMO 主键 DDL 失败 | 超长自动升级 MEMO / MEMO/LONGBINARY 主键降级 |

### 确认无问题（第二轮实测）
- 纵断面 500 根因是**数据**（所选孔 ZKSD=0 被有效孔过滤），非代码缺陷；ZKSD>0 的孔生成正常
- 全量单孔/全库一致性 1066/1066；配置热生效（674→436→674）；路径穿越 400 封堵；page_size 钳制
