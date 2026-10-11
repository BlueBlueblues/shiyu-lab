# 用户系统升级验收记录

日期：2026-10-11。分支 `feat/a-backend`，HEAD `c6c6dbc9cbeba2c1cf63b208ab2ce972d9d66209`。
本记录对应未提交工作区，包含此前公开首页改动；不代表服务器已部署用户系统。

## 分阶段结果

| 步骤 | 主要文件 | 数据库变化 | 检查与原流程影响 |
|---|---|---|---|
| 1. 迁移设计与实现 | app/db.py、docs/user-system-migration.md、tests/test_users.py | users/sessions、projects.user_id、索引及旧数据归属触发器；首次旧库迁移前备份 | 4 项测试通过：原六表记录保留、重复执行、备份、失败回滚、外键；只使用临时库 |
| 2. 用户模型与密码 | app/users.py、app/passwords.py | 密码 hash 和独立随机盐 | 模型校验及密码测试通过；用户名唯一且不区分 ASCII 大小写，邮箱可选；注册密码 12–128 位 |
| 3. 注册、登录、退出 | app/auth_routes.py、app/auth.py、app/main.py | Session 令牌摘要和 12 小时绝对期限 | 身份模块及原 A01 共 52 项通过；错误密码、同源校验、轮换、过期、撤销、限流已验证 |
| 4. 项目隔离 | app/auth.py、app/repositories.py、app/main.py | API 创建显式设置当前用户，不接受客户端 user_id | 身份与管理页面 28 项通过；17 个资源入口越权返回 404，不调用工作流；公开问卷仍可匿名提交 |
| 5. 页面入口 | app/templates/home.html、login.html、register.html、profile.html、base.html；app/static/app.js、app.css | 无额外业务表变化 | 身份及页面 30 项通过；登录/注册/退出模拟 DOM 13 项通过 |
| 6. 回归验收 | tests/test_users.py、tests/test_a01.py、tests/test_a02.py、tests/test_management_pages.py；相关 tools/docs 检查 | 原业务表无重建、无删除 | 身份升级快照 455 通过；保留并行 JSON 改动后的最终工作区 468 通过、0 失败、1 跳过，300.03 秒 |

各阶段数字有重复用例，不相加为独立用例总数。

## 最终检查

