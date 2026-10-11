# A07：反馈复盘、报告快照与 Markdown 导出

开发日期：2026-10-11。分支 feat/a-backend，开始 HEAD 28615b60cda77da41cecd39524dcb7a1ce44115e；初始工作区干净，未发现适用 AGENTS.md。未修改六张表、既有字段契约、模板、静态资源或冻结依赖。

## 接口

以下路由全部要求现有管理员 HTTP Basic；POST 继续要求受控 Origin 与 X-Requested-With: FirstOrderLab。

| 路由 | 请求 / 返回 |
| --- | --- |
| POST /api/projects/{project_id}/review | 空 JSON 对象；返回 report_id、run_id、response_count、cutoff_response_id |
| GET /api/projects/{project_id}/report | 最新成功报告；固定字段见下面示例 |
| GET /api/projects/{project_id}/report.md | 最新报告的 Markdown 附件；UTF-8，文件名 firstorder-report-报告UUID.md |
| GET /projects/{project_id}/report | report.html，管理员页面 |

请求与返回示例（ID 为说明性占位，不冒充实际运行证据）：

```http
POST /api/projects/项目UUID/review
Authorization: Basic <本地管理员凭据>
Origin: <受控 PUBLIC_BASE_URL 的同源 Origin>
X-Requested-With: FirstOrderLab
Content-Type: application/json

{}
```

```json
{"data":{"report_id":"报告UUID","run_id":"运行UUID","response_count":1,"cutoff_response_id":12}}
```

报告 GET 的 data 固定为：

```text
{
  id: 报告UUID,
  created_at: UTC ISO 8601,
  response_count: 整数,
  cutoff_response_id: 整数,
  statistics: 总纲7.4 / 现有Statistics模型,
  review: 总纲7.5 / 现有Review模型,
  stale: 布尔值,
  data_kind: real或test
}
```

项目不存在返回 404 / NOT_FOUND；未发布复盘返回 400 / INVALID_STATE；同项目正在运行返回 409 / RUN_IN_PROGRESS。无成功报告时 GET API 与附件返回统一 404。非法请求 422、认证缺失 401、非法管理写入来源 403；模型上游/输出错误 502、超时 504；失败错误附 run_id 供查询持久日志。不会返回模型原始文本、SQL 或堆栈。

## 执行与快照

1. 在短写事务中检查发布状态和同项目运行锁，插入 action=review 的真实 run。
2. 插入 analyze_experiment 工具开始日志，提交后读取冻结题目、计划和回答快照。
3. 使用 A06 统计函数；同一读取事务保证一致性，关闭连接后才请求现有模型适配器。
4. 系统提示词固定，项目/假设/统计/文字反馈作为 user 消息中的 JSON 数据。文字反馈不是执行指令。
5. json.loads、Review Schema 与证据引用校验都通过后，才在同一短事务新增报告并完成工具/run。报告永不覆盖旧报告。
6. finally 失败清理更新真实错误日志并释放运行锁。保留原成功报告，支持用户手动重试。

保持单次模型等待40秒、工作流60秒预算、无自动重试。等待超时丢弃晚到结果；模型线程不访问数据库。

假设必须 h1/h2/h3；题号必须来自冻结题目；option_id 属于该题；response_id 必须是本次快照指定简答题的实际文字回答 ID。拒绝不存在、错误题型或快照以外的引用，不自动修复引用。

零回答仍请求并校验模型，不用示例数据替代失败。合法输出的所有假设由程序强制为 insufficient_evidence，原因/摘要明确零反馈，清空关键反馈与证据引用，追加证据不足限制。少量样本及意向不能证明商业成功的要求同时写入提示词；语义质量仍需人工审阅。

报告 GET 的 stale 对照当前回答数量与最大 ID；项目详情/列表复用原有派生状态逻辑。报告后新增回答时 collecting，重新复盘成功纳入新快照后 reviewed。若复盘等待期间新增回答，本次报告可以刚生成就 stale=true，新增数据留给下一次复盘。

Markdown 只从已保存 statistics/review 生成，包含 real/test、UTC时间、run_id、回答份数及截止ID、每题分母、全部选项与百分比、文字反馈、结论、证据、局限和下一步行动。新增回答不改变旧快照的导出文本。用户/模型内容作为文本转义，不作为 HTML 或 Markdown 链接执行。

## 报告页面 context（待 B）

```text
{
  project: 与项目详情API的project相同,
  report: 与报告GET的data相同；无报告为null,
  running_run_id: 当前运行UUID或null
}
```

Jinja 另提供 request。project 含派生状态与 data_kind，不含 plan_json。无报告不是不存在的项目；B 用 report=null 展示“尚无成功复盘报告”，不显示空白成功结果。报告 stale=true 时显示旧快照使用的回答数，提醒已有新反馈。

report.html 当前尚未交付：路由已接线、页面待联调，实际页面会返回既有明确的 500 模板缺失提示。测试使用临时模板确认空状态/context/认证，不代表正式页面验收。B 不应使用 safe 或 innerHTML 执行模型或用户文本。

## 验证

所有验证使用临时 SQLite、data_kind=test 和 Mock HTTP 模型响应。日志由真实被测工作流写入，不灌入成功日志。未访问正式反馈库、使用或输出真实密钥，也未请求真实模型。

```powershell
conda run -n firstorder-lab python -m pytest tests/test_a07.py tests/test_a06.py tests/test_a04.py -q
conda run -n firstorder-lab python -m pytest -q --ignore=tests/e2e
conda run -n firstorder-lab python docs/verify_templates.py
git diff --check
```

相关测试：81 passed，1 warning（15.02秒）。覆盖未发布、权限、零反馈、证据引用、旧报告stale与重跑、失败/非法输出/超时、原子保存失败回滚、复盘期间新增回答及所有同项目写操作锁、Markdown快照一致与转义、重启持久化、报告页context与模板缺失。

非浏览器回归：327 passed，1 warning（33.12秒）；模板验证：34/34通过，退出码0；git diff --check通过。现有 httpx 弃用警告不通过升级依赖处理。Windows Conda 并行启动时发生一次临时文件占用，模板验证已串行重新执行成功。

开发过程中工作区出现了其他来源的管理模板、静态资源、survey.html及tools/改动，本轮未修改或覆盖这些内容。发现后再次串行验证：非浏览器回归327 passed（29.29秒），模板34/34通过。

## 剩余验收

- 本轮未做真实模型复盘验收，Mock 通过不是供应商成功证据；后续需隔离 test 项目做真实调用，并人工检查内容质量。
- 未开展真实用户试用。
- 未发现 tests/e2e 浏览器测试目录；浏览器E2E与报告正式模板集成待B/C单独交付执行，不混入普通回归测试。
- 单进程、单worker约定保持不变；报告读取不分页，沿用小样本P0边界。
- 未提交、推送或合并main。
