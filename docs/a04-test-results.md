# A04 工具工作流与实验草稿验收

日期：2026-10-10（北京时间）。遵循总任务大纲 V1.1，未发现需要更改技术契约的冲突。仅实现计划生成的两个工具及三条管理 API，不实现 A05～A07。

## 修改文件

- 新增 `app/services/workflow.py`：真实顺序工具执行、预算检查、失败清理。
- 修改 `app/repositories.py`：项目输入读取、运行/工具日志、草稿保存、日志查询。
- 修改 `app/main.py`：三个受保护同步 API、统一脱敏工作流错误。
- 新增 `tests/test_a04.py`：临时数据库及 Mock HTTP 模型边界测试，实际执行工作流并持久化日志。
- 新增 `smoke_a04.py`：隔离 SQLite 的真实模型接口冒烟。
- 新增本验收记录。

没有修改 `.env`、`app/db.py`、`app/auth.py`、`app/config.py`、现有 Pydantic Schema、A03 适配器/提示词或依赖，没有迁移、删除或重建正式数据表。

## 工作流与事务

1. `BEGIN IMMEDIATE` 短事务检查项目存在、运行冲突、已发布状态，写入真实 `agent_runs(action=plan,status=running)` 后提交。沿用 A01 的部分唯一索引作为并发兜底。
2. 持久化 `make_validation_plan` 开始记录，关闭连接。工具重新读取实际项目，调用 A03 `generate_plan`，复用原 Plan 校验并再次校验返回结果。模型等待期间没有数据库连接或写事务。
3. 成功时记录第一工具结束及脱敏结果：项目 ID、题数、假设数、实际模型调用元数据。输入只记录项目 ID、test/real、配置模型名、字段长度，不保存完整想法、密钥或管理员密码。
4. 持久化 `create_experiment` 开始记录。再次检查运行和发布状态，在同一事务保存 `projects.plan_json`、实验 landing/questions、第二工具成功日志及运行成功日志。任一写入或提交前预算检查失败，整份新草稿与这些成功更新一起回滚。
5. 首次 token 使用 `secrets.token_urlsafe(32)`，256 位安全随机量，超过契约的至少 128 位要求。未发布重生成保留 experiment_id、public_token、原 created_at，只替换计划与草稿内容。已发布在开始及保存事务中都拒绝重生成。
6. 失败在 finally 的独立短事务将仍 running 的工具和运行标为 failed，保留已成功的第一工具及旧草稿。没有启动的工具不虚构日志。中断状态不会被晚到的结果覆盖，沿用 A01 启动清理。

总预算 60 秒，正常执行阶段最多使用 55 秒，为失败日志预留最多 5 秒。HTTP 适配器仍设置 40 秒超时；工作流对模型结果的等待上限也是 40 秒，并受剩余执行预算限制。写锁等待和提交等待设置为不超过剩余预算，提交前再次检查；没有自动重试。

同步 HTTP 不能通过 Python 线程强制取消，因此超时后底层请求可能继续完成或产生费用。等待线程只返回内存结果、不写数据库；超时后结果丢弃，不创建草稿或改写失败记录。操作系统/磁盘故障仍可能阻止失败日志落盘；A01 的下次启动清理负责恢复遗留 running 状态，不能假装日志已经成功保存。

## API

全部沿用 HTTP Basic。管理写入还要求同源 Origin 和 `X-Requested-With: FirstOrderLab`。JSON 成功统一包裹 data，错误统一使用 error.code/message/fields；已创建运行的失败按总纲附加 error.run_id。

| API | 输入 | 成功结果 |
| --- | --- | --- |
| POST /api/projects/{project_id}/plan | 必须为 `{}` | HTTP 200；project_id、experiment_id、run_id、status=planned |
| GET /api/projects/{project_id}/runs | 无 | 真实 run 摘要数组，started_at 倒序，空项目日志为 [] |
| GET /api/runs/{run_id} | 无 | run 字段及按 started_at、插入顺序排列的 tool_calls |

run 包含 id、project_id、action、status、started_at、finished_at、error_code、error_message。tool 包含 id、run_id、tool_name、status、started_at、finished_at、error_code；数据库 JSON 字段解码为 input_summary、output_ref。使用同一读取事务获取 run 和工具，避免混合不同提交的状态。没有新增数据库状态字段。

