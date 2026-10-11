# 本轮收尾与待提交审查（2026-10-11）

实际分支 `feat/a-backend`，HEAD `532b225b84adcfd37aebd68c64bf1c4f9792ad8a`。仓库及父目录未找到适用 AGENTS.md。既有改动保留；未暂存、提交、推送或合并。

本轮只修改两个提示词及其针对性测试，新增修订记录和本审查记录；以下列表同时包含之前 B/C 集成、预算调整、模型脚本与验证证据。
当前 API / gpt-6.1-sol 配置和 HTTP 60 秒、模型等待 60 秒、工作流 80 秒、冒烟客户端 85 秒、管理端前端 90 秒预算保持不变。

## 实际验证

- `conda run -n firstorder-lab python -m pytest tests/test_a03.py tests/test_a07.py -q`：120 通过 / 0 失败 / 0 跳过，5.81 秒；1 条既有依赖弃用警告。
- 22 个配置、预算源码及原始验证文件前后 SHA256 比对：0 个变化。旧供应商成功记录和新供应商所有原始产物、失败与诊断记录保留。
- 本轮真实模型请求 0 次；未运行完整真实链路、全量回归、模拟 DOM、真实浏览器或手机验证。旧记录的结果不作为本轮重跑结果。
- 提示词语义质量待今后真实模型验证；新增约束不改变 JSON Schema、错误契约或已发布实验。

## 脱敏与暂存范围

- 初步检查 73 个已有待提交文件：70 个文本文件扫描当前配置密钥/密码字面值、常见密钥格式、Bearer 字面值和 URL 嵌入认证，发现 0 项。3 张历史截图已人工查看，均为测试项目/样例，只有本机测试链接，没有真实反馈或认证信息。
- 原始模型 JSON/Markdown 是隔离 test 证据；历史 user-test-log.csv 只有表头，没有真实参与者数据。测试口令及测试 token 是显式模拟数据，不是实际 API 凭据。
- .env、data/、evidence/、数据库、日志及缓存由现有 .gitignore 排除；本轮诊断和审查临时文件位于系统临时目录。以下是 gitignore 检查用的示例路径，没有打开或创建真实反馈库。

```text
.env
data/real-feedback.sqlite3
evidence/private.log
__pycache__/audit.pyc
```

- 排除建议：`docs/delivery-history/bc-28615b6-20261011/C_changes.diff` 是旧基线整包差分，与当前逐文件集成重复，仅保留本地，不建议暂存。历史文档与截图保留在标注所属基线的归档目录，不作为当前验证通过证据。
- 建议列表不包含 .env、密钥、真实数据库、临时文件或敏感日志；不使用 git add .，实际暂存区为空。

## git status --short

使用 `git -c core.quotepath=false status --short`，仅关闭中文路径转义。

```text
 M app/prompts/plan.txt
 M app/prompts/review.txt
 M app/services/llm_client.py
 M app/services/workflow.py
 M app/static/app.js
 M app/templates/workspace.html
 M smoke_llm.py
 M tests/test_a03.py
 M tests/test_a04.py
 M tests/test_a07.py
 M tools/check_pages_live.py
 M tools/check_pages_render.py
 M tools/probe_management_js.mjs
 M tools/probe_survey_render.py
?? README.md
?? diagnose_llm.py
?? docs/a07-real-model-result.json
?? docs/a07-real-model-verification.md
?? docs/a07-real-report.md
?? docs/bc-integration-20261011.md
?? docs/browser_e2e.py
?? docs/closeout-review-20261011.md
?? docs/delivery-history/
?? docs/model-verification/
?? docs/prompt-revision-20261011.md
?? docs/test-results.md
?? docs/upstream-error-diagnosis-20261011.md
?? docs/verify_a06_statistics.py
?? pytest.ini
?? scripts/
?? smoke_a07.py
?? tests/c_integration/
?? tests/test_llm_diagnostic.py
?? tools/check_a06_pages_live.py
?? tools/check_a06_pages_render.py
?? tools/probe_workspace_js.mjs
?? tools/smoke_full_chain.py
```

## git diff --stat

此命令只统计已跟踪文件，未跟踪文件另列于下一节。

