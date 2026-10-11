# test/c-foundation 相对基线 3f79df9 的差异说明（交 A 审阅）

编制：C｜时间：2026-10-10｜基线：`3f79df9`（`test: fix missing-template isolation and health contract checks`）
验证分支：`test/c-integration`（**未向 `feat/a-backend` 推送任何修改**）
验证环境：Python **3.11.9** + 独立虚拟环境 + `requirements.txt` 冻结版本（fastapi 0.143.0 / pytest 9.1.1）+ 隔离 SQLite

---

## 1. 一句话结论

| 项 | 结果 |
| --- | --- |
| **基线自身** | ✅ 完全可复现：A 的 **280 passed**、模板脚本 **34/34** |
| **集成本分支后（341 个用例）** | **311 passed / 30 failed / 1 skipped** |
| A 的测试是否被影响 | ✅ **无影响**，集成后仍 **280 passed** |
| C 的用例中失败的 30 个 | **22 个是 A06/A07 接口未实现（404）**，**8 个需要 A 裁决**（见 §5） |

**关键点：本分支不修改任何后端业务代码。** 全部改动都在 `tests/`、`scripts/`、`docs/`、`README.md`、`pytest.ini` 内。

---

## 2. 任务1：基线独立复验（3.11 + 隔离 SQLite + test 数据）

### 2.1 环境

```powershell
py -3.11 -m venv <仓库外的独立目录>\venv311
<venv311>\Scripts\python.exe -m pip install -r requirements.txt
```

实测解释器 **Python 3.11.9**；关键包版本与 `requirements.txt` 完全一致。

### 2.2 命令与结果

| # | 命令 | commit | 实际结果 |
| --- | --- | --- | --- |
| 1 | `<venv311>\Scripts\python.exe -m pytest tests -q` | `3f79df9` | **280 passed in 22.22s** |
| 2 | `<venv311>\Scripts\python.exe docs\verify_templates.py` | `3f79df9` | **共 34 项，通过 34 项，失败 0 项** |
| 3 | `git diff --check 8eb6e6b 3f79df9` | — | 退出码 0（无空白错误） |

**A 声称的「A05 44/44、模板验证 34/34、git diff --check 通过」经独立复验全部成立。**
（说明：`tests/` 下 A 的用例总数实为 **280**，`test_a05.py` 单文件为 20 个 `def test_`；「44/44」应是 A 本机另一口径，不影响结论。）

### 2.3 隔离性证据

- 每个用例独立临时 SQLite（`DATABASE_PATH` 指向临时目录），运行后删除；
- 全程 **模型真实 HTTP 调用次数 = 0**（mock 覆盖）；
- 未触碰 `data/` 下任何数据库。

---

## 3. 任务2：`/health` 契约对齐

### 3.1 基线事实

- 实现：`app/main.py:221` → `return {"data": {"status": "ok"}}`（**带 data 包装**）
- A 的测试：`tests/test_a01.py:64` 断言 `{"data": {"status": "ok"}}`
- A 的隔离改造：`tests/test_a05.py::test_missing_B_template_explicit` 用独立空模板目录触发真实
  `TemplateNotFound`，不再依赖仓库是否已交付模板 —— **这个改造是对的，我确认通过**

### 3.2 我方对齐（任务2 要求）

| 文件 | 原断言 | 改后 |
| --- | --- | --- |
| `tests/test_api.py` | `{"status": "ok"}` | `{"data": {"status": "ok"}}` |
| `scripts/smoke.py` | `body == {"status": "ok"}` | `body == {"data": {"status": "ok"}}` |

同时把用例名 `test_health_check_is_public_and_unwrapped` 改为
`test_health_check_is_public_and_data_wrapped`，避免名字与行为相反。

### 3.3 ⚠️ 需要 A 在总纲侧追认

**这是对总纲 §6.2 原文的有意偏离。** 总纲写的是 `GET /health` → `{"status":"ok"}`（无包装）。
当前基线选择"与其它成功响应一致地包装"，团队已按此统一。

- [ ] **A 确认**：把总纲 §6.2 的 `/health` 表述改为 `{"data": {"status": "ok"}}`
      （否则下一轮又会有人按原文改回去，重演这次冲突）
