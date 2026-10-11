# 测试与验收结果记录

版本：V1｜维护：C｜最近更新：2026-10-10 01:30（北京时间）
对应任务：成员C指南 C02、C06 第 4 条「`docs/test-results.md` 记录 T01～T14 每项状态、
运行时间、commit、证据路径与剩余问题」；总纲 §10。

> **怎么读这份文件**
> 只有真正执行过、留下输出的才算「通过」。**未执行的一律写「未测」，不写「预计通过」。**
> 每一项都给出用例名，A 交付 `app/` 后可以直接照着跑。

---

## 1. 本轮运行环境与命令

| 项目 | 值 |
| --- | --- |
| 运行时间 | 2026-10-10 01:20～01:30（北京时间，UTC+8） |
| 设备 | DEV-C（见 [`env-reproduction.md`](env-reproduction.md)） |
| 解释器 | CPython **3.13.7**（Windows AMD64）；契约要求 3.11，见阻塞 BUG-003 |
| 虚拟环境 | `firstorder-lab/.venv`（仓库内，未提交） |
| 依赖 | 见 [`requirements-c-repro.txt`](../requirements-c-repro.txt) |
| commit | **无** —— 仓库与远端由 A 的 A00 交付，本轮没有 Git 仓库，见阻塞 BUG-004。改用下方文件指纹作为内容标识 |
| 数据库 | 每个用例独立临时库（`DATABASE_PATH` 注入），**未触碰任何真实数据库** |
| 模型 | 全部为 mock（`tests/fixtures/plan.json`、`review.json`）；**真实模型调用次数 = 0** |

运行命令：

```powershell
$root = "C:\Users\MATEBOOK\Documents\deepseek-harness\default-workspace\firstorder-lab"
$env:PYTHONPATH = "$root\tools"; $env:TMP = "$root\.localtmp"; $env:TEMP = "$root\.localtmp"
& "$root\.venv\Scripts\python.exe" -m pytest tests/test_harness.py -q --basetemp "$root\.localtmp\pytest"
& "$root\.venv\Scripts\python.exe" -m pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py -q --basetemp "$root\.localtmp\pytest"
& "$root\.venv\Scripts\python.exe" -m pytest --collect-only -q --basetemp "$root\.localtmp\pytest"
& "$root\.venv\Scripts\python.exe" scripts\smoke.py --base-url http://127.0.0.1:8123
```

原始输出：

- `evidence/reports/pytest-harness-2026-10-10.txt`
- `evidence/reports/pytest-core-2026-10-10.txt`
- `evidence/reports/pytest-collect-2026-10-10.txt`

---

## 2. 本轮实际结果

| 命令 | 结果 | 证据 |
| --- | --- | --- |
| `pytest tests/test_harness.py -q` | **10 passed**（0.20s） | `pytest-harness-2026-10-10.txt` |
| `pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py -q` | **51 skipped**，原因统一为 `ModuleNotFoundError: No module named 'app.main'` | `pytest-core-2026-10-10.txt` |
| `pytest --collect-only -q` | **61 tests collected**；`tests/e2e` 因未装 Playwright 整体 skip | `pytest-collect-2026-10-10.txt` |
| `scripts/smoke.py --base-url http://127.0.0.1:8123` | 退出码 **3**（服务不可达），提示信息可读 | 见下方 §5 BUG-001 |
| `python -m py_compile`（全部 C 的 Python 文件） | 通过 | 本轮终端输出 |

**10 个通过的用例**（`tests/test_harness.py`，不依赖 A 的后端）验证的是 C 自己的测试脚手架：
`FakeLLM` 的计划/复盘自动识别、显式脚本与故障注入、参数形态容忍；
`_patch_llm` 对 `from ... import <seam>` 直接绑定的重绑（最易出错的一处）；
`Client` 请求头合并与裁剪；`token_from_url` / `error_code` / `data_of`；
以及 `read_public_token` / `insert_running_run` 对总纲 §4 表结构的读写。

**这不等于任何业务验收通过**：51 个契约用例全部因为 A 的后端不存在而无法执行。

---

## 3. T01～T14 汇总

