# 首单实验室 FirstOrder Lab

大学生创业需求验证 Agent。首个场景固定为：一名大学生想向校园社团提供“活动报名网页定制服务”，
先验证是否存在需求、客户在意什么、下一步应怎样试用。

第一版只完成这一条链路：

> 创建创业项目 → Agent 生成验证计划并调用工具创建实验草稿 → 人工检查并发布 →
> 潜在客户填写真实问卷 → 程序计算统计 → Agent 基于反馈生成复盘 → 下载实验报告。

契约以 [`docs/tasks/01_项目总任务大纲.md`](docs/tasks/01_项目总任务大纲.md) 为准；四份任务文件都在
[`docs/tasks/`](docs/tasks/)。接口、字段、状态冲突时以总纲为准，各人指南不得自行改名。

---

## 0. 当前实现状态（截至 2026-10-10）

本 README 描述**最终可运行的完整流程**，但截至 10/10 只有 C 的部分已交付。
A/B 交付前，第 4 节的启动命令会失败，这不是 README 写错，而是后端尚不存在。

| 部分 | 负责人 | 状态 | 说明 |
| --- | --- | --- | --- |
| `app/`（路由、数据库、模型、工作流、报告） | A | **未交付** | A01～A04 尚未落地，`app/main.py` 不存在 |
| `app/templates/`、`app/static/`（页面、样式、脚本） | B | **未交付** | B01～B03 尚未落地 |
| `tests/`、`scripts/`、`README.md`、`pytest.ini`、`docs/` | C | 已交付骨架 | 见 [`docs/test-results.md`](docs/test-results.md) |
| `.env.example`、`.gitignore`、`requirements.txt` | A | **未交付** | 本文档按总纲 §3.2 的约定名描述它们 |

同时阻塞的事项、需要 A/B 提供的输入，记在
[`docs/handoff-c-2026-10-09-10.md`](docs/handoff-c-2026-10-09-10.md)。

---

## 1. 环境准备

总纲 §3.1 要求 **Python 3.11**，并且三人版本一致。

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
# 若提示“禁止运行脚本”，先在当前窗口放开：
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python --version      # 应输出 Python 3.11.x
```

命令行提示符（cmd.exe）用：

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python --version
```

### macOS / Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python --version    # 应输出 Python 3.11.x
```

> 若机器上没有 3.11，请先安装再继续；不要用 3.12/3.13 代替后不说明。
> 本机复核记录与版本差异见 [`docs/env-reproduction.md`](docs/env-reproduction.md)。

---

## 2. 安装依赖

`requirements.txt` 由 A 在 10/10 冻结（含运行与本次测试依赖）。**版本以冻结文件为准，
不要因为 AI 建议就升级或更换框架。**

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

浏览器端到端测试另需（10/12 前安装）：

```bash
python -m pip install playwright
python -m playwright install chromium
```

若暂时没有 `requirements.txt`，可以先按 A 指南的起步清单安装，并把实际版本记录回冻结文件：

```bash
python -m pip install fastapi uvicorn jinja2 pydantic httpx python-dotenv
python -m pip install pytest
```

本机复核时实际冻结的版本见 [`requirements-c-repro.txt`](requirements-c-repro.txt)，
它只是 C 的临时复现记录，最终仍以 A 的 `requirements.txt` 为准。

---

## 3. 配置

复制 A 提供的占位文件，然后在本地填写**真实的模型与管理配置**：

```bash
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
```

`.env.example` 只放占位值。`.env` 已被 `.gitignore` 排除，**不要提交，也不要贴到群聊或文档里**。

| 配置名 | 含义 | 注意 |
| --- | --- | --- |
| `APP_ENV` | 运行环境标识 | `dev` / `test` / `prod` |
| `DATABASE_PATH` | SQLite 文件路径 | 测试与真实试用必须用不同文件 |
| `LLM_BASE_URL` | 模型供应商接口地址 | 只接一个已开通的供应商 |
| `LLM_API_KEY` | 模型密钥 | 不写入任何文档或截图 |
| `LLM_MODEL` | 模型名 | 与供应商控制台一致 |
| `ADMIN_USER` | 管理端账号 | HTTP Basic |
| `ADMIN_PASSWORD` | 管理端密码 | 不要用弱口令；公网必须 HTTPS |
| `PUBLIC_BASE_URL` | 生成分享链接的受控基地址 | 不信任请求 Host |

---

## 4. 启动服务

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

单进程、单 worker（总纲 §3.1）。不要加 `--workers`：运行锁与“启动时清理中断日志”
依赖同进程部署。

验证：

```bash
curl http://127.0.0.1:8000/health      # 期望 {"status":"ok"}
```

Windows PowerShell 用 `Invoke-RestMethod http://127.0.0.1:8000/health`。

