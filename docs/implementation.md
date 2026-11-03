# 实施记录

作者：FubukiHanasaki。需求基线：spec-v0.4.md。所有阶段依序实施；未实测项目不得表述为验收通过。

| 阶段 | 修改计划与文件 | 测试计划 |
|---|---|---|
| P0 | pyproject / server 配置与健康接口 / agent 入口 / web React 骨架 / migrations / compose / CI | 健康、错误信封、迁移、前端构建 |
| P1 | server 节点模型和 API / agent 配置、平台发现、轮询 / 安装脚本 | 一次性令牌、凭证隔离、过期、心跳、失联重连 |
| P2 | 模板、任务状态机、事务调度、租约、工件适配器、受限 Worker | 提交到结果闭环、并发领取、迟到结果、续租、取消、路径穿越 |
| P3 | 用户、会话、CSRF、API Key、资源授权、节点策略、审计 | 用户隔离、权限升级拒绝、私人模式、负载、吊销 |
| P4 | 服务定义和实例、受管后端、Chat SSE、异步图片 | 启停/空闲/活动请求、资源锁、兼容字段、协议测试；GPU 单独验收 |
| P5 | web 管理员/Portal 页面、响应式界面 | TS 构建、API 集成、浏览器（可用时） |
| P6 | 失败恢复、限额、文档、备份恢复、端到端 | A01–A14 证据矩阵、全部自动化回归 |

## 安全设计审查

节点凭证与用户密钥独立；任务只运行登记且可验证隔离的模板；本地/云端策略取交集。生产只接受 PostgreSQL 与安全 cookie。Agent 仅出站 HTTPS，默认本地维护模式，配置能力不足则拒绝。数据库租约过期进入 reconciling，只有确认旧进程已退出才能重试。首个受控计算模板只处理有界数字输入；测试模型必须显式启用并标明 synthetic，不代表真实 GPU 验收。

Git 署名、文档作者及产品署名：FubukiHanasaki。临时文件统一 tmp/，禁止提交实际凭证和运行数据。

## P0 结果

模块化单体和 monorepo 已建立；健康与错误信封测试通过（1 项），Alembic 基线可升级。Python 依赖精确版本见 uv.lock；前端构建随 Node 运行时准备继续验证。运行命令见 README。Docker/HTTPS 未实测；P1–P6 待后续提交。

## P1 修改

新增身份/节点迁移 0002、一次性注册、节点专用哈希凭证、跨平台配置与发现、HTTPS 重连、非特权安装脚本。通过本机 bootstrap-key 生成一小时管理凭证进行首轮联调；会话与完整用户管理在 P3 加入。默认 Maintenance，管理员和本地均需明确允许共享。节点状态由心跳新鲜度计算，不因重连重置本地策略。

P1 验证：3 项测试通过（含 P0）；SQLite 迁移至 0002 通过。Windows/Linux 代码路径已提供，当前只有 Linux 主机，Windows 实机安装与断网场景仍需验收。不存在静默宿主机执行回退：无合格隔离层只接入监控，不报告 compute 能力。

## P2 结果

新增 0003 迁移、受控模板 API、Job/Attempt 状态机、PostgreSQL 事务 advisory lock + 有效 attempt 唯一索引、幂等提交/领取/完成、租约续期、待核对及停止确认、取消、分页日志、本地工件存储和授权下载。SQLite 采用 BEGIN IMMEDIATE，生产禁止使用 SQLite。资源计入未核对 attempt，避免失联后过量分配；Shared 优先。

Linux 实际使用 bubblewrap 的网络/PID/挂载命名空间、只读运行时、独立临时目录、CPU affinity、地址空间/CPU 时间/文件大小限制。没有支持的隔离能力即不提供该 Worker。Windows 此时可监控接入，不宣称与 Linux 等价的计算隔离。

9 项测试通过，包括真实隔离子进程完成“提交→领取→统计→工件上传→授权下载”、并发领取、过期租约、停止确认后重试和旧结果拒绝。实际 GPU、Docker HTTPS 和 Windows 仍未验收。架构保持模块化单体，无额外队列服务。生产前仍需 P3–P6。

## P3 结果

新增 0004 迁移、scrypt 密码、HttpOnly/SameSite 会话、CSRF、持久登录限流、密钥作用域/期限/吊销、Owner 用户授权界面所需 API、节点 Admin 资源授权及审计查询。管理权限由后端验证；普通用户只能访问自己的任务/工件。13 项测试通过，新增用户隔离、越权、Private 本地优先、Admin 授权、CSRF 和登录失败审计；SQLite 全量迁移及 Alembic 差异检查通过。实际生产 TLS 和 Windows/GPU 仍待外部环境验收。

## P4 阶段提交记录（最终验收见 P6）

新增 0005 服务定义/实例迁移、服务期望状态与心跳报告、模型注册、OpenAI Chat 严格字段子集、SSE、原生异步图片接口。Agent 提供受管 Ollama 进程、回环监听、本地模型允许列表、systemd 用户 scope 的 CPU/内存/进程数限制、独占服务资源锁、活动请求计数和空闲原生卸载后终止自有 scope。合成适配器显式标记 synthetic，不把测试图片或回显当成真实 AI。

16 项测试通过，包括合成 Chat JSON/SSE 和 PNG 工件完整闭环、活动请求防卸载及 GPU 锁逻辑。Chat SSE 首版在推理完成后分块返回，尚非后端逐 token 转发。首版真实适配器为 Linux Ollama；Windows 在缺少等效隔离实现时拒绝该 Worker。尚未接入真实图像推理适配器。

