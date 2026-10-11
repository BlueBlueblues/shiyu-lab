# 新模型 A07 冒烟验证：生成阶段超时

2026-10-11，北京时间 09:57:57–09:58:38。
实际 HEAD：`532b225b84adcfd37aebd68c64bf1c4f9792ad8a`。
命令：`conda run -n firstorder-lab python smoke_a07.py`，退出码 **1**。

实际通过 load_settings() 加载的基础地址：`https://www.cctq.ai/v1`。
实际加载并用于请求构造的模型名：`gpt-6.1-sol`。
没有输出、复制或保存 API Key；本次结果不足以确认上游实际完成了该模型请求。

## 隔离与脚本审查

- 脚本使用 TemporaryDirectory，数据库为该目录内的 a07.sqlite3，与配置中的正式数据库路径不同；新增断言检查这一点。
- create_app(settings) 显式使用隔离 settings，HTTP 监听仅绑定 127.0.0.1 的随机端口。
- 项目及反馈均标记为 test；管理凭据和 PUBLIC_BASE_URL 在内存中替换为测试值。未打开或访问真实反馈库。
- 已修正脚本写死旧供应商名称及覆盖旧证据的问题。本次目录按 UTC 时间与随机后缀独立创建。
- 补充 Plan、Statistics、Report 及 Markdown 下载/持久快照一致性检查；本次只执行到了生成请求，这些后续检查未运行。
- 无自动重试，未修改后端适配器、认证、业务接口、依赖或 40 秒模型等待预算。

## 实际结果

| 阶段 | 结果 | 耗时 |
| --- | --- | ---: |
| 创建 test 项目 | HTTP 201，通过 | 0.062 秒 |
| 真实模型生成计划 | HTTP 504，LLM_TIMEOUT，失败 | 40.047 秒 |
| 发布 | 未执行 | — |
| 提交受控回答 | 未执行 | — |
| 统计 | 未执行 | — |
| 真实模型复盘 | 未执行 | — |
| 报告 JSON | 未执行 | — |
| 报告 Markdown 与 stale 快照 | 未执行 | — |

脚本内总耗时 **40.562 秒**。已执行 HTTP 阶段：**1 成功、1 失败**。
模型阶段：**1 次尝试、0 次成功、1 次超时**；复盘模型请求次数为 0。
持久 run：**1 个 failed**，错误码 LLM_TIMEOUT；make_validation_plan 工具状态 failed。
运行 ID：`890b3119-8ab8-4cfe-be51-cb32e8e24bb0`。

工作流队列最多等待 40 秒；该失败记录没有模型 CallInfo，tool.output_ref 为 null。
因此上游最终 HTTP 状态、用量和响应格式未知，不能将空 model_calls 列表解释为“模型失败 0 次”。
result.json 已在运行后增加明确的阶段尝试/失败计数，将缺失完整元数据的 model_call_failure_count 标记为 null；未为此再次请求模型。

## 输出契约与证据

没有得到可供验证的方案输出，不能判断新供应商是否符合 Chat Completions、Plan 或 Review 输出契约。
本次观察到的是 LLM_TIMEOUT，不是 LLM_OUTPUT_INVALID。未生成新报告，故本目录没有 report.md。
本次证据：[result.json](result.json)。语法检查与证据断言检查通过。

旧供应商的三个文件均经 SHA256 对比确认未改变：

- [a07-real-model-result.json](../../a07-real-model-result.json)
- [a07-real-model-verification.md](../../a07-real-model-verification.md)
- [a07-real-report.md](../../a07-real-report.md)

未开展浏览器、手机或真实用户试用；实际 TCP 冒烟只使用测试项目。
未自动提交、推送或重试。后续需检查供应商响应延迟与现有 40 秒预算的适配情况，再由用户决定是否重跑。
