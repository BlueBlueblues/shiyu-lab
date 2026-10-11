# 502 异常分类诊断（2026-10-11）

实际分支 `feat/a-backend`，HEAD `532b225b84adcfd37aebd68c64bf1c4f9792ad8a`；本轮开始未发现适用 AGENTS.md，保留 B/C 集成、时间预算、当前供应商配置及已有证据。
原失败证据：`docs/model-verification/20261011T022848Z-6868e981/result.json`。生成接口返回本地 502 `LLM_UPSTREAM_ERROR`，实际模型调用 11.799 秒，`http_status=null`。

## 原记录能确定的范围

`call_model()` 先通过非流式 `httpx.post()` 完成整个 HTTP 请求和响应体缓冲，返回后才执行 `info.http_status = response.status_code`。
因此原失败发生在该赋值之前的 HTTP 调用阶段；若正式 HTTP 响应正常返回，非成功状态和 JSON/Chat 格式解析错误均会保留其状态码。
原实现将 `httpx.RequestError` 与 `httpx.HTTPStatusError` 合并映射为本地 502，但没有保存类型。结合本次记录，可以排除正常返回响应后执行 `raise_for_status()` 的路径，不能追溯具体的 TLS/DNS/连接/读写/协议异常。

`http_status=null` 表示适配器没有取得可返回的完整响应对象，不等于上游返回 502，也不一定表示从未收到响应头。新增 MockTransport 测试证明：上游先给出 200 响应头，再在响应体读取中抛 ReadError，缓冲型 `httpx.post()` 仍不会返回响应对象，适配器状态为空。
本地 HTTP 502 是应用错误映射，不能作为供应商 HTTP 502 的证据。

## 最小代码修改及错误契约

修改 `app/services/llm_client.py`：在既有 `CallInfo` 和 `llm_http_call` 结构日志上增加 `stage`、`failure_kind`、`exception_type`、`cause_types` 四个字段。
仅保存异常类型名称，原因类型链最多 5 层；TLS/DNS 分类依赖实际 ssl/socket 异常类型，不扫描异常消息猜测。
不保存异常字符串、traceback、URL、认证头、API Key、完整请求/响应正文。原模型名、受控耗时、HTTP 状态和允许的 token 元数据保留。
诊断字段沿既有工作流写入 `tool_calls.output_ref.model_call`，并由 `smoke_a07.py` 自动收集到新证据，无需改变业务错误包装、认证或依赖。

| 情况 | 诊断分类/阶段 | 本地契约 | 上游 HTTP 状态 |
| --- | --- | --- | --- |
| HTTP 非成功状态（含重定向） | `http_status` / `http_status`，HTTPStatusError | 502 LLM_UPSTREAM_ERROR | 从返回响应或异常的 response 保存真实状态 |
| 连接/网络/协议异常 | `transport` / `http_request`，保存具体异常类型 | 502 LLM_UPSTREAM_ERROR | 无完整响应时为 null，不能捏造状态 |
| 原因链包含 ssl 异常 | `tls` / `http_request` | 502 LLM_UPSTREAM_ERROR | 同上 |
| 原因链包含 socket.gaierror | `dns` / `http_request` | 502 LLM_UPSTREAM_ERROR | 同上 |
| HTTPX 响应解压/编码失败 | `response_decoding` / `http_request`，DecodingError | 原有 502 LLM_UPSTREAM_ERROR | 缓冲助手未返回响应时仍可能为 null |
| HTTPX 阶段超时 | `timeout` / `http_request`，保存 Read/Connect/Write/PoolTimeout 类型 | 504 LLM_TIMEOUT | 可取得则保存，否则 null |
| 成功 HTTP 响应不是可解析 JSON | `response_json` / `response_json` | 502 LLM_OUTPUT_INVALID | 已保存真实状态，通常 200 |
| JSON 中 Chat choices/content 等格式错误 | `chat_response` / `chat_response` | 502 LLM_OUTPUT_INVALID | 已保存真实状态 |
| 成功 HTTP 中 error 对象 | `upstream_error_payload` / `chat_response` | 502 LLM_UPSTREAM_ERROR | 如 200；不能改写为 502 |

HTTP 失败状态优先于错误响应的 JSON 解析失败，保持原错误契约。
HTTP/Chat 成功不等于方案或复盘的业务结构校验成功；后续校验仍由 planning/reviews 与工作流的错误码、既有校验记录说明，不在 HTTP 日志中编造模型调用失败。
HTTP 各阶段 60 秒、模型等待 60 秒、工作流 80 秒、冒烟客户端 85 秒均保持上轮设定，无自动重试。

## 自动验证

修改 `tests/test_a03.py` 与 `tests/c_integration/test_workflow.py`，增加 TLS/DNS 类型链、网络/解码异常、真实上游 502、JSON/Chat 区分、敏感信息不进入日志、类型链有界、读取响应体失败丢失状态，以及计划/复盘诊断元数据落库断言。全部为 Mock/隔离临时数据库，真实模型调用 0 次。

