# 部署说明

本次归档不执行部署，不修改现有服务。以下操作只供未来新环境部署参考。

1. 使用 Python 3.11 创建独立虚拟环境，安装 `requirements.txt` 并执行 `pip check` 与 `pytest`。
2. 在版本目录之外创建私有环境配置，参考 `.env.example`。数据库放在服务用户可写的独立数据目录；配置文件权限建议 0600。
3. 管理员密码、模型 Key、验证码签名密钥和邮件凭据由部署者单独配置。默认关闭邮件与模型执行。
4. 首次启动会初始化数据库并执行 `app/migrations.py` 迁移。任何已有数据库迁移前，应备份并先在隔离副本验证，不能直接用测试操作生产库。
5. 使用专用低权限用户运行 Uvicorn，绑定 `127.0.0.1`，通过 HTTPS 反向代理提供访问。`PUBLIC_BASE_URL` 与实际 HTTPS 域名一致。
6. systemd 的 `WorkingDirectory` 指向经校验的版本目录，`EnvironmentFile` 指向独立配置，`ExecStart` 使用该环境的 Python 启动 `-m uvicorn app.main:app`。根据反向代理设置可信转发 IP，避免信任任意来源。
7. 部署、服务重启与回滚需由维护者另行安排；源码归档流程不执行这些动作。

数据库与其 WAL/SHM、备份、日志、Session、验证码、邀请码、真实反馈和证书私钥不应进入 Git。SMTP / Resend 为可选发信配置，本仓库不携带任何账号凭据。
