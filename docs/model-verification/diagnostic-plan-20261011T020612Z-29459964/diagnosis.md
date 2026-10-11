# 新供应商生成超时诊断（2026-10-11）

使用当前 load_settings() 配置：基础地址 `https://www.cctq.ai/v1`，模型 `gpt-6.1-sol`。
最终请求地址 `https://www.cctq.ai/v1/chat/completions`。
诊断未改 .env、正式适配器、工作流、认证、业务接口、依赖或旧证据。
没有打开数据库，没有启动正式应用，没有自动重试、提交或推送。

## 超时与运行锁关系

| 层级 | 当前限制 | 实际作用 |
| --- | --- | --- |
| `app/services/llm_client.py:82` | `httpx.post(timeout=40.0)` | connect/read/write/pool 各阶段超时，不能当作严格的 40 秒总时长；不跟随重定向 |
| `app/services/workflow.py:23` | `MODEL_SECONDS=40.0` | 生成及复盘都用 Queue.get 等待，最多 min(40, 主工作剩余预算)；超时丢弃晚到结果 |
| `app/services/workflow.py:22` | `WORKFLOW_SECONDS=60.0` | 每次工作流从开始起的总预算；主工作截止为第 55 秒，最后 5 秒留给失败事务 |
| `app/services/workflow.py:56` | SQLite 等待最多 min(5秒,剩余预算) | BEGIN IMMEDIATE 与 commit 的锁竞争等待，不是模型运行锁 TTL |
| `app/db.py:57`、`app/repositories.py:109/283` | 无单独时间 TTL | 唯一部分索引与 running 状态阻止同项目并发；成功/失败事务完成后释放 |
| `app/db.py:106` | 服务启动恢复 | 遗留 running run/tool 标为 failed / INTERRUPTED，不按 started_at 定时过期 |
| `smoke_a07.py:47` | HTTP 客户端 timeout=65 | 等待本地应用 API 的阶段超时，不覆盖后端 40 秒模型等待 |
| `smoke_a05.py:51` | 启动等待10秒，退出 join10秒 | HTTP 监听辅助器限制，与模型等待不同 |
| `app/static/app.js:15` | 管理请求70秒 | 浏览器 AbortController 预算；也需要与后端预算协调 |

生成和复盘同步 HTTP 在 daemon 线程中执行；工作流队列超时不能强制取消供应商请求。
线程不访问数据库，晚到输出不会写入已失败 run。当前运行锁不在40秒自动过期，
而是由 finally 的失败收尾释放；进程异常中断则依赖下次启动恢复。
只延长 HTTP timeout，工作流仍会在40秒返回504；只延长 MODEL_SECONDS，HTTP阶段40秒限制仍在。

## 两次独立请求的实际结果

| 请求 | HTTP | 耗时 | 结果 |
| --- | ---: | ---: | --- |
| 最小非流式请求，只回复 OK | 200 | 2.625秒 | choices[0].message.content 为字符串 OK，finish_reason=stop |
| 条件执行的方案诊断，重放旧 test 项目输入与当前方案提示词 | 200 | 35.843秒 | Plan校验通过；3条假设、4道题，finish_reason=stop |

最小请求：北京时间10:04:43–10:04:45。
方案请求：北京时间10:06:12–10:06:48；35.828秒后取得响应头。
方案提示词1664字符，响应文本1702字符；用量1017 prompt tokens、964 completion tokens、1981 total tokens；
completion_tokens_details.reasoning_tokens=173。

每种请求只发送1次，总计2次，没有自动重试。两次命令退出码均为0，HTTP成功2次、失败0次。
每次均由 asyncio.timeout(120) 限制整个连接/发送/读取过程，同时HTTPX各阶段上限120秒。
请求体 stream=false；AsyncClient.stream 仅用于及时记录HTTP响应头，不请求供应商流式输出。
诊断继承与正式适配器相同的 trust_env=true、不跟随重定向行为。
没有设置额外 max_tokens 或其他供应商选项，避免改变正式请求兼容性。

方案诊断仅直接请求供应商，不走正式40秒工作流，不发布、不提交回答、不复盘，未进行完整链路复验。
输入来自旧失败证据中的受控 project_input，通过 ProjectCreate 校验且 data_kind=test。
请求使用与正式适配器相同的 system/user 两条消息及JSON序列化方式。
只保存返回内容哈希、长度、校验结论及白名单用量，不保存原始方案文本或响应头/异常原文。

## 判断与最小修复建议

当前供应商连接、Bearer请求和非流式Chat响应格式可用；较长方案输出也能满足项目Plan契约。
本次结束原因为stop，没有输出截断证据。964个completion tokens与35.843秒耗时说明方案请求明显慢于OK请求，
但这些数据不能分离供应商排队、推理、生成或传输时间，也不能单独证明“输出过长”是首次失败原因。

旧正式冒烟在40.047秒收到504/LLM_TIMEOUT，未留下完整上游CallInfo。
本次相同test输入独立请求耗时35.843秒，未复现超过40秒；现预算只剩约4秒余量。
因此可确认40秒工作流等待是首次失败的直接截断点，供应商响应波动是合理推测，尚不能定位其内部原因。

若继续使用当前模型，最小预算调整候选（本轮仅建议，未实施）：

- HTTP阶段超时40→60秒，生成及复盘 MODEL_SECONDS 40→60秒一起调整。
- WORKFLOW_SECONDS 60→80秒：主工作75秒、失败清理截止80秒，为模型之外的数据库读写与校验留余量。
- smoke_a07 本地HTTP等待65→85秒，前端管理AbortController 70→90秒；若部署层有请求超时，也需高于后端80秒。
- 运行锁没有TTL可同步修改；保留 running 唯一索引和 finally 失败释放、启动恢复。验证延长后同项目仍拒绝并发、
  超时仍释放锁、晚到结果不写入；不能靠提前解锁接受并发来绕过等待限制。
- 测试中的40秒配置断言需与预算修改同步；复验现有超时/失败清理测试及隔离真实链路。

HTTPX阶段60秒并不保证HTTP总耗时60秒，工作流MODEL_SECONDS仍是整体模型等待的最终护栏。
这组数字是基于本次样本的候选余量，不保证供应商以后都在60秒内完成。
如果必须保留原任务文档的60秒总预算，应优先选择更快的兼容模型/供应商，而不能只扩大等待。
不建议凭本次数据强行缩短输出或设置过小max_tokens，以免截断必须的Plan JSON。

## 文件与验证

- [最小请求证据](../diagnostic-20261011T020443Z-326c3609/result.json)
- [方案请求证据](result.json)
- [首次正式链路超时证据](../20261011T015757Z-a015c852/result.json)
- 独立脚本 `diagnose_llm.py`；默认最小请求，`--plan-input` 仅用于重放受控test方案请求。
- `conda run -n firstorder-lab python -m pytest tests/test_llm_diagnostic.py -q --tb=short --basetemp evidence/pytest-llm-diagnostic-final-20261011`：6通过、0失败，0.34秒，全为Mock HTTP。
- 自检覆盖一次非流式请求、总deadline无重试、错误正文不泄露、缺失content、合法Plan及null内容。
- 既有配置、正式超时实现和已有验证文件均通过SHA256快照核对，未改变；新证据不包含API Key。
- 未重跑全量pytest、正式完整链路或浏览器验证；本轮没有修改正式业务代码。