```text
 app/prompts/plan.txt          |   3 +
 app/prompts/review.txt        |   3 +
 app/services/llm_client.py    |  53 ++++-
 app/services/workflow.py      |   6 +-
 app/static/app.js             | 534 ++++++++++++++++++++----------------------
 app/templates/workspace.html  | 241 ++++++++++++++++---
 smoke_llm.py                  |   4 +-
 tests/test_a03.py             | 106 ++++++++-
 tests/test_a04.py             |   2 +-
 tests/test_a07.py             |  16 ++
 tools/check_pages_live.py     |   7 +-
 tools/check_pages_render.py   |  18 +-
 tools/probe_management_js.mjs |  41 +++-
 tools/probe_survey_render.py  |  18 ++
 14 files changed, 694 insertions(+), 358 deletions(-)
```

## 未跟踪文件清单（59 个）

`git -c core.quotepath=false ls-files --others --exclude-standard`：

```text
README.md
diagnose_llm.py
docs/a07-real-model-result.json
docs/a07-real-model-verification.md
docs/a07-real-report.md
docs/bc-integration-20261011.md
docs/browser_e2e.py
docs/closeout-review-20261011.md
docs/delivery-history/bc-28615b6-20261011/B_交付说明_A06.md
docs/delivery-history/bc-28615b6-20261011/C_changes.diff
docs/delivery-history/bc-28615b6-20261011/C_delivery_notes.md
docs/delivery-history/bc-28615b6-20261011/INDEX.md
docs/delivery-history/bc-28615b6-20261011/README.md
docs/delivery-history/bc-28615b6-20261011/api-selection-questionnaire.md
docs/delivery-history/bc-28615b6-20261011/c-integration-diff.md
docs/delivery-history/bc-28615b6-20261011/env-reproduction.md
docs/delivery-history/bc-28615b6-20261011/handoff-c-2026-10-09-10.md
docs/delivery-history/bc-28615b6-20261011/pytest.ini
docs/delivery-history/bc-28615b6-20261011/requirements-c-repro.txt
docs/delivery-history/bc-28615b6-20261011/screenshots/admin-workspace.png
docs/delivery-history/bc-28615b6-20261011/screenshots/mobile-survey-320.png
docs/delivery-history/bc-28615b6-20261011/screenshots/mobile-survey-375.png
docs/delivery-history/bc-28615b6-20261011/submission-checklist.md
docs/delivery-history/bc-28615b6-20261011/test-results.md
docs/delivery-history/bc-28615b6-20261011/user-recruitment.md
docs/delivery-history/bc-28615b6-20261011/user-test-log.csv
docs/model-verification/20261011T015757Z-a015c852/result.json
docs/model-verification/20261011T015757Z-a015c852/verification.md
docs/model-verification/20261011T022848Z-6868e981/result.json
docs/model-verification/20261011T022848Z-6868e981/verification.md
docs/model-verification/20261011T024047Z-e832b021/plan.json
docs/model-verification/20261011T024047Z-e832b021/report.md
docs/model-verification/20261011T024047Z-e832b021/result.json
docs/model-verification/20261011T024047Z-e832b021/review.json
docs/model-verification/20261011T024047Z-e832b021/verification.md
docs/model-verification/diagnostic-20261011T020443Z-326c3609/result.json
docs/model-verification/diagnostic-plan-20261011T020612Z-29459964/diagnosis.md
docs/model-verification/diagnostic-plan-20261011T020612Z-29459964/result.json
docs/prompt-revision-20261011.md
docs/test-results.md
docs/upstream-error-diagnosis-20261011.md
docs/verify_a06_statistics.py
pytest.ini
scripts/smoke.py
smoke_a07.py
tests/c_integration/__init__.py
tests/c_integration/conftest.py
tests/c_integration/e2e/test_flow.py
tests/c_integration/fixtures/plan.json
tests/c_integration/fixtures/review.json
tests/c_integration/test_api.py
tests/c_integration/test_harness.py
tests/c_integration/test_statistics.py
tests/c_integration/test_workflow.py
tests/test_llm_diagnostic.py
tools/check_a06_pages_live.py
tools/check_a06_pages_render.py
tools/probe_workspace_js.mjs
tools/smoke_full_chain.py
```

## git diff --check

工作区已跟踪文件：退出码 0。
含全部建议新增文件的独立临时索引检查：退出码 0。
临时索引仅用于检查，实际 .git/index 前后哈希相同，未实际暂存。Git 的 LF→CRLF 转换提示不属于 diff --check 错误。

