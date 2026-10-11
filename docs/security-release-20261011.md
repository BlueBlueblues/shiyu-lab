# 多用户上线前安全验收（2026-10-11）

基线 `feat/a-backend / fc35500`。本轮没有改变 Schema、数据库结构、统计口径、Prompt、核心 workflow 或模型配置。代码只增加计划失败的脱敏诊断和四个针对性测试。

## 公网安全调整

- 最终入口 `https://81.71.70.249`，PUBLIC_BASE_URL 同步该值。IPv6 的 HTTP 入口和残留域名 HTTP 入口跳转此 IPv4 HTTPS；证书只覆盖 IPv4，不能声称 IPv6 HTTPS 已通过。
- 复用 Nginx，Let’s Encrypt IP 证书免费、约六天有效。当前证书到期 `2026-10-18 06:43:20 UTC`；独立 `/opt/firstorder-certbot` 环境，不修改应用依赖。
- 自动续期 `firstorder-certbot.timer` 每12小时检查，持久化定时器；deploy hook 先 nginx -t 再 reload。certbot renew --dry-run --run-deploy-hooks 成功。
- 原 Caddy 配置无可信证书，ACME TLS-ALPN 验证报 connection reset；停用其自动启动，由 Nginx 使用443。
- HTTP GET/HEAD 308 跳 HTTPS，HTTP POST 等写入403，避免把已收到的明文凭据再重放到 HTTPS。首次访问前已走 HTTP 的客户端并不能通过重定向获得追溯保护；必须分享 HTTPS 链接。
- /register、/api/auth/register 在公网保持503，尚未宣布正式上线。验收只曾允许服务器loopback通过验证TLS注册，验收结束恢复关闭。
- 后端仅监听 [::1]:8000，systemd 只信任 127.0.0.1/::1 代理；Nginx覆盖X-Forwarded-For，不能用客户端提供的该头冒充来源。
- 登录请求Nginx每IP 10/min，burst5；旧Basic请求每IP 10/min，burst20。应用原认证限流也保留。旧Basic自动化高频调用可能收到429，须降低频率；不改变账号权限。
- Cookie实测Secure、HttpOnly、SameSite=Lax、Path=/、Max-Age43200；数据库仍是12小时绝对期限、令牌摘要存储、退出即撤销。未删除历史Session或重置账号密码。数据库和.env权限0600。

服务器变动：`/etc/nginx/sites-available/firstorder`、`/etc/nginx/conf.d/firstorder-auth-limit.conf`、`/etc/systemd/system/firstorder.service.d/10-loopback.conf`、`/etc/systemd/system/firstorder-certbot.{service,timer}`、`/etc/letsencrypt/renewal-hooks/deploy/firstorder-nginx`、`.env`仅PUBLIC_BASE_URL。生产密钥、管理员密码、模型及数据库路径保持原值。

## 备份与回滚

修改前完整备份 `/root/firstorder-backups/20261011T154116Z-security`：项目tar.gz、SQLite backup和Nginx/Caddy/systemd配置tar.gz，root私有目录。原六业务表、users、sessions中的每条旧记录逐条核对不变，完整性及外键通过。新增两个受控TLS验收账号和一个test项目保留，验收Session已退出；无真实反馈修改。

代码有问题可回到fc35500，保留数据库和已验证HTTPS配置；本轮没有数据库迁移。不要直接恢复旧数据库覆盖上线后的新增数据。不要盲目恢复旧HTTP Nginx配置重新开放密码入口；若HTTPS故障，关闭认证/注册入口，先修复或恢复已验证TLS配置。保留所有证书私钥和生产配置在服务器，不进Git。

## 指定模型失败

旧run `5fc433f4-6af9-4067-8199-397a501e1213`：make_validation_plan失败，LLM_OUTPUT_INVALID；上游200、36.076秒，应用502。原始无效模型响应和Pydantic字段错误未保存，无法追溯具体字段；不能把此错误说成上游502或超时，也不能认定某字段已被修复。

新增诊断区分：plan_json（JSON边界或json_decode）、plan_schema（字段类型/缺失/额外字段等）、plan_contract（模型验证器的引用/选项等约束）。仅记录受信字段路径、错误类型、上游状态、模型与耗时；未知字段名替换为unknown_field，最多20条；不保存错误input/context/message、密钥、认证头或正文到正式日志。原HTTP错误响应契约不变，run工具元数据新增可选validation_errors。

新回归输入与上一失败验收一致，但一次成功不能确定旧故障根因；不放宽Schema、不猜测修改业务逻辑。

## 实际验证

- 相关A03/A04/A07/users首轮202通过、1失败（新JSON分类用例误用了无闭合大括号输入，实际应归JSON边界）。修正该用例输入后A03模块99通过；其余104用例此前已通过，未重复全量回归。此前全量468/0/1记录保留。
- HTTPS认证及历史数据检查31通过、0失败；通过Nginx的服务器loopback TLS请求，实测注册/重复账号/登录/错误密码/错误Origin/退出/个人项目/越权/旧报告隔离/Cookie属性。不能将此写成外部浏览器注册证明。
- 公网外部HTTP客户端已验证受信证书和HTTPS首页200、注册503、HTTP登录308、HTTP密码POST403。
- 一次隔离真实DeepSeek完整链路46通过、0失败，总68.703秒；计划37.328秒、复盘29.625秒；真实模型调用2次，无重试。发布、三份受控回答、统计、JSON报告、报告页、Markdown、双用户隔离和退出完成。没有生产数据库写入。
- plan run `29f5698d-28e6-4883-abbb-fb06ec2d30d1`；review run `4dd9f900-1501-40ec-ab58-01b84fbef2b7`。
- 原始模型产物和新证据：仓库外 `D:\AI+比赛\firstorder-isolated-real-20261011T154648Z`。原始model-response-1/2.txt、plan.json、report.json、report.md保留，未用旧结果或Mock冒充。
- 复盘明确3份受控回答、四类意愿独立、不证明真实行为/商业成功；依据对应题目和原始测试文字，行动阈值标待确认建议。计划q5价格文字仍混入“不愿付费”，复盘已如实指出限制并建议下轮拆分；本轮没有修改Prompt或重分类统计。
- 当前工具没有可用浏览器，IAB打开失败。控制台、桌面视觉和手机适配未验收。

## 上线判断

HTTPS与一次真实输出契约通过，历史数据安全检查通过；公网注册继续关闭。旧失败具体根因不能追溯，诊断已补足。尚未完成真实浏览器/手机验收，不能宣布正式上线；正式公开注册及真人试用需完成上述人工验收后再解除Nginx两个注册入口的503。
