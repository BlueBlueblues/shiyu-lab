# B/C 差异集成与模型预算验证

实际分支 `feat/a-backend`，HEAD `532b225b84adcfd37aebd68c64bf1c4f9792ad8a`，与预期一致。未发现适用 AGENTS.md。
开始时已有诊断脚本、模型验证脚本和旧证据均保留；`.env`、旧诊断脚本与旧证据的 SHA256 对比无变化。没有提交、推送或合并 main，没有修改认证、业务接口或依赖。
解压位置为独立临时目录 `firstorder-bc-audit-ef3bd9f7b22240a7a393fbd35d4b0184`。逐文件 diff 留在该目录 `reviewed-package.diff`。

| 交付包（均基于 28615b6） | SHA256 |
| --- | --- |
| `D:\AI+比赛\B_frontend_delivery_A06_20261011.zip` | `19C05EDC6A94066B4BBB7E4225A3812F02B94CC6F1367D80A5B2CEA19701F331` |
| `D:\AI+比赛\C_test_integration_20261011.zip` | `5AFE8F729BB13459D065F8E385546346E6EABA6D6705247928F2BDDF57316F0F` |

先前微信目录解压的全部文件与用户提供路径再次解压的文件逐字节一致。没有整目录覆盖应用或 tests。

## 合并范围

- `app/templates/workspace.html`：B 的正式 context 服务端首屏、A06 统计、按需原始回答；保留原 testid、测试样例提示和已发布只读入口。
- `app/static/app.js`：只替换工作台部分，保留当前问卷答案收集、必答/同意拦截、UUID 幂等、存储失效及 422 定位修复；保留失败后刷新状态并锁定按钮的增强。A06 分母、两位小数、零分母暂无数据和原始回答脱敏均由真实 API 数据驱动。
- `app/services/llm_client.py`、`app/services/workflow.py`：统一 HTTP、模型等待及工作流预算；`tests/test_a03.py`、`tests/test_a04.py` 的 HTTP 契约断言同步为 60 秒。
- 已有 `projects.html`、`project_new.html`、`survey.html`、`app.css` 审查后保留当前版本；交付重复内容不再覆盖，尤其保留窄屏题干/数据类型及换行修复。
- `tests/c_integration/`：逐文件纳入 C 的 API、统计、工作流、脚手架与 E2E 骨架及 plan/review 夹具。conftest 仅在该子目录生效，使用 `create_app(load_settings())`，不清除全局 app 模块缓存。Mock 覆盖 planning/reviews 直接绑定；遗漏入口会被本地 HTTP 断言阻止。
- `pytest.ini`：只设置 tests 收集目录、strict markers、e2e/slow 标记；不排除现有 A 文件，不设置全局自动夹具或重试。
- 新增 B 脚本 `tools/check_a06_pages_live.py`、`check_a06_pages_render.py`、`probe_workspace_js.mjs`、`smoke_full_chain.py`；保留并更新现有 `check_pages_live.py`、`check_pages_render.py`、`probe_survey_render.py`、`probe_management_js.mjs`。
- 新增 C 脚本 `docs/verify_a06_statistics.py`、`docs/browser_e2e.py`、`scripts/smoke.py`。前两者使用独立测试库；浏览器脚本改为临时目录和空闲端口，截图另存，不删除任意已有数据库。`scripts/smoke.py` 是显式已启动 test 服务的操作脚本，未在真实服务运行，`--with-plan` 会发真实模型请求，不进入自动回归。
- `smoke_a07.py`：客户端 85 秒、独立 plan.json/review.json 产物、预算与源码指纹。原有脚本副本保存在审计临时目录；旧供应商证据原样保留。
- `smoke_llm.py`：独立直连探针的 HTTP 阶段预算同步为 60 秒，供应商提示改为“当前模型 API”；仅检查语法，未再次运行真实请求。专用 `diagnose_llm.py` 的单次 120 秒诊断期限按原要求保留。
- `README.md`、本记录及 `docs/test-results.md` 提供当前说明；C/B 原交付说明、原文档、环境记录与截图保留在 `docs/delivery-history/bc-28615b6-20261011/`，属于原作者历史基线，不冒充本轮取证。

## BUG 裁决与接口兼容

最初在当前后端复现 C 的指定用例：**3 failed，1.96 秒**（超时、上游连接失败、工具结果引用）。
BUG-C01：C 的 FakeLLM 在 call_model 入口外直接抛 httpx 异常，绕过适配器异常映射，所以工作流按未知异常处理为 500。修正测试替身，保持 504/502 和释放锁断言。
另加 4 项测试，从真实 HTTP 适配器下方注入超时/连接失败，计划与复盘均得到 504 `LLM_TIMEOUT` / 502 `LLM_UPSTREAM_ERROR` 并清理运行锁。当前正式适配器的错误映射无此业务缺陷，不增加工作流的多余网络异常分支。

BUG-C03：正式 run API 返回解析后的 `output_ref`，不是数据库列名 `output_ref_json`。
`make_validation_plan` 引用项目、假设/题目数量和模型元数据；实验尚未创建。`create_experiment` 才引用实际 experiment_id。断言改为真实字段并加强每个工具引用与数量核对，没有虚构提前出现的实验 ID。
其他 C 旧契约已对齐：`detail.project.status`；简答统计为 `{id,type,answered_count,texts}`，不含 options；夹具说明键不作为严格模型输出字段。