| 编号 | 验收内容 | 状态 | 用例 / 证据 | 阻塞原因 |
| --- | --- | --- | --- | --- |
| T01 | 创建项目、刷新、重启后数据不丢 | **未测** | `test_api.py::test_project_creation_persists_and_is_listed`、`::test_project_survives_process_restart` | BUG-001 |
| T02 | 真实模型生成方案与草稿：3 假设、4～6 题、有效引用、两条工具日志 | **未测** | `test_api.py::test_generate_plan_creates_validated_draft`（mock 路径） | BUG-001 + 真实模型账号未验证（BUG-009） |
| T03 | 模型超时/非法 JSON 后重试：明确错误、无半成品、锁释放、可再成功 | **未测** | `test_workflow.py::test_invalid_model_output_fails_cleanly_and_allows_retry`、`::test_llm_timeout_reports_504_and_releases_lock`、`::test_llm_upstream_error_reports_502`、`::test_failed_regeneration_keeps_previous_draft` | BUG-001 |
| T04 | 未发布公开 404；发布后公开 200；发布后编辑 409 | **未测** | `test_api.py::test_draft_public_url_is_404_before_publish`、`::test_publish_exposes_public_pages_and_locks_edits`、`::test_unknown_token_is_404`、`::test_draft_patch_only_allows_text_and_required` | BUG-001 |
| T05 | 三类题真实提交；无效答案 422 且不入库 | **未测** | `test_api.py::test_survey_validation_rejects_bad_answers`（8 组参数）、`::test_valid_three_type_submission_is_stored`、`::test_survey_rejects_missing_consent` | BUG-001；**手机实机验证未做** |
| T06 | 同 UUID 同内容 200 且记录数不变；不同内容 409 | **未测** | `test_api.py::test_submission_idempotency_and_conflict` | BUG-001 |
| T07 | 固定小样本统计：可选题分母、多选、零数据 null | **未测** | `test_statistics.py`（6 个用例，含 66.67% 与零分母断言） | BUG-001 |
| T08 | 零反馈与真实反馈复盘；零反馈强制证据不足 | **未测** | `test_workflow.py::test_review_requires_published_experiment`、`::test_review_saves_snapshot_and_uses_model_once`、`::test_zero_response_review_forces_insufficient_evidence`、`::test_review_rejects_evidence_outside_snapshot` | BUG-001 |
| T09 | 复盘后新增反馈：旧报告不改、stale=true、重新复盘纳入新回答 | **未测** | `test_workflow.py::test_new_response_makes_report_stale_and_old_report_is_kept` | BUG-001 |
| T10 | 工具日志与并发/中断：有起止与引用、并发 409、重启释放旧 running | **未测** | `test_workflow.py::test_concurrent_plan_request_is_rejected_with_409`、`::test_interrupted_running_run_is_failed_on_startup` | BUG-001 |
| T11 | 完整浏览器链路 | **未测** | `tests/e2e/test_flow.py::test_full_browser_flow` | BUG-001 + BUG-006（Playwright 未安装、B 的页面未交付） |
| T12 | 管理权限与公开隔离 | **未测** | `test_api.py::test_management_reads_require_admin`、`::test_management_write_requires_origin_and_site_header`、`::test_public_pages_do_not_leak_management_data` | BUG-001 |
| T13 | 新环境启动与备份恢复 | **部分完成** | 见 §4 | 启动与备份恢复依赖 BUG-001；`backup.py` 属 C06（10/13） |
| T14 | 真实用户试用与材料一致 | **未开始** | 见 [`user-recruitment.md`](user-recruitment.md) | BUG-007（试用者尚未招募，需人工完成） |

**结论：T01～T12 全部未测，T13 部分完成，T14 未开始。没有任何一项可以写成「通过」。**

---

## 4. T13 详情（唯一有进展的一项）

C06 要求「在隔离环境从副本启动，检查项目、回答、报告数量和关键 ID 一致」。

| 子项 | 状态 | 证据 |
| --- | --- | --- |
| 干净虚拟环境创建 | 通过 | `env-reproduction.md` §3 |
| 按冻结版本安装依赖 | 通过（用 C 的临时冻结，A 的 `requirements.txt` 未交付） | `requirements-c-repro.txt` |
| 测试能在新环境收集运行 | 通过 | 61 collected |
| 按 README 启动服务并看到 `/health` 200 | **未测** | BUG-001 |
| 从备份副本恢复并核对数量与 ID | **未测** | `scripts/backup.py` 属 C06（10/13），且无数据可备份 |

---

## 5. 缺陷与阻塞清单

写法遵循成员C指南第 4 节。严重度：S0=数据丢失/公开泄露，S1=主链路不可用/统计错误，
S2=可恢复交互问题，S3=纯视觉。

### BUG-001（S1，阻塞 T01～T13）

- **现象**：仓库内不存在 `app/`，`python -m uvicorn app.main:app` 无法启动，
  51 个契约用例全部 skip。
- **最小重现**：`python -c "import app.main"` → `ModuleNotFoundError: No module named 'app.main'`
- **预期**：`app/main.py` 存在并暴露名为 `app` 的 ASGI 对象（总纲 §3.2、§6）
- **实际**：模块不存在
- **环境**：DEV-C，`DATABASE_PATH` 指向临时测试库，未附密钥
- **负责人**：A（A01～A04）
- **是否阻断**：阻断 T01～T13 与演示
- **需要的输入**：A 的 `app/`、`.env.example`、`requirements.txt`、`.gitignore`

### BUG-002（S2，环境）

- **现象**：在**受文件沙箱限制的自动执行环境**里，`pip` / `python -m venv` / `pytest tmp_path`
  报 `PermissionError`。
