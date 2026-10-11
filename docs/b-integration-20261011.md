# B 前端差异集成记录（2026-10-11）

实际分支 `feat/a-backend`，HEAD `28615b60cda77da41cecd39524dcb7a1ce44115e`。
检查了工作区、祖先目录及仓库内的 AGENTS.md，未发现适用文件。
既有后端修改、未跟踪的复盘实现、文档和测试均保留，本轮没有开发 A07。
没有提交、推送或合并。

## 交付来源与审查

用户确认使用 `D:\AI+比赛\B_frontend_delivery_20261011.zip`（实际找到的文件不含 `(1)`，58,430 字节）。
SHA256：`0A9AAC1B91913678AF11EB450FBE00B1DF56BC19C074D399046576C9B9922E80`。
独立解压目录：`C:\Users\周飞鹏\AppData\Local\Temp\firstorder-b-7122d4732ac2458da05b0cae46cf912c`。
已检查包内 `B_交付说明.md`、`B_改动明细.diff` 和全部交付文件，并与当前文件逐项比较。

B 基于旧版 3f79df9；其“管理路由/A06 缺失”和首页纯文本的说明过时。
没有采用其路由代码，没有重复注册路由，没有整目录覆盖。
对既有三个前端文件使用逐文件差异补丁；新模板独立新增。
交付说明及原始 diff 留在解压目录，避免把过时说明作为当前项目契约。

## 本轮修改文件

| 文件 | 内容 |
| --- | --- |
| `app/templates/projects.html` | 新增项目列表，使用现有正式首页的 projects context |
| `app/templates/project_new.html` | 新增创建项目表单，字段匹配 ProjectCreate |
| `app/templates/workspace.html` | 新增工作台，方案、草稿、人工发布、分享链接、运行日志；统计/复盘/报告明确禁用 |
| `app/templates/survey.html` | 增加安全 JSON 题目属性与字段错误容器 |
| `app/static/app.js` | 纳入三页交互与问卷修复，保留草稿编辑功能；处理空预算、禁用存储、错误后状态刷新和发布只读入口 |
| `app/static/app.css` | 纳入 B 新增样式；窄屏保留数据类型和题干，并允许长文本换行 |
| `tools/check_pages_live.py` | 纳入并改成检查正式路由，删除运行时挂载，验证当前 API 与 A06；隔离数据库，禁止模型 HTTP POST |
| `tools/check_pages_render.py` | 纳入静态模板契约检查，修正过时 A06 文案断言 |
| `tools/probe_survey_render.py` | 纳入 public/preview 渲染脚本；契约标记不符合预期时返回失败 |
| `tools/probe_survey_js.mjs` | 纳入并加强模拟 DOM；修正初始 hidden、单选互斥、节点删除，增加滚动/必答/同意/409/幂等/禁用存储检查 |
| `tools/probe_management_js.mjs` | 新增小型管理交互自检，模拟 DOM/fetch，覆盖创建/预算/发布/任务冲突 |
| `docs/b-integration-20261011.md` | 本记录 |

对比集成前快照，`app/main.py`、`app/repositories.py`、`app/schemas.py`、
`app/services/workflow.py`、`app/services/reports.py`、`app/services/reviews.py`、
`app/prompts/review.txt` 的 SHA256 全部相同。
原问卷模板 10 个、原 JS 9 个不同的 data-testid 字符串全部保留。

## 最终验证结果