P4 提交时本机尚无模型与运行时，硬件标志未达到。P6 已在 tmp 准备并校验公开测试模型，完成真实 GPU 加载、推理、空闲卸载与自有进程退出；当前证据见 acceptance.md。

## P5 结果

React/TypeScript 中文响应式 UI 已连接真实 API：安全登录、任务工作台/分页/日志/取消/结果下载、Chat SSE、图片异步提交与历史、节点指标/云端共享模式/一次性令牌/独立中止/吊销、模型服务启停、用户授权/API Key/模板登记/审计。没有硬编码节点和模型数据。空状态从 API 返回，未配置时禁止相应提交。

TypeScript 类型检查和 Vite 生产构建通过，后端 16 项回归通过。当前 CUA 浏览器清单为空，浏览器点击和移动端实机 QA 尚无证据。运行：npm run build --prefix web 后启动 pcc-server，访问根路径。页面作者署名 FubukiHanasaki。


## P6 开发版加固结果

- 新增请求体与任务/工件总量限额、请求 ID/结构化日志/拒绝审计、无堆栈错误信封、过期工件/会话清理、Owner 人工停止确认与旧结果 fencing。
- Agent 持久化领取 ID，丢失完成确认时精确重试；独立守卫使用服务端 TTL 与本地单调时钟，网络阻塞和时钟偏差不绕过租约。启动前再次校验本地 Private/Reserved/负载/额度/已启用适配器，退出停止自有服务。
- 模型慢启动在后台执行，不阻塞心跳；服务预热计入调度资源。长轮询等待释放线程，数据库原子领取仍保持全进程事务锁。
- 增加真实 ComfyUI 队列/历史/PNG 下载适配器：管理员批准的本地工作流、固定安全节点类型、禁用 custom nodes、回环监听和 cgroup 资源限制、受控临时目录。实际图像权重尚待验收，协议 mock 不作为实模结果。
- 完善中文界面中的队列解释、API Key 有效期、预留名单与负载策略；统一格式化源码。静态 API 文档不依赖第三方 CDN 脚本。
- 补齐 Docker 非 root Server、PostgreSQL 非超级用户账号初始化、Caddy 请求上限、打包清单、开发工具、备份/恢复和 A01–A14 验收矩阵。

最终验证：SQLite **38/38**，实际 PostgreSQL 18.6 非超级用户角色 **38/38**，无 skip；包括真实 HTTP + Agent + 隔离进程 + 授权下载，且探测确认沙箱无法读取宿主凭证或连接宿主回环服务。前端 TypeScript/Vite、wheel/sdist 构建、wheel 独立安装及 CLI 入口、Shell 语法、Compose 与 Caddy 原生配置校验通过。FastAPI/Starlette 测试客户端有一条弃用提示，已记录依赖升级策略。

真实 Ollama 0.40.14 / smollm2:135m GPU 生命周期已实测；原生显存 381,828,463 bytes，完整证据保存于 docs/evidence/gpu-lifecycle.json，仅证明推理链路，不评价生成质量。没有真实 GPU 的 CI 不伪造此证据。

运行：`python3 scripts/dev.py setup / init-dev / migrate / build / serve` 按 README 顺序；生产用 `.env.example` 和 Docker Compose。临时运行时、权重、数据库、缓存、虚拟环境和构建产物全部留在忽略的 tmp/。Git 采用 P0–P6 分阶段提交，作者 FubukiHanasaki，开发版标签 v0.1.0-dev。

**尚未完成发布验收：** Windows 实机接入/等价受限执行器、ComfyUI 真实图片权重、Docker/Caddy 真机 HTTPS 与重启持久性、备份恢复演练、浏览器及移动端交互验证。当前是可运行开发版，不宣称 A01–A14 全通过。架构未引入额外队列、微服务或硬件型号调度分支。

## P6 后续补齐 · 2026-11-03

上述阶段记录为原交付快照。本轮补齐 Windows AppContainer/Job Object 执行、独立运行时和 SID ACL、停止后 profile 清理，增加实际 TLS/重启持久性测试以及可执行的流式备份/单事务恢复；完成 PostgreSQL 清空恢复演练。ComfyUI 已使用真实 SD1.5 权重完成 GPU 推理与普通用户 HTTPS 图片任务、授权下载和空闲卸载，修复重复启停与显存池预算问题。

最终 PostgreSQL 59 项通过/5 项 Windows 跳过，SQLite 58 项通过/6 项跳过，前端传输 3 项通过，类型/生产构建、Python 分发包及部署配置检查通过。Windows 原生和 Compose 验收 CI 已提供但未触发；生产 Docker/域名/NAT、Windows 实机和此前暂跳过的浏览器视觉仍需外部验收。当前状态与命令以 [后续核验](completion-v0.4.md)和[验收矩阵](acceptance.md)为准。

## Windows 受管模型 · 2026-11-03

补齐 Windows Ollama/ComfyUI 的 Job Object 进程树控制、资源限制、端口归属校验、服务生命周期和恢复记录；提取跨平台受管进程接口。提供标准账号配置、CPU/NVIDIA 模式、批准的工作流和原生测试入口。Ubuntu 定向模拟/契约检查 29 项通过、7 项 Windows 用例跳过，wheel/sdist 和静态检查通过；不将这些结果当作 Windows 真实模型验收。配置、升级和实机验证流程见 [Windows 模型支持](windows-models.md)。