- **根因**：CPython 在 Windows 上把 `os.mkdir(path, 0o700)` 映射为仅所有者可访问的权限，
  而 `tempfile.mkdtemp()` 正是用该模式；沙箱无法在其上落地授权。
- **重现矩阵**：见 [`env-reproduction.md`](env-reproduction.md) §4.2。
- **影响**：**只影响该受限环境**；人自己打开的终端、以及 A/B 的电脑都不受影响。
- **处理**：受限环境里用 `tools/sitecustomize.py` 垫片（**不进入仓库**）。
- **负责人**：C（已规避）。

### BUG-003（S2，环境/契约）

- **现象**：本机没有 Python 3.11（只有 3.13.7 / 3.9 / 3.8.18），契约要求 3.11。
- **影响**：依赖版本与 A 冻结的可能不一致；不能声称「按契约版本验证过」。
- **负责人**：团队决定（装 3.11 或三人一致修改契约）。

### BUG-004（S2，流程）

- **现象**：没有 Git 仓库与远端，无法记录 commit。
- **影响**：本文件只能以文件指纹代替 commit；C 无法按总纲 §2 用 `test/c-*` 分支提交 PR。
- **负责人**：A（A00 建仓库）。仓库就绪后本文件需补 commit 号。

### BUG-005（S2，阻塞 C01 一部分）

- **现象**：`requirements.txt`、`.env.example`、`.gitignore` 均未交付。
- **影响**：README 第 2 节的安装命令暂时不可执行；本机改用 `requirements-c-repro.txt` 代替。
- **负责人**：A。

### BUG-006（S3，计划内）

- **现象**：未安装 Playwright 与 chromium 浏览器。
- **影响**：`tests/e2e` 整体 skip；按成员C指南 C04 计划在 10/12 上午安装并执行。
- **负责人**：C。

### BUG-007（S2，需人工）

- **现象**：试用者尚未招募（`docs/user-test-log.csv` 只有表头）。
- **影响**：T14 无法开始；实施显著性维度缺少真实证据。
- **负责人**：团队成员（AI 无法代替）。截止：10/11 前发出邀请，10/12～13 完成试用。

### BUG-008（S3，材料）

- **现象**：10 页《参赛指南》PDF 原件未取得，`submission-checklist.md` 中标注
  【总纲转引】的页码未经逐页复核。
- **负责人**：C。截止 10/11。

### BUG-009（S2，阻塞 T02 的一半）

- **现象**：真实模型账号未验证（供应商、base URL、模型名、额度均未确认）。
- **影响**：T02 的「真实模型」一半无法完成；mock 通过不算真实模型成功（总纲 §10）。
- **负责人**：A（A00/A03）。

---

## 6. 文件指纹（代替本轮缺失的 commit）

本轮交付内容的 SHA256 前 16 位，用于确认「跑测试的确实是这一版」：

| 文件 | SHA256 前 16 位 |
| --- | --- |
| `tests/conftest.py` | `2A5EB79B880CA1C0` |
| `tests/test_api.py` | `AFC7A27A4B16247B` |
| `tests/test_statistics.py` | `204548FE94F3F31E` |
| `tests/test_workflow.py` | `541A97931EE4ACF4` |
| `tests/test_harness.py` | `0B00A760984F38C7` |
| `tests/e2e/test_flow.py` | `CA1BDE4160E9734C` |
| `tests/fixtures/plan.json` | `6EC836165F2EA93E` |
| `tests/fixtures/review.json` | `93C5CAD4C11A57B0` |
| `scripts/smoke.py` | `471CD22CD2D066FB` |
| `pytest.ini` | `AE0DD9E9B09ADFFD` |
| `README.md` | `46F468A01FFFFD2A` |
| `requirements-c-repro.txt` | `00616E358486AD6B` |
| `tools/sitecustomize.py` | `FF4BE86FE78DC998` |
| `docs/submission-checklist.md` | `4E27188861263FC8` |
| `docs/env-reproduction.md` | `0F61CB18D12CD5FE` |
| `docs/user-recruitment.md` | `33D0F14F6E980070` |

---

## 7. A 交付 `app/` 之后的复跑顺序

1. `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`，确认 `/health` 返回 200；
2. `python -m pytest tests/test_harness.py -q`，应仍然 10 passed；
3. `python -m pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py -q`，
   把每一项的通过/失败结果填回第 3 节表格（**失败不得改写成通过**）；
4. `python scripts/smoke.py --base-url http://127.0.0.1:8000 --with-plan`，
   记下真实模型的 `run_id`、供应商/模型、耗时、成功/失败；
5. 安装 Playwright 后跑 `python -m pytest tests/e2e -q`（C04）；
6. 每修一个 BUG，只复跑相关用例与必要主链路，不无理由反复跑全套消耗模型费用。