正式 `/`、`/projects/new`、`/projects/{id}` 只注册一次，权限和 context 均按现有后端验证。所有脚本未临时挂载或替换正式页面路由。
A07 后端已实现；旧供应商完整真实链路成功证据在 `docs/a07-real-*`，基线 532b225。`report.html` 两包均未交付，工作台复盘/报告三个入口仍明确禁用；后续由 B 接 `/review`、`/report`、`/report.md` 与报告页、stale 提示。

## 预算关系

| 层 | 新预算 | 实际含义 |
| --- | --- | --- |
| HTTP 适配器 | 60 秒 | httpx connect/read/write/pool 各阶段超时，非总墙钟期限 |
| 模型结果等待 | 60 秒 | Queue 总等待，且不超过工作流剩余期限 |
| 单次工作流 | 80 秒 | 主流程最多 75 秒，预留 5 秒失败清理；模型外至少留 15 秒供状态检查与写事务 |
| 运行锁 | 无独立 TTL | running 唯一索引；成功/失败事务释放；启动将中断记录标 failed/INTERRUPTED |
| smoke_a07 与 C 冒烟计划客户端 | 85 秒 | httpx 各阶段；本地非流式小响应，晚于工作流完成/失败 |
| 前端管理请求 | 90 秒 | 包含 fetch 与响应体读取的总定时器，管理读写默认一致 |

还修复了前端在收到响应头后过早清除定时器的问题；模拟 DOM 探针证明响应体等待仍会超时并清理。
自动回归验证并发 409、计划/复盘超时失败清理、锁释放、旧草稿/报告保留、晚到结果不落库、事务失败回滚。同步 HTTP 线程不可强制取消，超时后可能继续等待网络，但不接触数据库，返回结果丢弃。

## 本轮实际验证

Python 由 `conda run -n firstorder-lab` 执行。Windows 并发 conda 激活曾导致 4 个脚本命令无法启动，改为单次激活环境内顺序运行；环境失败日志与最终通过日志分别保存，没有把失败覆盖成成功。
脚本日志及渲染产物在 `evidence/bc-integration-20261011/`（本地 gitignored）。

| 类别 | 验证 | 通过 / 失败 / 跳过 |
| --- | --- | --- |
| Python 自动测试 | 相关 A03/A04/A07 | 134 / 0 / 0，13.33 秒；为全量子集，不累计 |
| Python 自动测试 | 全量非浏览器 `pytest -m "not e2e"` | **398 / 0 / 1，41.36 秒** |
| 脚本 | `docs/verify_templates.py` | 34 / 0 / 0 |
| 脚本 | 现有/B A06 模板渲染 | 38 + 37 / 0 / 0 |
| 脚本 | 现有正式页面/B A06 真后端 | 34 + 43 / 0 / 0 |
| 脚本 | B 本地 Mock 全链路 | 43 / 0 / 0；本地假模型 1 次、真实供应商 0 次 |
| 脚本 | C 独立 A06 核验 | 22 / 0 / 0 |
| 模拟 DOM | 问卷 public / preview | 23 + 4 / 0 / 0 |
| 模拟 DOM | 工作台 A06 / 管理操作与期限 | 21 + 16 / 0 / 0 |
| 静态检查 | `node --check app/static/app.js` | 退出 0 |
| 真实浏览器 | C 浏览器脚本、手机视口 | 未运行：conda 环境缺 Playwright；历史截图不代表本轮通过 |

7 个脚本共 **251 项通过 / 0 失败**；模拟 DOM 共 **64 项通过 / 0 失败**。两者与 pytest 分别记录，不能当作真实浏览器验证。全量唯一 skip 为浏览器模块，现加入仅允许已 Mock 隔离服务的显式运行护栏。
集成中遇到的旧字段断言/导入问题和前端探针替身问题已修正后验证，初次失败不计为最终通过数。

## 新配置真实验证

自动回归及 JS 探针通过后，执行 `conda run -n firstorder-lab python smoke_a07.py` **一次**，无自动重试。
当前基础地址 `https://www.cctq.ai/v1`，模型 `gpt-6.1-sol`，配置未更换。
独立临时数据库/test 项目：创建 **1 成功**，生成 **1 失败**（应用 502 `LLM_UPSTREAM_ERROR`），总耗时 **12.375 秒**；实际模型调用 **11.799 秒**，上游 HTTP 状态缺失。未触碰原反馈库。
新证据：[本轮真实验证](model-verification/20261011T022848Z-6868e981/verification.md)，[result.json](model-verification/20261011T022848Z-6868e981/result.json)。

没有新方案或复盘内容，后续发布、回答、统计、JSON、Markdown及人工内容审查均未到达，不能宣称通过。
待人工复验项：反馈引用是否逐条依据保存快照、有无虚构用户/成交/数值、回答份数是否被误写成人数、行动标准是否具体可执行且不暗示已完成。
这次不是 60 秒超时，无法从受控错误码进一步归因网络环节。此前对比同时改变供应商和模型，不能认定新供应商性能较差。本轮到此停止真实调用，未重试。

旧成功、旧新配置超时、最小请求及计划诊断证据全部保留原样。真实用户试用、当前真实浏览器与手机验证、report.html 联调仍待完成。

最终 `git diff --check` 退出 **0**；使用独立临时 Git index 将本轮新增文件纳入空白检查，亦退出 **0**，未改变仓库真实暂存区。5 个验证脚本 AST 语法检查通过，未执行其中的额外真实请求。
