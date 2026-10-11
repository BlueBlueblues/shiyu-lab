# C 交付说明（2026-10-11）

交付人：C｜基线：`28615b6`（feat/a-backend，含 A06 与管理页面路由）
C 的提交：`7a0e1af`（集成分支 `test/c-integration-28615b6`）

> **一句话**：本包把 **C 的测试套件**、**B 的管理页面模板**、**A06 的独立复验**和
> **浏览器端到端验证**合并到基线 `28615b6` 上，并附可复跑的验证脚本与截图。
> **未修改 A 的任何后端代码**；对 B 的交付文件只做原样落地。

---

## 一、应用方式（重要：只覆盖下列路径）

解压本包后，把以下内容复制到仓库根目录。**`app/` 只覆盖模板与静态资源；A 的 Python 代码不在包内，不会被影响。**

```
app/templates/base.html            ← C 原版（相对基线未变，但你需要它）
app/templates/experiment_edit.html ← C 原版（相对基线未变）
app/templates/service.html         ← C 原版（相对基线未变）
app/templates/projects.html        ← B 新增
app/templates/project_new.html     ← B 新增
app/templates/workspace.html       ← B 新增
app/templates/survey.html          ← B 修改（缺陷修复）
app/static/app.js                  ← B 修改（增量实现）
app/static/app.css                 ← B 修改（新增类）
tests/                             ← 整个目录（C 新增）
docs/                              ← 逐个文件复制，勿整目录覆盖（A 的 docs/a0*.md 不在包内）
scripts/smoke.py                   ← C 新增
tools/                             ← B 的页面与问卷探针
README.md
pytest.ini
requirements-c-repro.txt
evidence/screenshots/              ← 浏览器验证截图（证据）
C_delivery_notes.md                ← 本文
C_changes.diff                     ← 相对基线 28615b6 的完整 diff
```

> 若走 git，等价于：`git checkout test/c-integration-28615b6`

---

## 二、本包包含什么

| 目录 / 文件 | 内容 | 归属 |
| --- | --- | --- |
| `tests/` | C 的测试套件：契约、统计、工作流、脚手架自检、E2E 骨架 | **C（新增）** |
| `tests/fixtures/` | 固定 plan / review 夹具（mock） | C（新增） |
| `docs/verify_a06_statistics.py` | **A06 统计独立核验**（22 项断言，不用 A 的测试） | C（新增） |
| `docs/browser_e2e.py` | **浏览器端到端**（自动起服务 + 驱动 Edge + 数据库核对，19 项） | C（新增） |
| `docs/c-integration-diff.md` | 相对基线的差异说明、缺陷复现、剩余失败分类 | C（新增） |
| `docs/test-results.md` | T01~T14 验收台账与 BUG 清单 | C（新增） |
| `docs/submission-checklist.md` | 赛事要求核验清单 | C（新增） |
| `docs/env-reproduction.md` | 环境复现记录 | C（新增） |
| `docs/user-recruitment.md`、`docs/user-test-log.csv` | 试用招募与记录字段 | C（新增） |
| `docs/api-selection-questionnaire.md` | 模型供应商选型 | C（新增） |
| `docs/handoff-c-2026-10-09-10.md` | C 的交接记录 | C（新增） |
| `docs/模板交付需求.md` | 模板交付需求与验收清单 | C（新增） |
| `docs/tasks/03_*.md`、`04_*.md` | 成员 B / C 指南副本 | C（新增） |
| `README.md` | 部署/配置/启动/测试/局域网/备份/E2E 说明 | C（新增） |
| `pytest.ini` | 测试配置 | C（新增） |
| `requirements-c-repro.txt` | C 本机实际版本（非冻结文件） | C（新增） |
| `scripts/smoke.py` | 最小验收脚本，退出码 0/1/3 | C（新增） |
| `app/templates/projects.html` | 项目列表页 | **B** |
| `app/templates/project_new.html` | 创建项目页 | **B** |
| `app/templates/workspace.html` | 工作台 | **B** |
| `app/templates/survey.html` | 问卷页（**含 B 对 C 模板的缺陷修复**） | **B 修改** |
| `app/static/app.js` | 前端脚本（**B 的增量实现**） | **B 修改** |
| `app/static/app.css` | 样式（**B 的新增类**） | **B 修改** |
| `app/templates/base.html` | 公共布局 | C（保持原样） |
| `app/templates/experiment_edit.html` | 草稿编辑页 | C（保持原样） |
| `app/templates/service.html` | 服务展示页 | C（保持原样） |
| `C_改动明细.diff` | 相对 `28615b6` 的完整 diff | — |

