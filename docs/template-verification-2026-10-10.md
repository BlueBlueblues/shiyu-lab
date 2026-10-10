# 模板交付与联调验证结果（B/C 交付）

交付人：C（临时接手模板交付）｜时间：2026-10-10
依据：`docs/模板交付需求.md`｜A 的请求：「交付草稿编辑、服务展示和问卷页面的 Jinja2 模板，并与已有路由联调」
基线分支：`origin/feat/a-backend`（`cd4f817 feat: implement A05 experiment publishing and responses`）

> **一句话结论：6 个交付文件全部完成，用真实路由渲染验证 34 项断言，通过 33 项。**
> 唯一失败项 **不是模板问题，而是 A 的 `/health` 响应多了一层 `data` 包装**（见 §4 BUG-001）。

---

## 1. 交付物

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `app/templates/base.html` | 42 | 公共骨架：viewport、样式/脚本引用、`nav` block（公开页覆盖为空） |
| `app/templates/experiment_edit.html` | 150 | 草稿编辑：只允许文本/选项 label/required；已发布自动只读 |
| `app/templates/service.html` | 60 | 服务展示页：预览与公开共用，`is_preview` 控制标识与 CTA |
| `app/templates/survey.html` | 121 | 问卷页：三类题、必答标记、知情说明、提交成功区 |
| `app/static/app.css` | 约 380 | 手机优先，320~430px 不横向溢出，点击区域 ≥44px |
| `app/static/app.js` | 约 370 | 统一请求封装、错误码映射、UUID 幂等提交、预览禁用 |

**同时修复**：`requirements.txt` 原为 UTF-16 LE（BOM `FF FE`，记事本另存导致），
已转为 UTF-8 无 BOM，内容未改（26 个包）。

**未触碰**：任何后端 API 路径、入参、返回结构、错误码、schema（严格按 A 的契约写模板）。

---

## 2. 与 A 的真实代码对齐情况

模板完全按 `app/main.py` 的真实 `render_template(...)` 与 context 编写，不是猜测：

| 模板 | 路由 | 真实 context（来自 `app/main.py`） |
| --- | --- | --- |
| `experiment_edit.html` | `GET /experiments/{id}/edit` | `experiment`（含 `id`/`landing`/`questions`）、`running_run_id`、`is_locked` |
| `service.html` | `GET /experiments/{id}/preview/service`、`GET /e/{token}` | `landing`、`questions`、`is_preview`、`service_url`、`survey_url`、`submit_url` |
| `survey.html` | `GET /experiments/{id}/preview/survey`、`GET /e/{token}/survey` | 同上（`submit_url` 公开时非空、预览时为 `None`） |

**确认的事实（原「待 A 确认」的问题已由代码本身回答）**：

1. **模板名字符串**：`"experiment_edit.html"` / `"service.html"` / `"survey.html"`，
   `template_folder` 为 `app/templates`（`main.py:173`）。✅ 与假设一致。
2. **传的是 `submit_url`**（完整地址），不是 `public_token`：
   `main.py:92` 拼成 `{PUBLIC_BASE_URL}/api/public/e/{token}/responses`；预览时为 `None`。
3. **`is_preview=true`**：模板显示「草稿预览，不能提交」并禁用提交（`data-testid="preview-banner"`）。
4. `static` 目录挂载在 `/static`（`main.py:174`），模板用 `url_for('static', path='/app.css')` 引用。

**保存时结构如何保持**：`experiment_edit.html` 通过 `tojson` 把服务端的 `questions` 结构交给
`app.js`，保存时只替换可编辑文本、原样回传 `id`/`type`/`options` 顺序，
因此不会触发 A 的结构校验（`services/experiments.py:63`）。

---

## 3. 验证方式与结果（34 项断言，33 通过）

**验证脚本**：`docs/verify_templates.py`（一次性联调脚本，可保留为回归用例）

**执行方式**：不依赖真实模型 —— 用 `TestClient` 走真实路由 + 直接种入契约格式的
`data_kind=test` 数据（项目 + 未发布实验），全程不触碰真实数据库、不调用模型。

```powershell
# 本机临时环境（3.13.7 虚拟环境，依赖与 requirements.txt 一致）
.\.venv\Scripts\python.exe docs\verify_templates.py
```

### 3.1 通过项（33）

| 分组 | 断言 |
| --- | --- |
| 草稿预览可访问 | 编辑页 200 且含 `experiment-save`；服务页预览 200 且含 `preview-banner`；问卷预览 200 且含 `preview-banner` |
| 预览保护 | 预览问卷提交按钮带 `disabled`；预览页无 `run-log`/`response-count`/`admin` |
| **发布前必须 404** | `GET /e/{token}` → 404；`GET /e/{token}/survey` → 404 |
| 草稿编辑 | `PATCH` 保存 200；改题号 → 422（结构锁定生效） |
| 人工发布 | `POST publish` 200，返回 `published_at` 与 `service_url`/`survey_url` |
| **发布后 200** | `GET /e/{token}` → 200 且含 CTA 与验证声明；`GET /e/{token}/survey` → 200 |
| 发布后锁定 | `PATCH` → 409 `EXPERIMENT_LOCKED`；编辑页显示只读标识 |
| **三种题型** | 问卷含 `question-q1..q4`、`survey-consent`、`submission-success`；渲染公开提交地址 |
| 提交入库 | 三类题提交 → 201 + `response_id`；数据库实际入库 1 条 |
| 幂等 | 同 UUID 同内容 → 200 且 `duplicate=true`；同 UUID 不同内容 → 409 |
| 非法输入 | 非法选项 → 422；必答缺失 → 422；`consent=false` → 422；均不入库 |
| 权限隔离 | 未登录管理页 401；未登录读实验 401；缺 `X-Requested-With` 写入 403 |
| 公开页不泄露 | 公开服务页无 `run-log`、无 `admin` 字样 |