```text
(git diff --check 无错误输出)
(临时索引 git diff --check 无错误输出)
```

## 建议暂存的准确文件列表（72 个）

列表包含本记录自身，每行一个仓库相对文件路径，没有目录通配；仅为建议，本轮未执行实际暂存。

```text
README.md
app/prompts/plan.txt
app/prompts/review.txt
app/services/llm_client.py
app/services/workflow.py
app/static/app.js
app/templates/workspace.html
diagnose_llm.py
docs/a07-real-model-result.json
docs/a07-real-model-verification.md
docs/a07-real-report.md
docs/bc-integration-20261011.md
docs/browser_e2e.py
docs/closeout-review-20261011.md
docs/delivery-history/bc-28615b6-20261011/B_交付说明_A06.md
docs/delivery-history/bc-28615b6-20261011/C_delivery_notes.md
docs/delivery-history/bc-28615b6-20261011/INDEX.md
docs/delivery-history/bc-28615b6-20261011/README.md
docs/delivery-history/bc-28615b6-20261011/api-selection-questionnaire.md
docs/delivery-history/bc-28615b6-20261011/c-integration-diff.md
docs/delivery-history/bc-28615b6-20261011/env-reproduction.md
docs/delivery-history/bc-28615b6-20261011/handoff-c-2026-10-09-10.md
docs/delivery-history/bc-28615b6-20261011/pytest.ini
docs/delivery-history/bc-28615b6-20261011/requirements-c-repro.txt
docs/delivery-history/bc-28615b6-20261011/screenshots/admin-workspace.png
docs/delivery-history/bc-28615b6-20261011/screenshots/mobile-survey-320.png
docs/delivery-history/bc-28615b6-20261011/screenshots/mobile-survey-375.png
docs/delivery-history/bc-28615b6-20261011/submission-checklist.md
docs/delivery-history/bc-28615b6-20261011/test-results.md
docs/delivery-history/bc-28615b6-20261011/user-recruitment.md
docs/delivery-history/bc-28615b6-20261011/user-test-log.csv
docs/model-verification/20261011T015757Z-a015c852/result.json
docs/model-verification/20261011T015757Z-a015c852/verification.md
docs/model-verification/20261011T022848Z-6868e981/result.json
docs/model-verification/20261011T022848Z-6868e981/verification.md
docs/model-verification/20261011T024047Z-e832b021/plan.json
docs/model-verification/20261011T024047Z-e832b021/report.md
docs/model-verification/20261011T024047Z-e832b021/result.json
docs/model-verification/20261011T024047Z-e832b021/review.json
docs/model-verification/20261011T024047Z-e832b021/verification.md
docs/model-verification/diagnostic-20261011T020443Z-326c3609/result.json
docs/model-verification/diagnostic-plan-20261011T020612Z-29459964/diagnosis.md
docs/model-verification/diagnostic-plan-20261011T020612Z-29459964/result.json
docs/prompt-revision-20261011.md
docs/test-results.md
docs/upstream-error-diagnosis-20261011.md
docs/verify_a06_statistics.py
pytest.ini
scripts/smoke.py
smoke_a07.py
smoke_llm.py
tests/c_integration/__init__.py
tests/c_integration/conftest.py
tests/c_integration/e2e/test_flow.py
tests/c_integration/fixtures/plan.json
tests/c_integration/fixtures/review.json
tests/c_integration/test_api.py
tests/c_integration/test_harness.py
tests/c_integration/test_statistics.py
tests/c_integration/test_workflow.py
tests/test_a03.py
tests/test_a04.py
tests/test_a07.py
tests/test_llm_diagnostic.py
tools/check_a06_pages_live.py
tools/check_a06_pages_render.py
tools/check_pages_live.py
tools/check_pages_render.py
tools/probe_management_js.mjs
tools/probe_survey_render.py
tools/probe_workspace_js.mjs
tools/smoke_full_chain.py
```

内容改动、原始产物对应问题及待确认行动示例见 [提示词修订记录](prompt-revision-20261011.md)。
未进行浏览器/手机或真实用户验证，report.html 仍待 B 交付。真实模型耗时曾接近 60 秒期限，本轮保留预算；新提示词的遵循情况与耗时尚未复验。