**app/ 下 A 的 Python 代码、`requirements.txt`、`.gitignore`、`.env.example` 一个字节都没改。**

---

## 二、关键结论（都需要你复核）

### 2.1 B 发现并修复了 C 模板的一个严重缺陷 ✅ 已用真实浏览器确认

| 项 | 内容 |
| --- | --- |
| 缺陷 | C 的 `survey.html` 缺 `data-questions` 属性 |
| 后果 | `app.js:274` 的 `collectAnswers` 依赖该属性 → `questions` 恒为空数组 → **`answers` 恒为 `{}` → 问卷在真实浏览器里根本提交不了** |
| 为什么 C 没测出来 | C 的 `TestClient` 验证不执行 JavaScript，只验证后端接口；这正好落在 C 自己标注"未验证"的区域 |
| B 的修法 | `data-questions='{{ questions | tojson }}'`（单引号包裹，`tojson` 会把 `'` 转义为 `\u0027`，安全） |
| C 的确认 | 浏览器实测 `answers` 非空且内容正确：`{"q1":"o1","q2":"o2","q3":"...","q4":["o1"]}` |

**B 的发现与修复是正确的、必要的。**

### 2.2 A06 统计口径经独立复验正确 ✅

用 `docs/verify_a06_statistics.py`（**不依赖 A 的测试**）核验 22 项，全部通过：

- 可选多选 `q4` 分母 = **3**（作答份数，不是全部 4 份）
- `o1`、`o2` 各 **66.67%**，合计 **133.34%**（多选允许超 100%）
- 零回答时所有 `percentage` 为 **null**（不是 0）
- 未发布草稿统计 **400 INVALID_STATE**
- 未登录读统计 / 原始反馈 **401**
- 原始反馈不暴露 `client_submission_id` 或 `public_token`

### 2.3 浏览器端到端 19/19 通过 ✅

用 Playwright 驱动**系统 Edge**（`channel=msedge`，避免下载 chromium），隔离 SQLite + 真实 uvicorn + 未登录公开上下文：

- 问卷 `form` 渲染了 `data-questions`；三类题可填写；提交成功提示可见
- 数据库**恰好入库 1 条**；`answers` 非空且正确；单选为字符串、多选为数组
- **375px 与 320px 均无横向溢出（0px）** —— 截图见附件产物
- 管理页 `/`、`/projects/new`、`/projects/{id}` 均渲染成功
- 统计接口 `response_count=1`、`q4` 分母=1

截图：`mobile-survey-375.png`、`mobile-survey-320.png`、`admin-workspace.png`

### 2.4 回归结论 ✅

**合入 B 的模板后，A 的 301 个用例仍全绿**；模板验证脚本 34/34 不变。

---

## 三、怎么应用本包

### 方式一：直接用完整文件覆盖（推荐）

解压后，把下列内容复制到仓库对应位置（**只覆盖列出的路径**）：

```
app/templates/projects.html
app/templates/project_new.html
app/templates/workspace.html
app/templates/survey.html
app/static/app.js
app/static/app.css
tests/           （整个目录）
docs/            （C 的新增文档；注意不要覆盖 A 的 docs/a0*.md）
scripts/smoke.py
README.md
pytest.ini
requirements-c-repro.txt
```

### 方式二：用 git 分支

```powershell
git fetch origin
git checkout test/c-integration-28615b6
```

> 若该分支尚未推送，用 `C_改动明细.diff` 手工比对，或直接采用方式一。

---

## 四、怎么验证（复现命令）

环境：Python **3.11.9** + 独立虚拟环境 + `requirements.txt` 冻结版本

```powershell
# 1. A 的用例（应 301 passed）
python -m pytest tests -q

# 2. 模板验证（应 34/34）
python docs/verify_templates.py

# 3. A06 统计独立核验（应 22/22）
$env:PYTHONPATH = "<仓库绝对路径>"; $env:FO_REPO = "<仓库绝对路径>"
python docs/verify_a06_statistics.py

# 4. 浏览器端到端（应 19/19；需要 Playwright）
python -m pip install playwright
python docs/browser_e2e.py
```

**实测结果**（Python 3.11.9 + 隔离 SQLite + test 数据）：

| 命令 | 结果 |
| --- | --- |
| `pytest tests -q` | **301 passed** |
| `docs/verify_templates.py` | **34/34 通过** |
| `docs/verify_a06_statistics.py` | **22/22 通过** |
| `docs/browser_e2e.py` | **19/19 通过** |

---

