# 安全边界与排障

作者：Fubuki。

## 边界

MVP 的隔离执行仅面向已批准、受限的模板，不等同于允许互联网上的不可信用户执行任意代码。将来开放第三方代码前必须独立威胁建模和沙箱评审。当前 API 不接受 shell、Python 源码、本地主机路径、privileged 容器、Docker socket 或用户自带运行环境。

Linux compute 要求实际可用 bubblewrap，不具备则拒绝。CPU 使用 affinity 与总 CPU 时间上限，内存使用地址空间限制，运行时间由独立守卫限制，网络/文件访问由命名空间隔离；Worker 只读运行时和批准的脚本，不挂载用户主目录、配置或节点凭证。Windows 监控支持不代表 Windows 执行隔离已交付。

Ollama 为节点管理员批准的本地二进制和模型，不接受云端可执行路径。Linux 使用用户 systemd scope 的 CPUQuota/MemoryMax/TasksMax；scope 名随机生成，停止只针对 PCC 自己的 scope。父进程结束不代表子进程停止；若整个 scope 的回收无法确认，保留 backend 并隔离该节点的调度额度，等待核对。GPU 预算需要模型验证，软件检查不提供硬件显存分区。不得用超出节点可分配额度的模型。

控制面使用非超级用户 PostgreSQL 账号；密码 scrypt，API/节点/租约/会话令牌使用加密哈希而非明文保存。注册/初次密钥只显示一次。生产需独立随机 PCC_SECRET 与数据库密码。API 端实施所有者授权，前端隐藏按钮不作为安全边界。TLS、Secure/HttpOnly/SameSite Cookie、CSRF、登录限流、请求体和任务配额均为分层措施。

状态转换经数据库事务与 PostgreSQL 全局 advisory lock 串行化，有效 attempt 另有唯一索引。当前适合个人规模，吞吐瓶颈应先量测事务锁而不是引入消息系统。重启保留持久任务；过期租约占用资源直到确认停止，宁可暂停排队也不隐式并行重做。

## 排障

| 现象 | 检查与处理 |
|---|---|
| 启动拒绝配置 | 替换 REPLACE 值；生产使用 PostgreSQL、Secure Cookie；Owner 密码至少 16 字符 |
| 网页 503 | 先运行前端 build；确认 PCC_WEB_ROOT 与 assets 存在 |
| 401 | 区分用户密钥与节点凭证；检查到期/吊销；机器时钟需同步 |
| 403 / CSRF | 浏览器重新 GET auth/me 获取 token；检查角色、密钥作用域、资源 grants |
| 422 | 对照 OpenAPI，未知字段不忽略；Chat 仅支持声明的字段 |
| 节点注册失败 | 令牌一次性、过期失效；检查 HTTPS 证书和出站网络，不关闭 TLS 校验 |
| 注册成功但无计算能力 | 检查 enable_compute、bubblewrap 实际探测、平台隔离能力及本地配置 |
| queued 很久 | 检查两层共享策略、心跳、Worker 模板能力、资源、用户授权、CPU/RAM 阈值 |
| reconciling | 检查原进程是否仍活跃，确认结束后由节点或 Owner 核对，禁止直接删除租约重试 |
| Private 无法通过云端恢复 | 预期行为，本地策略优先；由节点管理员修改本地配置 |
| GPU 模型启动失败 | 检查 systemd 用户 scope、设备 UUID、预安装模型目录、模板/本地资源预算、端口占用；不接管已有非 PCC Ollama |
| 工件 410 / 校验失败 | 检查本地卷挂载、过期时间与备份；不要手工更改 storage 内文件 |
| 审计不足 | 查结构化请求日志的 request_id；反向代理只有 Caddy 可接入，禁止任意伪造转发头 |

## 已知发布限制

实际 Windows 安装/断网重连、Windows 隔离执行、真实图像模型、生产 Docker/Caddy HTTPS、移动端浏览器 QA、完整备份恢复演练仍需验收。合成 Worker 验证协议，不代表 GPU 或模型质量通过。基础保护已实现手动模式和 CPU/RAM 阈值；游戏进程/CUDA 活动检测属于 V1，当前未实现。
