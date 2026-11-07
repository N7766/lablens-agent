# 工作区身份与权限

Round16 候选默认启用身份验证；本机验收记录见[本轮报告](conversation-round16.md)。这是一条用户会话→成员授权→内部服务身份的链路，尚未形成公网部署、密码重置、MFA或企业SSO方案。

## 启动与旧记录

在私有 `.env` 中配置 `LABLENS_SERVICE_KEY`（随机生成的32–512位可打印ASCII，不能含逗号/空格）和 `LABLENS_AUTH_BOOTSTRAP_PASSWORD`（至少12个字符，最多72个UTF-8字节）。可用 `python -c 'import secrets; print(secrets.token_urlsafe(48))'` 在本机生成服务密钥；不要提交或贴进Issue。初始管理员默认用户名`admin`。数据库已有管理员时启动不覆盖其密码。

Python 使用 `uvicorn lablens.api:app --env-file .env --host 127.0.0.1 --port 8765`；JVM需要将同一组配置显式导入环境，另设置DB/Redis变量后启动JAR。Java不自动读取`.env`。Compose会传递密钥，但本轮没有在本机运行Docker，容器可运行性不算本轮已验收结果。

`LABLENS_AUTH_ORIGIN` 默认 `http://127.0.0.1:8081`，须与浏览器访问的源站完全一致；`localhost`和`127.0.0.1`不互换。Cookie默认HttpOnly、SameSite=Strict、8小时有效；本机HTTP使用`Secure=false`，HTTPS才设`LABLENS_AUTH_SECURE_COOKIE=true`。容器内部可以绑定所有接口，但现有Compose只在宿主loopback发布。不要将这些本地默认值当作公网部署配置。

Flyway V9只新增身份表，**不会自动归属旧工作区**。旧文档、任务和快照保留；在完成明确接管前不出现在任何用户的工作区列表。管理员通过下列API指定已有账户接管，已有成员的工作区无法再次接管。升级前按原流程备份；不要通过删除成员表或回退到无鉴权模式来“恢复访问”。

## API

登录和写请求需`Origin`与配置完全一致。登录返回会话Cookie和`csrf_token`；后续写请求必须同时携带Cookie及`X-CSRF-Token`，只在内存保留CSRF。服务端不接受用工作区UUID、服务密钥或Bearer头替代用户会话。

| 请求 | 权限与用途 |
|---|---|
| `POST /api/auth/login` `{username,password}` | 登录，无公开注册；MySQL按直接来源IP每分钟最多20次尝试，转发头不参与身份/限流判断 |
| `GET /api/auth/session` / `POST /api/auth/logout` | 读取当前身份 / 撤销服务器会话；注销后旧Cookie重放失效 |
| `POST /api/auth/users` `{username,password,admin:false}` | 管理员分配账户；用户名为小写ASCII3–64字符 |
| `GET /api/auth/unowned-workspaces` | 管理员查看未归属工作区，最多1000项 |
| `POST /api/auth/workspaces/{id}/adopt` `{username}` | 管理员明确指定旧工作区所有者；不允许接管已有成员的工作区 |
| `GET /api/workspaces` / `POST /api/workspaces` | 只列出自己的工作区 / 原子创建并绑定所有者；列表最多1000项 |
| `GET /api/workspaces/{id}/members` | 所有者查看成员 |
| `PUT /api/workspaces/{id}/members/{username}` `{role}` | 所有者授权`EDITOR`或`VIEWER`；网页也可管理 |
| `DELETE /api/workspaces/{id}/members/{username}` | 所有者撤销成员；不能移除、改变所有者 |

`OWNER`和`EDITOR`可添加资料、提交/取消任务、修改会话、导入和构建索引。`VIEWER`只读。管理员只拥有全局运维和账户分配权限，不能隐式读取别人的工作区。非成员与不存在的工作区均返回404；未登录返回401，只读成员写入返回403。所有旧的历史接口和新索引/导入接口走同一授权边界。`/actuator/health`只暴露基本健康状态；全局状态、metrics与Prometheus仅管理员可读。

用户会话随机值只以SHA-256摘要存入MySQL；密码使用Spring的BCrypt（cost12）。页面在账户切换、失效或失去工作区权限时清空保存的选择/导入草稿并刷新，忽略旧身份的在途响应。个人浏览器已下载/复制的资料无法被服务器远程撤回。

## 服务身份与费用

Java任务、取消、索引HTTP调用和Agent内部检索携带`X-LabLens-Service-Key`。Python所有`/api`路由（含旧实验API）都验证唯一凭据；只公开静态界面、接口说明和基本health，不给浏览器注入服务密钥。旧Python页面的交互API在默认模式下不能匿名操作；使用JVM工作区登录入口。

`LABLENS_AUTH_ENABLED=false`是显式loopback开发模式，无用户隔离，只适合旧的离线工具/集成夹具。JVM要求loopback绑定和直接peer；Python只接受直接loopback请求，不相信X-Forwarded-For。默认开启时没有密钥就拒绝启动，而非自动降级。

任务仍共享MySQL全局及工作区准入；同一工作区新增成员不会获得新的额度或幂等空间。既有资料版本发送许可照常校验。导入、索引、登录限流是独立机制；它们不等于分布式模型并发、token或金额硬额度。

## 撤权与运维边界

授权在每次HTTP请求进入业务之前检查。**已通过授权检查的在途请求可以在撤权提交后完成**；已入队任务/导入也不会因撤权自动取消。保证的是撤权提交后新进行授权检查的请求被拒绝。成员变更和旧记录接管在工作区行锁下再次检查，不能靠并发修改移除所有者或抢占已有归属。

本轮没有做会话/登录桶自动清理、密码重置/轮换界面、按用户公平调度或跨节点取消。过期会话拒绝访问，但旧行需要后续有界维护。服务密钥是受信服务边界，共享该密钥的进程能调用worker，不能将它分配给普通账户。主服务未自动升级。

验证入口：真实MySQL的`AuthIntegrationTest`、离线`tests/test_service_auth.py`、`services/workspace/tests/auth-ui.test.cjs`，以及隔离环境运行`python scripts/verify_workspace_auth.py --output <新的结果路径>`。后者创建合成用户/工作区并保留，不调用供应商；用新路径保存每次尝试，不能覆盖失败证据。