- 针对性首轮：`conda run -n firstorder-lab python -m pytest tests/test_a03.py tests/c_integration/test_workflow.py::test_real_adapter_maps_http_errors_and_cleans_run -q --tb=short --basetemp evidence/pytest-upstream-diagnostics-20261011`，**85 passed / 0 failed**，1.92 秒。
- 增加响应体读取失败验证后，全量：`conda run -n firstorder-lab python -m pytest -m "not e2e" -q --tb=short --basetemp evidence/pytest-upstream-full-20261011`，**414 passed / 0 failed / 1 skipped**，39.30 秒。
- 唯一 skip 为未接通报告 UI 的浏览器骨架；保留既有 Starlette/httpx 弃用 warning，没有为此变更依赖。

## 当前配置单次真实复验

自动回归通过后，使用当前 `https://www.cctq.ai/v1` / `gpt-6.1-sol` 执行 `conda run -n firstorder-lab python smoke_a07.py` 一次。
脚本替换为独立 TemporaryDirectory SQLite、独立管理凭据和 `data_kind=test` 项目，断言数据库路径不同于当前配置；原反馈库不打开。新证据目录按时间/随机后缀生成，保留全部旧记录。

实际结果：**成功**，2026-10-11 **10:40:47–10:42:45（北京时间）**，总耗时 **117.703 秒**。15 个业务 HTTP 阶段全部达到预期状态，**13 项检查通过 / 0 失败**；模型共 **2 次调用 / 0 失败 / 0 自动重试**。
生成模型调用 58.582 秒、复盘模型调用 58.457 秒，两次均为上游 HTTP **200**。对应本地生成/复盘请求分别 58.609 / 58.484 秒。总耗时累加两次独立工作流，80 秒预算适用于每次工作流，非完整冒烟总耗时。

新证据：[result.json](model-verification/20261011T024047Z-e832b021/result.json)、[plan.json](model-verification/20261011T024047Z-e832b021/plan.json)、[review.json](model-verification/20261011T024047Z-e832b021/review.json)、[报告 Markdown](model-verification/20261011T024047Z-e832b021/report.md)、[验证与内容审查](model-verification/20261011T024047Z-e832b021/verification.md)。

本轮没有复现旧 502，不能事后把旧错误命名为 TLS、DNS 或供应商 HTTP 502，也没有证据证明添加日志修复了网络故障。定位结论仅限：旧异常发生在缓冲 HTTP 请求尚未返回完整响应的分支，具体异常类型已丢失；今后该分支能保存受控类型链。
两次成功调用均接近 60 秒模型等待期限（余量约 1.4–1.5 秒）；保留当前预算，单次成功不能证明响应时延稳定。供应商与模型都曾改变，不能作供应商独立性能结论。

## 内容审查与边界

方案依据创建时的项目输入，生成时尚无回答，属于待验证假设；无虚构市场规模、订单或已完成调研。需求、价格和试用意愿分别经 h1/q1+q2、h2/q3、h3/q4 关联。
复盘依据 **3 份受控 test 回答**，报告快照截止 ID 为 3。q1/q2/q3 的 a1/a2/a3 各为 1 份、33.33%，a4 为 0；q4 保存三个受控文本。12 个证据引用与本快照及假设题目关联相符。
三条假设均为 `insufficient_evidence`，明确不代表真实用户总体、付费需求或商业成功，未发现虚构数值、订单或真实参与者。
JSON、Markdown 的回答份数和持久化快照一致；Markdown 明确“回答份数不等同独立参与人数”。加入第 4 份回答后，旧报告保持 3 份、stale=true，旧 Markdown 内容不变。

内容仍有以下待人工修订项（原模型产物保持原样）：

- h3 的“只能说明回答者希望了解交付周期和费用”宜改成“受控测试文本表达了希望了解交付周期和费用”，避免赋予程序样例真实参与者身份或意愿。
- 行动 1 的“一批真实回答”“预先设定的样本量和场景分层规则”没有给出具体份数/已存在规则；行动 3 的“约定数量的真实试用”同样未量化。应先人工明确目标数量、截止时间及验收材料。属于完成标准不够可核验，不是 JSON/Markdown 结构失败。
- 方案 q3 的“超过50元或需进一步了解”混合价格档位与不确定意愿，下一轮问卷应分开选项；价格档位是待验证设置，没有证明客户愿意支付。

本轮只审查，没有改写方案、复盘、提示词或重新调用模型。真实浏览器、手机和真实用户试用未执行；report.html 仍属于既有待交付项。
最终 `git diff --check` 通过。当前配置、诊断脚本与全部旧模型证据的 SHA256 对比无变化；没有提交、推送或合并。