---

## 5. 管理登录

管理页面与全部管理 API 使用 **HTTP Basic** 管理凭据，浏览器首次访问会弹出登录框，
之后同源请求由浏览器自动带上。公开服务页与问卷不需要登录。

- 管理端入口：<http://127.0.0.1:8000/>
- 未登录访问管理接口应返回 `401 AUTH_REQUIRED`
- 管理写请求还必须带同源 `Origin` 与固定请求头 `X-Requested-With: FirstOrderLab`，
  否则返回 `403 FORBIDDEN`

公开 token **不是**管理端凭证，拿到 `/e/{public_token}` 不能访问管理数据。

---

## 6. 运行测试

核心用例（不需要启动服务，不调用真实模型，使用临时 SQLite 与 mock 模型）：

```bash
python -m pytest tests/test_api.py tests/test_statistics.py tests/test_workflow.py -q
```

整个 `tests/` 目录（`pytest.ini` 已把 `tests` 设为 testpaths）：

```bash
python -m pytest -q
```

说明：

- 每个用例分配独立临时数据库，通过 `DATABASE_PATH` 注入，**不会读写真实数据库**；
- 项目一律 `data_kind=test`，mock 通过只算软件测试证据，不能替代真实模型调用；
- A 的后端未交付时，用例会以明确原因 **skip**（不是通过）。
  设 `FO_LAB_REQUIRE_BACKEND=1` 可让缺失后端变成硬失败：

  ```bash
  FO_LAB_REQUIRE_BACKEND=1 python -m pytest tests/test_api.py -q   # PowerShell: $env:FO_LAB_REQUIRE_BACKEND="1"
  ```

最小验收脚本（需要服务已启动，默认只操作 `test` 项目，不向 `real` 实验写任何回答）：

```bash
python scripts/smoke.py --base-url http://127.0.0.1:8000
python scripts/smoke.py --with-plan        # 额外触发一次真实模型计划，会计费与耗时
```

退出码：`0` 全部通过，`1` 有检查失败，`3` 服务不可达或缺少管理配置。

---

## 7. 局域网手机试用

真实试用者要能用自己的手机打开公开问卷。**正式试用使用单独的数据库和 `real` 项目。**

1. 手机与电脑接入同一个可信 Wi-Fi；不要在公共网络上开放监听。
2. 查出电脑在局域网内的 IP：

   ```powershell
   ipconfig | Select-String "IPv4"
   ```

   ```bash
   ipconfig getifaddr en0        # macOS
   hostname -I                    # Linux
   ```

3. 用该 IP 作为受控基地址启动，不要用 `127.0.0.1`（手机访问不到）：

   ```powershell
   $env:PUBLIC_BASE_URL="http://192.168.1.23:8000"
   $env:DATABASE_PATH="data/real-trial.db"
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

4. 允许 Windows 防火墙的入站连接（首次监听会弹窗；也可在“高级安全 Windows Defender
   防火墙”里为专用网络放行 TCP 8000）。
5. 手机浏览器打开 `http://192.168.1.23:8000/e/<public_token>`，确认服务页与问卷可读、
   三类题可选、提交后有成功提示。
