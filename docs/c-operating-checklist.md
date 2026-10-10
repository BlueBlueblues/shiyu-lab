# C 当前状态与操作单（2026-10-10 12:00 更新）

> **每次忘记要做什么，就看这份文件的第一节。** 一节一节往下做，做完打勾。

---

## 1. 你现在在哪（1 分钟速览）

| 事项 | 状态 |
| --- | --- |
| Python 3.11 / VS Code / Git 凭据 | ✅ 全部就绪 |
| 加入仓库权限 | ✅ 已加入并可推送 |
| A 的后端代码 | ✅ 在分支 `origin/feat/a-backend`（4 个提交，A01~A05） |
| **模板 6 个文件（A 点名要的活）** | ✅ **已完成 + 已推送到 GitHub**（`8eb6e6b`） |
| 模板验证 | ✅ 34 项断言，**33 通过** |
| 发现的后端缺陷 | ⚠️ 1 个：`/health` 多包一层 `data`（BUG-001） |
| C 的测试成果（61 个用例） | ⚠️ 已在本地提交（`f74363c`），**推送失败，待重试** |
| A06 统计 / A07 复盘 / 报告导出 | ❌ **A 还没写**，整条报告链路（T07~T11）都做不了 |

**一句话**：A 要你做的模板活，**已经做完并推上去了**。现在卡在 A 那边——他要修一个 bug，还要写剩下的接口。

---

## 2. 立刻要做的两件事

### 2.1 重试推送你的 C 成果分支

网络刚才断了，模板那次成功了，这次重试：

```powershell
cd "$env:USERPROFILE\Documents\GitHub\firstorder-lab"
git push origin test/c-foundation:test/c-foundation
```

**✅ 成功**：看到 `* [new branch] test/c-foundation -> test/c-foundation`。

**❌ 失败**：等 1 分钟再来一次，或者跳过 —— **你的成果在本地已经有提交，不会丢**。

### 2.2 把下面这段话发给 A

```
@A 模板交付完成，已推送到 feat/a-backend（提交 8eb6e6b）。

【交付内容】6 个文件：
  app/templates/base.html、experiment_edit.html、service.html、survey.html
  app/static/app.css、app.js

【验证结果】34 项断言，33 项通过，覆盖你点名的所有验收点：
  ✅ 草稿预览可访问（编辑页/服务预览/问卷预览都 200）
  ✅ 发布前公开页 404；发布后服务页、问卷页都 200
  ✅ 三类题渲染与提交值格式正确（单选字符串/多选数组/简答文本）
  ✅ 提交成功有明确反馈（submission-success + 时间戳）
  ✅ 发布后编辑 409 EXPERIMENT_LOCKED、编辑页只读
  ✅ 公开页无 run-log/admin 等管理数据
  ✅ 幂等：同 UUID 同内容 200+duplicate，不同内容 409
  ✅ 非法选项/必答缺失/consent=false 都 422 且不入库
  验证脚本：docs/verify_templates.py（可复跑）
  报告：docs/template-verification-2026-10-10.md

【发现 1 个后端 bug，需要你修】BUG-001（S1）
  现象：GET /health 返回 {"data": {"status": "ok"}}
  预期：{"status": "ok"}（总纲 §6.2，C 的 tests/test_api.py:44 明确断言）
  位置：app/main.py:219-221
  影响：/health 是唯一免登录接口，被包一层 data 后，C 的 T13「按 README 启动看到
       /health 200」和 scripts/smoke.py 都会判定失败
  建议：health() 直接 return {"status": "ok"}

【还有一件事要确认】
  ① requirements.txt 原来是 UTF-16 LE（记事本另存导致），Linux/macOS 上 pip 读不了，
     我已转成 UTF-8 无 BOM，内容未改。请确认接受。
  ② .gitignore 第 14 行把 evidence/ 整目录忽略，C 的测试证据（evidence/reports/）
     按总纲 §10 需要能提交。改不改由你定（里面可能有未脱敏截图）。

【提醒：报告链路还缺接口】
  我看了 app/main.py，目前没有这些路由：
    GET  /api/experiments/{id}/responses
    GET  /api/experiments/{id}/statistics
    POST /api/projects/{id}/review
    GET  /api/projects/{id}/report
    GET  /api/projects/{id}/report.md
    GET  /api/projects/{id}/statistics
  这些是 A06/A07，reports 表目前也是空的。
  没有它们，工作台/报告页没法做，T07~T11 一条都跑不了。
  请问你计划什么时候给？我需要据此排 C03/C04 的时间。

【模板的 context 我按你的真实代码写的，确认三点】
  a) 我确认了模板名字符串就是 "experiment_edit.html"/"service.html"/"survey.html"
  b) 公开页传的是 submit_url（完整地址），预览时是 None —— 我按这个判断能否提交
  c) is_preview=true 时我显示「草稿预览」+ 禁用提交，提交按钮带 disabled 属性
```