### 3.2 未在本次验证覆盖（需真实浏览器 / 手机）

| 项 | 原因 | 计划 |
| --- | --- | --- |
| 手机端实际显示（375×667、320×568） | 需真实浏览器渲染 | C04 用 Playwright 截图；C03 用真机 |
| 前端 JS 交互（保存按钮恢复、UUID 重发） | `TestClient` 不执行 JS | C04 E2E 覆盖 |
| 真实模型生成草稿 | 需要模型账号与额度 | A 的 A03 冒烟 + C 的 `scripts/smoke.py` |

---

## 4. 发现的后端缺陷

### BUG-001（S1，契约不符，阻塞 T-健康检查与 `smoke.py`）

| 项 | 内容 |
| --- | --- |
| 现象 | `GET /health` 返回 `{"data": {"status": "ok"}}`，多了一层 `data` 包装 |
| 预期 | `{"status": "ok"}`（总纲 §6.2；C 的 `tests/test_api.py:44` 明确断言；`scripts/smoke.py` 也依赖） |
| 位置 | `app/main.py:219-221` |
| 最小重现 | `python -c "from fastapi.testclient import TestClient; from app.main import create_app; print(TestClient(create_app()).get('/health').json())"` → `{'data': {'status': 'ok'}}`（注意需进入 lifespan） |
| 影响 | 健康检查是**唯一不需要管理员身份**的接口。破坏它会让 C 的 `T13 新环境启动验证` 与 `smoke.py` 判定失败；外部探活脚本也不认 |
| 建议修复 | `health()` 直接 `return {"status": "ok"}`；或给该路由加 `response_class=JSONResponse` 并绕过 `DataResponse` 包装 |
| 负责人 | A |
| 是否阻断 | 阻断 C 的 C01/T13「按 README 启动并看到 /health 200」的判定 |

**其余 33 项说明 A 的 A01~A05 实现质量良好**，尤其是发布锁定、幂等冲突、权限同源校验都按契约工作。

---

## 5. 验收清单对照（来自 `docs/模板交付需求.md` §H）

| A 提出的重点验收 | 结果 |
| --- | --- |
| 草稿预览可访问 | ✅ 编辑页/服务页预览/问卷预览均 200 |
| 人工发布后服务页和问卷页返回 200 | ✅ 均已验证 |
| 三种题型能填写 | ✅ 渲染与提交值格式均正确（单选字符串/多选数组/简答文本） |
| 提交成功后有明确反馈 | ✅ `submission-success` 区 + 时间戳；重放提示「未重复记录」 |
| 手机端正常显示 | ⏳ 已在 CSS 层保证（320~430px、无横向溢出、点击区 ≥44px），**待浏览器/真机确认** |
| 发布前仍必须返回 404 | ✅ 已验证 |
| 公开页面不能显示管理员数据 | ✅ 已验证 |

---

## 6. 待办与后续

| 项 | 负责人 | 说明 |
| --- | --- | --- |
| 修 BUG-001（`/health` 去掉 `data` 包装） | A | 一处改动，改完 C 立即复跑 |
| 合入 `feat/a-backend` | A | 模板需要与后端同分支才好联调 |
| `is_preview` 的页面文案与 A 的预期是否一致 | A 确认 | 现为「草稿预览：这是未发布草稿的预览，受访者无法提交，也不能分享。」 |
| `gitignore` 是否放开 `evidence/reports/` | A 决定 | 整目录被忽略会让测试证据无法提交（总纲 §10 要求可举证） |
| 手机端真机验证（T05） | C | A 修完并合并后执行 |
| Playwright 全链路（T11） | C | 需安装 Playwright + chromium |
| **A06 统计 / A07 复盘 / 报告导出** | A | 当前 `app/main.py` 没有 `statistics`、`review`、`report.md` 路由；工作台与报告页也要靠它 |

---

## 7. 环境记录（本次验证）

| 项 | 值 |
| --- | --- |
| 解释器 | CPython **3.13.7**（Windows AMD64） |
| 依赖 | 与 `requirements.txt` 一致：fastapi 0.143.0 / jinja2 3.1.6 / pydantic 2.14.0 / httpx 0.28.1 |
| 数据库 | 每个用例独立临时 SQLite（`DATABASE_PATH` 指向系统临时目录），**未触碰真实数据库** |
| 模型调用次数 | **0**（全程 mock/直插夹具数据） |
| 契约版本 | 以 `origin/feat/a-backend` 的 `app/` 为准 |

> ⚠️ 契约要求 Python **3.11**，本次因新克隆目录没有虚拟环境、且本机 3.11 尚无依赖，
> 临时复用了 3.13.7 环境中的同版本依赖包。**模板渲染与 3.13 无版本相关性**，
> 但正式联调请按 `docs/模板交付需求.md` §B 用 3.11 重建 `.venv` 后再跑一次本脚本。
