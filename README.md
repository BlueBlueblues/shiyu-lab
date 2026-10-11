# FirstOrder Lab

基线 `feat/a-backend / 532b225`。本地配置在 `.env`，字段说明见 `.env.example`；密钥不提交。
使用 Python conda 环境 `firstorder-lab`：

```powershell
conda run -n firstorder-lab python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
conda run -n firstorder-lab python -m pytest -m "not e2e" -q
conda run -n firstorder-lab python docs/verify_templates.py
node --check app/static/app.js
```

管理页 `/`、`/projects/new`、`/projects/{id}` 使用正式路由和 Basic 认证。问卷公开页与预览页保留现有修复；工作台已接 A06 统计及按需读取原始回答。
A07 后端已实现（复盘、报告 JSON、Markdown）；`report.html` 尚待 B 交付，工作台的三个复盘/报告入口保持禁用。

模型使用非流式 Chat Completions 协议，`LLM_BASE_URL` 填基础地址，适配器追加 `/chat/completions`。
HTTP 各阶段 60 秒，模型结果总等待 60 秒，工作流 80 秒含 5 秒失败清理，真实冒烟客户端各阶段 85 秒，前端管理请求总期限 90 秒。
运行锁由数据库的 running 记录及唯一索引保持，完成/失败释放，启动清理中断记录；没有独立时间 TTL。

自动回归只使用 Mock 和隔离测试库。`smoke_a07.py` 是显式真实模型验收入口：每次建立独立临时库与 test 项目，分别调用一次计划和复盘，无自动重试，证据另存 `docs/model-verification/`。
`docs/browser_e2e.py` 使用隔离临时库、种子计划和 Edge，不调用真实模型，需要环境已安装 Playwright。
`tests/c_integration/e2e/test_flow.py` 为待联调 UI 骨架，仅允许已 Mock 的隔离 test 服务；不能作为本轮浏览器通过证据。

本轮结果及契约裁决见 [集成记录](docs/bc-integration-20261011.md)。原交付的过时说明、环境记录和截图保留在 [历史目录](docs/delivery-history/bc-28615b6-20261011/)，其通过数字和截图属于原作者的旧基线，不代表当前验证。