| 类别 | 命令/检查 | 通过 | 失败 |
| --- | --- | ---: | ---: |
| 语法 | `node --check app/static/app.js` | 通过 | 0 |
| 后端测试 | `conda run -n firstorder-lab python -m pytest -q --tb=short --basetemp evidence/pytest-b-integration-20261011` | 327 | 0 |
| TestClient 回归 | `conda run -n firstorder-lab python docs/verify_templates.py` | 34 | 0 |
| 静态 Jinja 渲染 | `conda run -n firstorder-lab python tools/check_pages_render.py .` | 40 | 0 |
| 正式路由 TestClient | `conda run -n firstorder-lab python tools/check_pages_live.py` | 35 | 0 |
| 渲染探针输入 | `conda run -n firstorder-lab python tools/probe_survey_render.py app/templates evidence/b-integration-20261011`，再带 `--preview` | 两次退出码 0 | 0 |
| 模拟 DOM：public | `node tools/probe_survey_js.mjs evidence/b-integration-20261011/survey_public.html app/static/app.js` | 23 | 0 |
| 模拟 DOM：preview | 同上，输入 `survey_preview.html` | 4 | 0 |
| 模拟 DOM：管理页 | `node tools/probe_management_js.mjs` | 15 | 0 |
| 差异格式 | `git diff --check`，另检查本轮新增文件 | 通过 | 0 |
| 真实浏览器 | 未执行 | — | — |
| 真实手机 | 未执行 | — | — |

pytest 数量包含当前工作区已有测试（含已有 A07 测试），不是旧记录的 301 项。
最终 pytest 有 1 条 Starlette/httpx 弃用警告；没有为此更换依赖。
TestClient 验证正式 HTML 路由和 API，不能证明浏览器执行 JS；两类 JS 探针均模拟 DOM/fetch。
管理探针中的生成请求也只是模拟请求，不调用真实模型。

首次全量 pytest：44 通过、282 个 setup 错误，原因是 Windows 默认 pytest 临时目录权限。
按执行权限流程在沙箱外、工作区独立临时目录重跑后 327/327 通过。
live 脚本的一次中间运行在执行检查前失败：新增 socket.connect 拦截误伤 Windows asyncio 自连接。
已改为在实际模型调用入口 httpx.post 拦截，最终 35/35；这不是后端接口失败。

## 当前契约与待联调

- 正式 `GET /`、`GET /projects/new`、`GET /projects/{project_id}` 均已有且只注册一次，继承管理鉴权；新建静态路由顺序正确。
- 创建请求使用七个 ProjectCreate 字段，读取 `data.project_id` 跳转。空预算被拒绝，0 可用；服务端仍负责最终校验。
- 工作台使用详情的 `project/plan/experiment/latest_report_meta/running_run_id`，运行列表和详情使用 `/api/projects/{id}/runs`、`/api/runs/{id}` 的现有契约。
- 人工发布发送 `{}` 到 `/api/experiments/{id}/publish`，分享链接来自后端。已发布禁止重生成，编辑入口注明只读。
- 问卷发送合法 UUID、`consent: true`、单选字符串/多选数组/简答文本；可选空题省略。保留重试、409、422 及预览禁用语义。
- 管理写请求带同源凭据和 `X-Requested-With: FirstOrderLab`。实际访问 Origin 必须与 PUBLIC_BASE_URL 同源，否则后端返回 403；这仍需浏览器联调。
- **A06 后端已存在，工作台尚未接入。** 后续读取 `/api/experiments/{id}/statistics` 的 `response_count/questions`，以及 `/responses` 原始反馈；按各题 `answered_count` 显示分母，零分母 percentage 为 null，不伪造数字。
- 统计接入需覆盖未发布 400、权限失败、零反馈、测试样例标记与刷新。当前 statistics-card 内四个按钮全部 disabled，JS 不请求统计/复盘接口。
- 复盘/报告页面及导出入口后续单独联调；本轮没有启用工作区已有 A07 实现，也没有新增 report.html。
- 待做真实浏览器与 320–430px 手机验证、Basic Auth 会话/Origin、剪贴板与隐私模式。存储成功标记只代表同一浏览器，不代表独立用户；禁用存储后刷新无法保留收条。

所有新脚本使用临时 SQLite 或静态夹具，data_kind=test，模型 URL/凭据使用测试占位值。
本轮未连接真实模型、未触碰真实反馈库、未输出密钥。