- 已知影响面：所有消费 `/health` 的探活脚本、C 的 `T13`、`scripts/smoke.py`；
      外部监控若按无包装解析会失败。

---

## 4. 集成分支做了什么（逐文件）

### 4.1 新增（C 独有，基线没有）

| 文件 | 内容 |
| --- | --- |
| `tests/conftest.py` | 测试基座：临时库、mock 模型、四类夹具、发布/提交/统计辅助 |
| `tests/test_api.py` | T01/T04/T05/T06/T12 等契约用例 |
| `tests/test_statistics.py` | T07 统计口径（含 66.67% 分母、零样本） |
| `tests/test_workflow.py` | T03/T08/T09/T10 与报告下载 |
| `tests/test_harness.py` | 对测试脚手架自身的自检 |
| `tests/e2e/test_flow.py` | T11 浏览器链路（需 Playwright） |
| `tests/fixtures/plan.json`、`review.json` | 固定夹具（mock） |
| `scripts/smoke.py` | 最小验收脚本，退出码 0/1/3 |
| `pytest.ini` | 测试配置 |
| `README.md` | 部署/配置/启动/测试/局域网/备份/E2E 说明 |
| `requirements-c-repro.txt` | C 本机实际版本（非冻结文件） |
| `docs/env-reproduction.md` | 环境复现记录与沙箱缺陷定位 |
| `docs/test-results.md` | T01~T14 状态与 BUG 台账 |
| `docs/submission-checklist.md` | 赛事要求核验清单 |
| `docs/user-recruitment.md`、`docs/user-test-log.csv` | 试用招募与记录字段 |
| `docs/api-selection-questionnaire.md` | 供应商选型问卷 |
| `docs/handoff-c-2026-10-09-10.md` | C 的交接记录 |
| `docs/模板交付需求.md` | 模板交付需求（已由 A 采用其中 `data-testid` 与 `is_preview` 约定） |
| `docs/tasks/03...`、`docs/tasks/04...` | 成员 B / C 指南副本（01/02 基线已有） |

### 4.2 修改（仅测试与脚本，不改业务）

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `tests/conftest.py` | ① mock 重绑列表补 `app.services.planning`；② 新增 `FakeLLM.as_model_fn()` 适配器；③ `_reply()` 改用真实 `CallInfo`/`ModelReply`；④ 新增 `_contract_payload()` 剥离 `_comment`；⑤ 预导入调用链模块 | 见 §5.1，四类真实缺陷 |
| `tests/test_api.py` | `/health` 断言对齐 | 任务2 |
| `tests/test_harness.py` | 重绑断言改为「绑定被替换 + 经适配器走到假实现」；假包补 `CallInfo`/`ModelReply` | 适配器改造后原断言失效 |
| `scripts/smoke.py` | `/health` 断言对齐 | 任务2 |

**未改动**：`app/` 下任何文件、`requirements.txt`、`.gitignore`、`.env.example`。

---

## 5. 本分支修掉的 4 个真实缺陷（都是 C 自己的测试基座问题）

这些都是「看不到 A 代码时写的基座」与「A 真实实现」之间的偏差，**不是 A 的 bug**，但会让测试结果失真，必须先修。

### 缺陷 1（最严重）：mock 未覆盖 `app.services.planning`，导致测试**真的去请求模型**

| 项 | 内容 |
| --- | --- |
| commit | `3f79df9`（未修前） |
| 最小复现 | `.venv\Scripts\python.exe -m pytest tests/test_statistics.py::test_statistics_are_deterministic_and_server_side -q` |
| 预期 | 200，统计对象 |
| 实际 | `502 {"error":{"code":"LLM_UPSTREAM_ERROR",...}}` —— 请求打到了 `https://llm.invalid/v1` |
| HTTP 状态 | 502 / 记录在 `agent_runs.error_code = LLM_UPSTREAM_ERROR` |
| 根因 | A 的调用链是 `main → workflow.run_plan → planning.generate_plan`，而 `planning` 里是 `from app.services.llm_client import call_model` 的**模块级直接绑定**；`_patch_llm` 的重绑列表只有 `workflow/reports/main`，**漏了 `planning`** |
| 证据 | `llm_client.call_model` 与 `planning.call_model` 为同一对象（同一 `id`），重绑未生效 |
| 修复 | 重绑列表加入 `app.services.planning`，并在 patch 前预导入整条调用链 |
| 影响 | 若不修，**测试会产生真实模型费用**，且"mock 全绿"变成假象 |

