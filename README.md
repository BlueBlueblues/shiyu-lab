# 试玉 · SHIYU LAB

> 以试辨真，以攻成玉

面向大学生 OPC（One Person Company）创业实践的证据驱动型 AI Agent 系统。以宋韵极简与东方治玉为视觉语言，把创业想法逐步转化为可审阅的假设、可执行的实验和可追溯的反馈。

官网：[shiyulab.xyz](https://shiyulab.xyz)

## 四 Agent 与工作流程

```mermaid
flowchart LR
    A[创业想法] --> B[识玉：机会与假设]
    B --> C[琢玉：实验与招募]
    C --> D[人工审阅 · 开试]
    D --> E[发布实验与问卷]
    E --> F[反馈统计]
    F --> G[鉴玉：证据评估]
    G --> H[攻玉：迭代提案]
    H --> I[人工核准 · 复琢]
    I --> C
```

| Agent | 职责 |
| --- | --- |
| 识玉 | 梳理机会、目标用户与待验证假设 |
| 琢玉 | 设计实验、落地页、问卷和冷启动招募工具包 |
| 鉴玉 | 结合反馈统计审查证据、风险与假设 |
| 攻玉 | 提出下一轮迭代，等待人工核准 |

模型输出经结构化契约校验，工作流保存各阶段结果与轮次。人工「审阅 · 开试」「核准 · 复琢」控制实验发布和迭代推进。冷启动招募工具包支持招募文案、渠道建议及触达素材，供人工审核使用，不代表已自动完成用户招募。

## 已有功能

- FastAPI 后端、Jinja2 模板与宋韵治玉 CSS/JavaScript 界面。
- 四 Agent、DeepSeek 调用、结构化输出、状态管理与轮次历史。
- 实验编辑与发布、公开问卷、反馈统计、报告与人工迭代。
- 用户登录、邀请及邮箱验证注册、项目隔离与管理员只读案例中心。
- SQLite 结构初始化与迁移、隔离自动化测试。

## 技术与目录

Python 3.11、FastAPI、Pydantic、Jinja2、SQLite、HTTPX、Uvicorn、pytest；可选 Playwright 浏览器验收。依赖版本以 requirements 文件为准。

```text
app/
  agents/        # 四 Agent 运行时与契约
  services/      # 模型客户端、工作流、实验、统计、迭代与招募
  prompts/       # 阶段提示词
  templates/     # 页面及冷启动工具包
  static/        # CSS、JavaScript、favicon
  main.py        # 应用入口
  migrations.py  # 数据库迁移
  auth*.py       # 认证与路由
tests/           # 隔离测试与 Mock 模型
scripts/         # Mock 预览、浏览器验收
docs/            # 部署说明与生产源码校验清单
.env.example
requirements.txt
requirements-dev.txt
pytest.ini
```

## 本地安装与启动

```bash
git clone https://github.com/BlueBlueblues/shiyu-lab.git
cd shiyu-lab
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

编辑本地 `.env`，为管理员设置独立用户名及强密码，把 `LLM_API_KEY` 占位符替换为自己的 Key。配置加载器即使关闭模型执行也会拒绝 `REPLACE_ME`；只做离线启动时可使用本地虚拟值，并保持 `AGENT_EXECUTION_ENABLED=false`。不要提交 `.env`。

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动会初始化指定的本地 SQLite 数据库并运行迁移。不要将本地 `DATABASE_PATH` 指向生产库。完成上述本地 `.env` 占位配置后，也可运行隔离 Mock 预览，无需真实模型 Key：

```bash
python scripts/preview.py
```

## 配置

| 环境变量 | 用途 |
| --- | --- |
| APP_ENV / DATABASE_PATH | 运行环境与本地数据库路径 |
| LLM_BASE_URL / LLM_API_KEY / LLM_MODEL | DeepSeek 兼容接口配置 |
| ADMIN_USER / ADMIN_PASSWORD | 本地初始管理员 |
| PUBLIC_BASE_URL | 问卷等对外链接的基础地址 |
| AGENT_EXECUTION_ENABLED | 是否允许模型执行，示例默认 false |
| INVITE_REGISTRATION_ENABLED / REGISTRATION_OTP_SECRET | 邀请注册开关与验证码签名密钥 |
| MAIL_PROVIDER / MAIL_FROM / MAIL_API_KEY | 邮件禁用、SMTP 或 Resend 配置 |
| SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_TLS | 可选 SMTP 参数 |

完整占位配置见 [.env.example](.env.example)。邮件默认禁用；启用邀请注册前需单独配置邮件及验证码密钥。

## 测试

```bash
python -m pip check
python -m pytest -q
python scripts/check_source.py
python scripts/check_secrets.py
```

测试使用临时数据库和合成 Mock 输出，不读取生产数据、不发送真实邮件、不调用付费模型。可选浏览器验收：

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/browser_acceptance.py
```

## 部署与边界

见 [部署说明](docs/DEPLOYMENT.md)。本仓库归档当前生产实际文件，Python 内部模块及 systemd 服务名可保留历史命名。

AI 输出属于辅助判断，不能替代真实市场验证。Mock 案例、自动化测试和合成反馈仅说明系统行为，不是市场验证成果；证据质量取决于真实采样、用户授权和人工审核。SQLite 与进程内限流适用于当前部署规模，多实例运行需另行评估。管理员案例功能不随源码分发任何私有案例或用户数据库。

源码基准及逐文件 SHA-256 见 [SOURCE_MANIFEST.json](docs/SOURCE_MANIFEST.json)。生产专用环境、数据库、日志、虚拟环境、发布元数据、历史证据和个人材料均不包含在此源码归档中。
