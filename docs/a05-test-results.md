# A05 验收与成员 B 交接

验收日期：2026-10-10。只实现 A05；六张表、已有 JSON 字段、项目派生状态、A01 身份认证、A03 模型适配器、A04 工作流均未更改。

## 实际结果

```powershell
Set-Location 'D:\AI+比赛\firstorder-lab'
python -m pytest tests/test_a05.py -v
python -m pytest -q
python smoke_a05.py
```

- A05：44 passed，4.77 秒。
- 全量：280 passed，20.74 秒。保留现有 Starlette TestClient/httpx 弃用提示（1 warning），没有升级冻结依赖。
- HTTP 冒烟：实际 Uvicorn TCP 监听、httpx 网络请求、临时 SQLite，15 项检查全部通过，退出码 0。
- 首次提交 HTTP 201：`{"data":{"response_id":1,"submitted_at":"2026-10-10T05:05:06.536916+00:00","duplicate":false}}`。
- 多选顺序相反的重放 HTTP 200，response_id 不变；不同答案重放 HTTP 409；数据库只有 1 条回答。
- SQLite 重新连接及 HTTP 服务重新启动后，发布时间、分享 URL、回答和幂等结果仍存在。
- 草稿为总纲第 7.2 节的明确 test 格式样例，直接通过现有仓库层创建；本次模型调用次数为 0，未创建虚构的 Agent 成功日志。这是 A05 真实 HTTP/数据库验收，不是新增 A04 真实模型验收。
- 未读取或修改真实 `.env` 凭据；未使用正式数据库、真实参与者数据或新依赖。

## 已接入接口

| 路由 | 权限 / 返回 |
| --- | --- |
| GET /api/experiments/{experiment_id} | 管理员；完整实验，草稿公开 URL 为 null |
| PATCH /api/experiments/{experiment_id} | 管理员、受控 Origin、固定 X-Requested-With；完整 landing/questions |
| POST /api/experiments/{experiment_id}/publish | 同上；空对象；幂等人工发布 |
| GET /experiments/{experiment_id}/edit | 管理员；experiment_edit.html |
| GET /experiments/{experiment_id}/preview/service | 管理员；service.html；is_preview=true |
| GET /experiments/{experiment_id}/preview/survey | 管理员；survey.html；is_preview=true、submit_url=null |
| GET /e/{public_token} | 无需登录；只允许已发布 token；service.html |
| GET /e/{public_token}/survey | 无需登录；只允许已发布 token；survey.html |
| POST /api/public/e/{public_token}/responses | 无需登录、无需管理写入头；首次 201、重放 200 |

编辑冻结题目/选项 ID、题型、数量和顺序；允许显示文本和 required 修改。更新计划内 landing/questions 与实验内容在同一短事务提交，保留假设等其他计划字段。编辑、发布在 BEGIN IMMEDIATE 后读取运行状态，避免与 A04 生成交错；公开回答提交不因 Agent 运行而拒绝。

提交依据数据库的已发布题目校验。可选空文本/空多选省略，多选排序，重复选项拒绝；事务包含读取、幂等比较和 INSERT，UNIQUE(experiment_id,client_submission_id) 保留数据库兜底。错误统一 JSON，不返回 SQL、敏感输入或堆栈。

## 成员 B 模板依赖（尚未完成）

当前 app/templates 只有 .gitkeep，尚缺 experiment_edit.html、service.html、survey.html 及配套 JS/CSS。

- 编辑模板上下文：experiment（与 GET API 相同）、running_run_id、is_locked（已发布只读）。运行中禁用写操作，服务端仍独立执行锁校验。
- 服务/问卷模板上下文：landing、questions、is_preview、service_url、survey_url、submit_url，以及 Jinja 的 request。
- 预览不传公开 token，submit_url 为 null；B 必须按 is_preview 禁用提交控件和事件。公开上下文不传 project、原始回答、运行日志或管理员链接。
- 保持 Jinja 默认自动转义；不要使用 safe 或把模型文本写入 innerHTML。
- 问卷需实现三种题型、知情说明、UUID 生成与重试沿用、错误展示和提交状态；预览不得触发实际提交。

**真实公开页结果为 HTTP 500 / INTERNAL_ERROR，提示成员 B 模板尚未交付。** 这不是公开页面完整通过的结果。自动化测试只在临时目录注入测试模板验证 200、权限和转义，没有写入正式模板。T04 的正式发布后公开 200、T12 的最终模板安全和手机/浏览器操作需在 B 交付后补验。

## 启动

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

正式启动使用现有本地配置；A05 冒烟脚本独立启动临时端口及数据库，不依赖已运行服务、不调用付费模型。只支持总纲既定单进程、单 worker。

未实现 A06 原始反馈读取/统计、A07 复盘、正式前端和真实参与者试用；未改变冻结技术契约。公开 UUID 幂等只能防重试重复，不能证明独立参与人数。