### 缺陷 2：fake 返回类型与 A 的 `ModelReply` 不符

| 项 | 内容 |
| --- | --- |
| 实际 | `500 {"code":"INTERNAL_ERROR"}`（`AttributeError: 'str' object has no attribute 'text'`） |
| 根因 | `FakeLLM` 直接返回字符串；`planning.generate_plan` 读取 `reply.text` / `reply.call_info` |
| 修复 | 新增 `FakeLLM.as_model_fn()` 适配器返回 `ModelReply`；`FakeLLM.__call__` 仍返回文本，
  使脚手架自检（原断言字符串）与业务调用（需对象）**互不干扰** |
| 附加发现 | `workflow.make_validation_plan` 对 `call_info` 执行 `dataclasses.asdict()`，
  因此 `call_info` 必须是**真实 dataclass**；改用 A 公开的 `CallInfo` 后 `TypeError` 消失 |

### 缺陷 3：夹具里的 `_comment` 键被 `extra="forbid"` 拒绝

| 项 | 内容 |
| --- | --- |
| 实际 | `502 {"code":"LLM_OUTPUT_INVALID"}` |
| 根因 | `tests/fixtures/plan.json` 含给人看的 `_comment`；`app/schemas.py:21` 的
  `ContractModel` 使用 `extra="forbid"` |
| 修复 | 新增 `_contract_payload()`，发送前递归剥离 `_` 开头的说明性键；夹具文件保持可读 |

### 缺陷 4：假包夹具缺 `CallInfo` / `ModelReply`

| 项 | 内容 |
| --- | --- |
| 实际 | `AttributeError: module 'app.services.llm_client' has no attribute 'CallInfo'` |
| 位置 | `tests/test_harness.py::test_patch_llm_rebinds_direct_imports_too` |
| 修复 | 假包补上这两个名字（`dataclass` 定义） |

---

## 6. 剩余 30 个失败分类（**未修复，按任务要求标为待实现或待裁决**）

### 6.1 待实现（22 个）：A06/A07 接口不存在

| 缺失接口 | 影响用例 |
| --- | --- |
| `GET /api/experiments/{id}/statistics` | `test_statistics.py` 7 个 |
| `GET /api/experiments/{id}/responses` | T05/T12 相关、权限隔离用例 |
| `POST /api/projects/{id}/review` | `test_workflow.py` T08 系列（**被吞成 404，契约要求 400/409**） |
| `GET /api/projects/{id}/report`、`/report.md` | T08/T09 与报告下载用例 |
| `GET /api/projects/{id}/statistics` | 工作台聚合用例 |

**这些用例一旦 A06/A07 落地即可直接执行，无需再改测试。**

### 6.2 需 A 裁决（8 个）