## 五、本轮修掉的 4 个测试基座缺陷（都是 C 自己的，非后端 bug）

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 测试返回 **502 LLM_UPSTREAM_ERROR** —— **真的去请求了模型** | `planning` 用 `from app.services.llm_client import call_model` 直接绑定；`_patch_llm` 重绑列表漏了 `app.services.planning` | 重绑列表补 `planning`，patch 前预导入调用链 |
| 2 | `AttributeError: 'str' has no attribute 'text'` → 500 | `FakeLLM` 返回字符串，A 的 `ModelReply` 是对象 | 新增 `as_model_fn()` 适配器；`call_info` 改用真实 `CallInfo`（`workflow` 会 `dataclasses.asdict`） |
| 3 | `502 LLM_OUTPUT_INVALID` | 夹具 `_comment` 被 `ContractModel` 的 `extra="forbid"` 拒绝 | 新增 `_contract_payload()`，发送前剥离 `_` 前缀键 |
| 4 | `AttributeError: ... has no attribute 'CallInfo'` | `test_harness` 假包夹具缺 `CallInfo`/`ModelReply` | 假包补齐 |

**缺陷 1 影响最大**：不修的话测试会产生**真实模型费用**，且"mock 全绿"是假象。

---

## 六、仍未完成的事项（如实声明，未记作通过）

| 项 | 状态 |
| --- | --- |
| **A07 复盘 / 报告导出** | ❌ **未实现**（A 下一轮）。`/review`、`/report`、`/report.md` 均不存在 |
| **管理页面完整联调** | ⚠️ **未完成**。页面能渲染，但「创建→生成→编辑→发布」这条交互链**未在浏览器里端到端跑过**（生成需真实模型）；`run-log` 轮询、保存恢复、发布确认均未验证 |
| 真实模型冒烟 | ❌ 未做，**调用次数 0** |
| 手机**真机**提交 | ❌ 未做（本轮是 375px/320px 模拟视口，不是真机） |
| 真实用户试用 | ❌ 未开始（`user-test-log.csv` 只有表头） |
| C01「全新 3.11 环境按 README 从零复现」 | ⚠️ 本轮尝试因 tar 管道损坏中断，**未独立复核** |
| `docs/test-results.md` | ⚠️ 内容过时（T01~T12 仍标"未测"，理由写的是"app/ 不存在"）。建议 A07 落地后统一刷新 |

---

## 七、待 A 裁决 / 修复的问题

```
BUG-C01（S1，错误码契约不符）
  commit: 3f79df9 起即可复现，与本轮改动无关
  重现: python -m pytest tests/test_workflow.py::test_llm_timeout_reports_504_and_releases_lock -q
  预期: HTTP 504 / LLM_TIMEOUT
  实际: HTTP 500 / INTERNAL_ERROR（agent_runs.error_code 亦记为 INTERNAL_ERROR）
  同源: httpx.ConnectError → 500 而非 502
  线索: app/services/workflow.py:82 把线程内异常原样重抛，绕过了
        app/services/llm_client.py:105 的 except httpx.TimeoutException，
        最终落进 run_plan 的 except Exception

BUG-C03（S2，判定口径分歧）
  重现: python -m pytest tests/test_api.py::test_generate_plan_creates_validated_draft -q
  现象: make_validation_plan 的 output_ref 只有 project_id/hypothesis_count/question_count
  说明: A 把 experiment_id 记在 create_experiment（设计合理），
        需 A 定"每个工具是否都必须有结果引用"

总纲 §6.2 的 /health 表述
  总纲原文为 {"status":"ok"}（无包装），实际基线为 {"data":{"status":"ok"}}。
  已按基线对齐测试；建议 A 同步更新总纲，避免下一轮冲突复发。
```

---

## 八、需要 A 的下一步输入

1. **A07**：`POST /api/projects/{id}/review`、`GET /api/projects/{id}/report`、`GET /api/projects/{id}/report.md`
   —— 这是**唯一的高优先级硬阻塞**，T08/T09/T11 末段与参赛材料「应用成效」全部依赖它
2. **真实模型账号**：供应商 / 模型名 / 额度，供 C 执行一次计划 + 一次复盘冒烟
3. **合并到 `main`**：C 的 T13 干净环境复现依赖它
4. **`BUG-C01` / `BUG-C03` 裁决**

---

## 九、B 的下一步输入

1. **`report.html`** 报告页模板（依赖 A07 的 report 接口）
2. **管理页面完整联调**（与 C 一起）：创建→生成→编辑→发布 的真实交互
3. **确认 `data-testid` 与字段命名**：C 的浏览器测试依赖 B 指南 §5 的标识
4. UI 素材/字体/图标来源 → 供 `docs/technology-sources.md`
