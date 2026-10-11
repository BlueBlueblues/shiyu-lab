# 用户空间升级迁移方案

基线为 `feat/a-backend / c6c6dbc`，保留此前未提交的公开首页改动。

1. 新增 `users(id, username, email, password_hash, created_at)`。用户名不区分 ASCII 大小写且唯一，邮箱可选。密码使用标准库 scrypt（N=32768、r=8、p=3），每次独立随机盐，不保存明文。
2. 新增 `sessions(token_hash, user_id, created_at, expires_at)`。随机令牌仅放入 HttpOnly Cookie，数据库只保存 SHA-256 摘要；12 小时绝对有效期，退出即撤销。
3. 为 `projects` 添加外键 `user_id` 和索引。默认管理员使用稳定 UUID，首次实际启动从已有 ADMIN 配置初始化其用户名和密码 hash。旧项目全部绑定该用户，不删除或重建现有业务表。
4. 迁移使用单个 SQLite 写事务，可重复运行。已有旧库首次迁移前使用 SQLite backup API 保存 `.pre-users-<UTC>.bak`，失败回滚身份表、字段、归属及版本变化。备份含历史业务数据，应按数据库同等保护，不提交 Git。
5. 旧种子脚本省略 user_id 时，由触发器绑定默认管理员；线上创建接口显式使用当前用户，拒绝客户端指定 user_id。管理员不享有跨用户访问特权。
6. 保留已有 Basic 自动化入口作为管理员兼容调用；网页以登录 Session 为主要入口。数据访问统一检查项目、实验及运行记录归属，其他用户或不存在的资源统一返回 404。
7. 匿名用户仍可访问首页、登录、注册、公开服务页、问卷及提交接口、静态资源和无敏感信息的健康检查。私有页面浏览器请求跳转登录；私有 API 未登录返回 401。
8. API 文档与 OpenAPI 同样要求登录；OpenAPI 标注 Session Cookie 与旧 Basic 两种认证方式。问卷与服务页（含预览）不显示账号导航或用户名。

不自动迁移本机真实库或服务器生产库。本轮全部验证使用隔离数据库。将来部署时先停止服务并核验备份，首次启动执行迁移；需要回滚时停服恢复备份和旧代码，升级后产生的新数据不可直接用旧备份覆盖。

管理员身份只在初始化时从配置导入，后续修改 ADMIN 配置不自动重置用户表密码。现有 HTTP IPv6 部署需先配置 HTTPS，再开放真实用户注册；HTTPS 下 Cookie 自动启用 Secure。多进程/多实例上线前需统一认证限流。

安全参考：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP Session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