6. 公网部署必须启用 HTTPS 并保护管理端；不要只把局域网地址换成公网 IP。

组织流程、记录表与指标口径见 [`docs/user-recruitment.md`](docs/user-recruitment.md) 与
[`docs/user-test-log.csv`](docs/user-test-log.csv)。**不替参与者填写，也不把 test 回答计入真实样本。**

---

## 8. 备份与恢复

用 SQLite 备份 API 生成一致副本，**不要在服务运行时直接复制数据库文件**：

```bash
python scripts/backup.py --source data/firstorder.db --dest evidence/backup-demo.db
```

> 状态：`scripts/backup.py` 属成员 C 的 C06（10/13）交付物，本 README 按已约定的命令接口先写。
> 脚本落地前请用 `sqlite3` 的 `.backup` 命令临时替代，并在复现记录里注明。

恢复：在隔离目录里把副本当作 `DATABASE_PATH` 启动，核对项目数、回答数、报告数与关键 ID 一致；
**不要覆盖真实数据目录**。

---

## 9. 浏览器端到端测试

需要已启动的服务、独立 test 数据库与已安装的 Playwright：

```powershell
$env:DATABASE_PATH="data/e2e.db"
$env:ADMIN_USER="<管理账号>"; $env:ADMIN_PASSWORD="<管理密码>"
$env:FO_LAB_E2E_BASE_URL="http://127.0.0.1:8000"
python -m uvicorn app.main:app --port 8000
# 另开一个终端：
python -m pytest tests/e2e -q
```

未设置 `FO_LAB_E2E_BASE_URL` 或缺管理凭据时，本目录整体 skip，不会污染核心用例结果。
失败截图与下载的报告保存在 `evidence/e2e/<时间戳>/`。

---

## 10. 已知环境问题

**只影响「被文件沙箱限制的自动执行环境」，不影响你们自己打开的终端窗口。**

在那个受限环境里，`python -m venv` / `pip install` / `pytest` 会报 `PermissionError`：

```
PermissionError: [Errno 13] Permission denied: ...\tmppkgxk4r2\pip-25.2-py3-none-any.whl
```

原因是 CPython 在 Windows 上会把 `os.mkdir(path, 0o700)` 映射成「只有所有者可访问」的权限，
而 `tempfile.mkdtemp()` 正是用这个模式建临时目录；沙箱加不上自己的写入授权，于是刚建好的
临时目录就写不进去。定位用的 `os.mkdir` 权限矩阵和完整复现步骤见
[`docs/env-reproduction.md`](docs/env-reproduction.md) 第 4 节。

> **为什么队友不用管它**：A、B 在自己电脑上双击打开 PowerShell 时，没有这层沙箱，
> 目录的所有者就是自己，写入正常。按本文第 1、2 节操作即可，不需要任何额外设置。

该问题只影响本开发机的沙箱环境；普通 Windows / macOS / Linux 上按第 1、2 节操作即可。

---

## 11. 目录与文件归属

见总纲 §3.2。简表：

| 目录 / 文件 | 归属 | 说明 |
| --- | --- | --- |
| `app/main.py`、`app/schemas.py`、`requirements.txt` | A | 共享文件，由 A 合并 |
| `app/` 其余文件 | A | 路由、数据库、服务层、提示词 |
| `app/templates/`、`app/static/` | B | 模板、样式、前端脚本 |
| `tests/`、`scripts/`、`README.md`、`docs/` | C | 测试、脚本、复现与交付材料 |

C 不新建第二套业务接口；后端缺陷通过 PR 交 A 复核。
分支：`main` 稳定，个人分支 `feat/a-*`、`feat/b-*`、`test/c-*`。
不上传 `.env`、API Key、管理员密码或真实反馈数据库。