不存在资源为 404 NOT_FOUND；运行冲突为 409 RUN_IN_PROGRESS；已发布为 409 EXPERIMENT_LOCKED；输入错误为 422 VALIDATION_ERROR；模型错误沿用 502/504 代码；内部错误仅返回固定说明，不暴露 SQLite 或 traceback。401 含 Basic 登录挑战。

## 自动化测试

```powershell
Set-Location 'D:\AI+比赛\firstorder-lab'
python -m pytest tests/test_a04.py -q
python -m pytest -q
```

实际结果：A04 为 **42 passed, 1 warning in 5.45s**；最终全量为 **236 passed, 1 warning in 12.69s**，包含原有 A01～A03 的 194 项。已有 Starlette TestClient httpx 弃用警告，未为此更改依赖。

覆盖：合法完整链路与真实工具顺序；计划/草稿一致；同项目并发及唯一索引；模型超时/上游/非法结构；保存、最终日志写入失败时原子回滚；保留旧草稿；重生成 ID/token 保留；已发布开始拒绝和保存时重查；模型等待日志可读、其他连接可写；超时结果丢弃；提交前预算耗尽回滚；未登录/错误密码/非法来源/非法 marker；空请求契约；404；重启保留成功数据和清理中断；中断结果不能覆盖；冒烟脚本隔离及成功/失败均如实报告。

自动化测试不请求真实模型；Mock 仅位于模型 HTTP 边界，成功工具日志来自实际被测函数，不是预灌入的成功记录。中断恢复测试单独模拟 running 开始记录，不宣称其为真实模型成功。

## 真实模型 A04 接口冒烟

```powershell
python smoke_a04.py
```

脚本通过 FastAPI TestClient 实际调用创建项目、计划生成和日志查询 API，模型上游为真实网络调用，没有 Mock。读取 `.env` 模型配置，但覆盖为临时 SQLite 路径与明确的测试管理凭据，不修改 `.env` 或正式数据库。每次只发一次模型请求，不自动重试。

使用 `data_kind=test` 的校园活动报名网页服务输入。先检查 SQLite 实际存储，再重建应用生命周期及连接核对持久化。临时数据库在脚本结束时删除，脱敏结果由 stdout 输出；不会发布实验或写回答、报告。成功退出码 0，失败或任何核对不通过退出码 1。

2026-10-10 实际成功结果：

```json
{
  "data": {
    "project_id": "fc797fc0-0bb4-4462-954f-d5762440cba4",
    "experiment_id": "82a0aab5-a12a-4850-8387-4cb23dd30973",
    "run_id": "4b68f792-1c5d-4288-b504-8ee72056af9d",
    "status": "planned"
  }
}
```

- HTTP 200；供应商书生·端砚；模型 deepseek-v4-flash-vision。
- run UTC 开始 2026-10-10T04:46:28.984416+00:00，结束 2026-10-10T04:46:50.910985+00:00（北京时间 12:46:28 至 12:46:50）；耗时约 21.927 秒，状态 succeeded。
- 模型调用耗时 21.899 秒，HTTP 200；接口实际 Token：prompt 1014、completion 1554、total 2568；cached_tokens=0、reasoning_tokens=918，不另加到 total，未推算费用。
- 原 Plan 校验通过：3 假设、4 题。
- make_validation_plan 工具 ID e4ba8bb6-c171-40e7-aeee-feaff9a199d6，UTC 04:46:28.989875 开始、04:46:50.896805 结束，succeeded。
- create_experiment 工具 ID e07cc16c-777b-436d-8275-7001473fcc70，UTC 04:46:50.903370 开始、04:46:50.910985 结束，succeeded。
- SQLite 实际计数：projects=1、experiments=1、agent_runs=1、tool_calls=2、responses=0、reports=0。
- plan_stored_and_validated、draft_consistent、unpublished、two_successful_tools、run_succeeded、restart_preserved 全部为 true。

本轮真实冒烟没有失败。后续追加的中断状态检查、清理预算预留和摘要模型名由最终 Mock 回归验证，没有为这些不改变正常链路的加固重复付费请求。

## 未完成内容与边界

- A05 的实验读取专用 API、草稿编辑、预览、发布与公开提交未开发；A06 统计、A07 复盘/报告未开发；没有前端或真实问卷。
- 本轮仅验证一次真实模型完整成功链路；不能据此保证供应商稳定或计划语义质量。内容仍须人工检查。
- 未开展真实进程崩溃或磁盘不可写测试；重启持久性通过重建应用生命周期/连接验证，中断通过隔离单元测试模拟。只能单进程、单 worker 部署。