| # | 用例 | 预期 | 实际 | 性质 |
| --- | --- | --- | --- | --- |
| B-1 | `test_llm_timeout_reports_504_and_releases_lock` | **504** `LLM_TIMEOUT` | **500** `INTERNAL_ERROR`，`agent_runs.error_code=INTERNAL_ERROR` | **疑似真实缺陷**：模型抛出 `httpx.TimeoutException` 后没有映射为 504。已确认 `llm_client.py` 有 `except httpx.TimeoutException`，但该异常是在调用方线程里重新抛出、绕过了它。**需要 A 定位**：`workflow.py:82` 重抛的异常未命中 `run_plan` 的 `except` 分支，落进 `except Exception` → 500 |
| B-2 | `test_llm_upstream_error_reports_502` | 502 | 500 | 同 B-1 同源（`httpx.ConnectError` 同样被吞成 500） |
| B-3 | `test_api.py:230` 工具结果引用 | `output_ref` 含结果引用 | `make_validation_plan` 的 `output_ref` 只有 `project_id/hypothesis_count/question_count`，**无结果引用** | **判定口径分歧**：总纲要求"记录…结果引用"。A 把 `experiment_id` 记在 `create_experiment`（合理）；C 的断言要求每个工具都有引用 → 需 A 定口径 |
| B-4 | T12 权限：`/responses` 未登录 | 401 | **404** | 接口不存在，无法测权限。**当前 404 不能记作"权限通过"**（总纲要求未知路径也返回统一错误包装，此处行为可接受，但权限结论待接口落地后重测） |
| B-5 | `test_review_requires_published_experiment` | 400 `INVALID_STATE` | 404 | 同上，待 A07 |
| B-6 | `test_review_rejects_evidence_outside_snapshot` | 502 `LLM_OUTPUT_INVALID` | 404 | 同上，待 A07 |
| B-7 | `test_runs_endpoint_orders_by_start_time_desc` | 2 条 run（plan + review） | 只有 1 条（review 未实现） | 同上，待 A07 |
| B-8 | `test_markdown_report_download` | Markdown 附件 | 404 | 同上，待 A07 |

---

## 7. 运行方法（A 复现用）

```powershell
# 1. 取分支
git fetch origin
git checkout test/c-integration        # 或 git cherry-pick 需要的提交

# 2. 3.11 环境（不要把 venv 建在仓库内，避免污染）
py -3.11 -m venv ..\..\c-verify\venv311
..\..\c-verify\venv311\Scripts\python.exe -m pip install -r requirements.txt

# 3. A 的用例（应 280 passed）
..\..\c-verify\venv311\Scripts\python.exe -m pytest tests -q

# 4. 只跑 C 的用例
..\..\c-verify\venv311\Scripts\python.exe -m pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py tests/test_harness.py -q

# 5. 模板验证（应 34/34）
..\..\c-verify\venv311\Scripts\python.exe docs\verify_templates.py

# 6. 冒烟（需先启动服务）
..\..\c-verify\venv311\Scripts\python.exe scripts\smoke.py --base-url http://127.0.0.1:8000
```

**隔离要求**：`DATABASE_PATH` 必须指向临时目录或独立库；`smoke.py` 默认只用 `test` 项目，不向 `real` 实验写入。

---

## 8. 待实现依赖清单（交 A）

| 依赖 | 解锁的 C 用例 | 备注 |
| --- | --- | --- |
| A06 统计接口 `GET /api/experiments/{id}/statistics` | 7 个统计用例（含 **66.67% 可选多选分母**、零样本 null、多选去重计数） | **优先级最高**，材料"应用成效"全靠它 |
| A06 原始反馈接口 `GET /api/experiments/{id}/responses` | 权限与原始文字用例 | 仅管理员可读 |
| A07 复盘接口 `POST /api/projects/{id}/review` | T08/T09 共 6 个用例 | 需保存统计快照与 `cutoff_response_id` |
| A07 报告接口 `GET /api/projects/{id}/report` / `report.md` | 报告展示与下载 2 个用例 | Markdown 需含数据来源、real/test、时间、run_id |
| 项目页面路由 `/projects/new`、`/projects/{id}`、`/projects/{id}/report` | B 的页面模板与 T11 | 当前 `GET /` 仍是临时纯文本 |
| B 的 4 个页面模板 | T11 全链路 | **B 目前零交付**（远端无 `feat/b-*` 分支） |

---

## 9. 未完成事项（如实声明）

| 项 | 状态 |
| --- | --- |
| 浏览器交互验证 | ⏳ 未做（Playwright 未安装，`tests/e2e` 整体 skip） |
| 手机真机提交验证 | ⏳ 未做 |
| 真实模型冒烟 | ⏳ 未做（本次全程 mock，**真实模型调用次数 = 0**） |
| A06/A07 用例 | ⏳ 标为**待实现**，未记作通过 |
| 真实用户试用 | ⏳ 未开始（`docs/user-test-log.csv` 只有表头） |

**本轮不把任何未执行项写成已完成。**