- 身份升级快照的 `conda run --no-capture-output -n firstorder-lab python -m pytest -m "not e2e" -q`：455 通过，0 失败，1 模块跳过，299.87 秒。C 的浏览器骨架未设置隔离 Mock 服务环境，未执行。原跳过文案仍含旧“报告待交付”说明，不能据此认定当前报告页缺失。
- 保留并行 JSON 解析改动后的当前工作区，同一命令再次回归：468 通过、0 失败、1 跳过，300.03 秒；新增的 13 个解析相关用例纳入本次检查。测试前后核对 36 个变动源码文件 SHA-256，未发现测试期间再次变化。
- `conda run -n firstorder-lab python docs/verify_templates.py`：35 通过，0 失败。
- 新用户完整业务链路（含预览页隐藏账号信息的最终断言）单独复验：1 通过。
- `conda run -n firstorder-lab python tools/check_a07_report_flow.py`：64 通过，0 失败，2 次 Mock 复盘调用。
- 模拟 DOM：认证 13、管理 17、工作台统计 21、复盘 17、公开问卷 23、预览问卷 4，共 95 个检查通过，0 失败。模拟 DOM 不能代替浏览器验收。
- 实际 TCP/Uvicorn：25 检查通过，0 失败；独立临时库，2 次 Mock 模型调用，真实模型调用 0 次。覆盖注册、登录、双用户隔离、创建、计划、发布、匿名填写、统计、复盘、JSON、报告页面、Markdown 和退出。
- TCP 脚本及结果保存在仓库外：`D:\AI+比赛\firstorder-users-20261011-check.py`、同名 `.json`；模拟 DOM 输入在 `D:\AI+比赛\firstorder-users-probes-20261011\`。
- `node --check app/static/app.js` 与 `git diff --check`：通过。
- 一条既有 Starlette TestClient 的 httpx 弃用警告保留，本轮不修改依赖。

## 修复与契约兼容

首轮完整回归 451 通过、4 失败、1 跳过：数据库比较未排除新增 user_id，以及权限层改变了“项目不存在。”文案。已增加 user_id 归属断言并恢复原错误文案；相关 5 项针对性复验通过，第二轮全量通过。另按新增身份表更新 A01 表清单，按 Session + Basic 更新 OpenAPI 认证描述断言，原业务字段及行为断言保留，C 测试不改。

前端认证最初使用旧统一错误映射，掩盖“用户名或密码错误”，已改为优先显示认证接口的脱敏提示。最初模板检查 34/35，通过补充公共布局条件修复：服务页与问卷（含管理预览）不显示用户名或个人空间导航；最终 35/35。

本身份升级保留 c6c6dbc 的业务请求/响应字段、数据库业务表约束、工作流、Prompt、LLM 客户端、模型配置及时间预算。不修改 .env、生产数据库、systemd，不新增依赖，不调用真实模型，不提交、推送或部署。

收尾时发现 20:22:26 并行出现的另外六个差分：`app/services/llm_client.py`、`app/services/planning.py`、`app/services/reviews.py`、`tests/test_a03.py`、`tests/test_a04.py`、`tests/test_a07.py`。内容是模型 JSON 提取及对应测试，本身份升级未编辑这些文件，未回退它们。455 通过记录属于这些修改出现前的快照；当前工作区合并后的回归单独记录，不能混用。

最终当前工作区重新回归 468 通过，以上六个文件保留并通过相关自动检查；这不将它们归为本身份升级的代码修改，也不代表进行过新的真实模型验收。LLM 客户端最后记录修改时间为 20:23:29，最终回归的源码哈希快照已包含该版本。

## 发布边界与下一步

1. 先在隔离环境人工验证桌面及手机注册/登录/退出、表单提示与项目权限；本轮没有真实浏览器或真机证据。
2. 当前公网地址使用 HTTP，开放真实账号前必须部署 HTTPS，并核对 PUBLIC_BASE_URL；此后 Cookie 自动使用 Secure。本轮未更换生产配置。
3. 生产升级先停服并确认备份，按迁移说明执行和检查旧项目归属。升级后有新数据时不能直接用旧备份覆盖。
4. 当前认证限流为单进程、每 IP 和用户名每分钟各 10 次注册/登录请求；共享网络会共用 IP 配额，多 worker/实例前需共享限流。旧 Basic 自动化入口尚保留，上线时需评估保留范围与额外限流。
5. 账号密码找回、邮箱验证、MFA、用户管理均不在本轮范围；默认管理员首次从已有 ADMIN 配置导入，配置变更不会自动重置数据库密码。

未提交历史文件 `docs/delivery-history/bc-28615b6-20261011/C_changes.diff` 和 `firstorder-lab-final-8eb4136.zip` 原样保留。

## 工作区文件清单（含前轮公开首页修改）

- `.gitignore`、`README.md`
- `app/auth.py`、`app/auth_routes.py`、`app/db.py`、`app/main.py`、`app/passwords.py`、`app/repositories.py`、`app/users.py`
- `app/static/app.css`、`app/static/app.js`
- `app/templates/base.html`、`app/templates/home.html`、`app/templates/login.html`、`app/templates/register.html`、`app/templates/profile.html`、`app/templates/projects.html`、`app/templates/project_new.html`、`app/templates/workspace.html`、`app/templates/report.html`
- `docs/browser_e2e.py`、`docs/verify_templates.py`、`docs/user-system-migration.md`、`docs/user-system-verification-20261011.md`
- `tests/test_a01.py`、`tests/test_a02.py`、`tests/test_management_pages.py`、`tests/test_users.py`
- `tools/check_a06_pages_live.py`、`tools/check_a07_report_flow.py`、`tools/check_pages_live.py`、`tools/probe_auth_js.mjs`、`tools/probe_management_js.mjs`、`tools/smoke_full_chain.py`

另保留上述六个并行模型解析差分，未提交历史差分与 ZIP 不在本身份升级文件清单内。