---

## 3. A 修完之后你要做什么（先了解，别现在做）

```powershell
cd "$env:USERPROFILE\Documents\GitHub\firstorder-lab"
git checkout feat/a-backend
git pull
.\.venv\Scripts\Activate.ps1

# 1. 复跑模板验证，确认 BUG-001 修好了（应显示 34/34）
python docs\verify_templates.py

# 2. 启动服务，用浏览器自己点一遍
Copy-Item .env.example .env
notepad .env      # 填 ADMIN_PASSWORD / LLM_* ；PUBLIC_BASE_URL=http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000/health`，**应该是 `{"status":"ok"}`**。

**手机验证时用这条**（局域网）：

```powershell
# 查 IP
ipconfig | Select-String "IPv4"
# 用 0.0.0.0 启动，让手机能访问
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 放行防火墙（管理员 PowerShell）
New-NetFirewallRule -DisplayName "FirstOrderLab 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```

并把 `.env` 里的 `PUBLIC_BASE_URL` 改成 `http://你的IP:8000`，重启服务。

---

## 4. 本次交付的文件在哪

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `base.html` / `experiment_edit.html` / `service.html` / `survey.html` | 仓库 `app/templates/` | 4 个模板 |
| `app.css` / `app.js` | 仓库 `app/static/` | 样式与脚本 |
| `verify_templates.py` | 仓库 `docs/` | 可复跑的验证脚本（34 项断言） |
| `template-verification-2026-10-10.md` | 仓库 `docs/` | 验证报告 + BUG-001 |
| `模板交付需求.md` | 仓库 `docs/` | 完整需求清单与验收标准 |
| 操作单（本文件） | 你的工作区 | 状态速查 |

---

## 5. 你现在还不用做的事（别分心）

| 事项 | 为什么先不做 |
| --- | --- |
| 作品介绍 PDF、演示视频、申报材料 | 提交类任务，10/13 之后再说 |
| Playwright 安装与 E2E | 需要报告接口（A06/A07）就位后才有意义 |
| 组织真实用户试用 | 同上，链路还不完整 |
| 统计样本验证（T07） | 统计接口还不存在 |
| 重新装 Python 3.11 虚拟环境 | 你现在用的 3.13 环境能跑通全部验证；切 3.11 留到 A 合并分支、正式联调前做 |

---

## 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| `git push` 报 `Connection was reset` | 重试 2~3 次；再不行把 `git format-patch` 导出的补丁微信发 A |
| `.\.venv\Scripts\Activate.ps1` 报"禁止运行脚本" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `python -m uvicorn ...` 报缺配置 | 确认 `.env` 里 8 个变量都填了，且没有 `REPLACE_ME` |
| 页面 500 且提示"模板尚未交付" | 说明你切到了没有模板的分支；`git checkout feat/a-backend` |
| 忘了自己做到哪了 | 回来看本文件第 1 节 |
