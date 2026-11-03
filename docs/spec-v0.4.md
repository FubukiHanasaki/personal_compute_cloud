# Personal Compute Cloud（PCC）开发需求规格 V0.4

> 状态：需求基线 / 待实施  
> 文档语言：中文  
> 目标读者：项目所有者、Codex 及后续维护人员  
> 原则：轻量、结构化、安全优先、可验证；**本文件不包含应用实现代码**。

## 1. 产品目标与范围

PCC 是一套自托管的个人分布式计算平台。云服务器托管控制面，Windows/Linux 计算节点运行轻量 Agent 并**主动向云端发起出站 HTTPS 连接**。普通用户、外部程序和 Web 前端只需通过 HTTPS 使用服务，**不安装 PCC 客户端**。

### 1.1 通用节点接入模型

PCC 不依赖任何特定设备、CPU/GPU 型号、节点数量或网络位置。任意受支持的 Windows/Linux 计算机，在安装 Node Agent、配置其任务/模型运行环境并完成节点注册后，即可作为计算节点接入云端，由 Server 统一发现能力、管理权限和调度任务。

- **Server 只管理能力与策略**：如 CPU、内存、GPU、显存、可执行任务模板、可用模型服务、资源限额及在线状态；不得依据设备名称硬编码调度逻辑。
- **Agent 是节点唯一必需的 PCC 组件**：只负责心跳、资源采集、领取任务、启动/停止受管 Worker、执行任务和上传结果。实际计算后端由节点管理员按需配置；不要求每台节点都安装 GPU 驱动、LLM 或图像模型。
- **安装即具备基础接入能力**：配置服务器地址及注册凭证后可以上线并报告自身能力；未配置相应 Worker 的节点不接收该类任务。对外 HTTPS API 使用者不安装 Agent。
- **统一管理不等于强制使用**：节点可设置共享策略与资源上限；调度器只能在授权与实际能力范围内分配任务，不能越权控制节点本地其他进程。

**已确认决策（不可在实现时擅自改动）：**
1. 通用计算 + AI 推理。
2. Agent 管理受管服务的自动启动、停止和模型卸载。
3. 管理员 WebUI、用户 Portal、标准 HTTPS API 三种入口。
4. Windows + Linux 节点同时支持。
5. 手动私人模式 + 自动本地负载保护。
6. 通用计算采用**管理员批准的任务模板 + 隔离执行环境**。
7. OpenAI 兼容 AI 接口 + PCC 原生任务 API。
8. 云端保存元数据，文件使用可配置存储后端。
9. 保守调度：优先常驻共享节点，私人节点仅在明确允许时参与。
10. 用户 Portal 包含任务面板、Chat、Image Generation。
11. Linux 云端通过 Docker Compose 部署。

### 1.2 非目标（MVP 不做）

Kubernetes、跨组织多租户计费、自动购买/销毁云 GPU、任意远程桌面、任意公网 SSH/终端、分布式训练、GPU 显存跨机器合并、自动部署任意用户上传的软件环境、复杂 DAG 编辑器和模型市场。**不能因为属于非目标，就在架构中使用无法扩展的硬编码设计。**

## 2. 最小系统架构

```text
浏览器 / Python / Codex / Text2Visual
              |
         HTTPS 443
              |
     Caddy（TLS/反向代理）
              |
     PCC Server（模块化单体）
      |   |    |     |
     Auth Jobs Nodes Services ── Storage Adapter
              |
       PostgreSQL（任务与元数据）
              ^
              | 节点主动发起的 HTTPS 长轮询 + 心跳
      ┌───────┼────────────┐
      |       |            |
  Node Agent Node Agent  Node Agent ...
      |       |            |
  按能力配置的受管 Worker / 计算环境
```

**推荐实现（允许 Codex 提出同级替代，但须解释）：**
- Server：Python / FastAPI；一个后端应用，内部按模块分层。
- 管理界面和 Portal：React + TypeScript；共用身份系统与组件库，按角色展示。
- 数据库：PostgreSQL；首版使用数据库事务实现任务领取与租约，不额外引入 Redis、Kafka、NATS。
- HTTPS：Caddy；Server 与数据库仅处于容器内部网络。
- Agent：Python，Windows Service / Linux systemd，使用 HTTPS 长轮询，不要求节点公网 IP、端口映射或 Tailscale；支持独立安装包或清晰的安装脚本，以及本地配置文件。
- 文件存储：统一 Storage Adapter；首版支持服务器本地磁盘，保留 S3 兼容对象存储实现位置。
- Worker：接口化支持 Python 受控任务、llama.cpp/Ollama、ComfyUI 等；首个可运行适配器以最小、可测试后端为准。

**原则：控制面和 Agent 不以 root/Administrator 权限常态运行。** 必须使用受限 OS 账号和显式允许的可执行能力。节点和本地推理服务不直接暴露到公网。

## 3. 核心对象与数据模型

| 对象 | 必需字段（概念级） | 约束 |
|---|---|---|
| User | id, role, status | Owner/Admin/User；不直接存明文密码 |
| APIKey | id, user_id, scopes, expires_at, revoked_at | 只存哈希；显示一次；可撤销 |
| Node | id, name, os, capabilities, policy, last_seen, status | 心跳过期后不可调度 |
| NodeCredential | id, node_id, hash, expires_at, revoked_at | 与用户 API Key 分离 |
| Job | id, owner_id, task_type, template_id, status, requirements, created_at | 持久化、按所有者授权 |
| JobAttempt | id, job_id, node_id, lease_token_hash, lease_expires_at, status | 多次尝试可审计；同一时刻最多一个有效执行租约 |
| ServiceDefinition | id, type, allowed_config, idle_policy | 服务定义与实例分离 |
| ServiceInstance | id, definition_id, node_id, state, last_active | 仅 Agent 管理自身节点上的实例 |
| Artifact | id, job_id, owner_id, key, size, sha256, expires_at | 校验所有权与存储访问权限 |
| AuditEvent | id, actor, action, target, timestamp, outcome | 管理动作可追踪 |

`Job` 为一次性任务；`ServiceInstance` 为可启停的长期服务；`Reservation/NodePolicy` 为资源预留与共享控制。三者不可混为同一任务状态。

## 4. 功能需求

### 4.1 节点注册与资源监控（MVP）

- Agent 提供跨平台安装与配置流程：安装 Agent → 配置 Server HTTPS 地址、节点名称和允许使用的资源 → 使用**一次性管理员签发的注册令牌**绑定 → 换取独立节点凭证 → 自动上线；注册令牌过期/使用后失效。
- 节点首次上线不要求预先配置所有计算后端；Agent 自动发现可用硬件及已启用的任务/服务适配器。所需驱动、模型、执行环境由节点管理员自行准备并在 Agent 中声明，能力缺失时不得领取不兼容任务。
- 定期上报 OS、CPU、RAM、磁盘、GPU 型号、显存总量/使用量、支持的 Worker 和节点状态。
- 报告物理容量、管理员可分配容量、实际可用容量，三者必须区分。
- 节点断线、重启、代理升级后可重新连接；失联节点停止参与新任务调度。
- 管理端可禁用、撤销节点；默认只有 Owner/Admin 可进行。

### 4.2 任务与调度（MVP）

- 客户端按**允许的任务类型/模板**提交异步任务；支持参数校验、资源需求、超时、幂等键。
- 按权限、节点策略、CPU/RAM/VRAM 可分配额度、模板兼容性和节点健康状态匹配。
- **保守调度**：优先调度已授权的 Shared 节点；Private / Reserved 节点遵循本地策略，禁止抢占任何非 PCC 进程。
- 任务可排队、取消、查看日志/状态、下载结果；断开浏览器不影响后台任务。
- 任务领取必须在数据库事务中原子执行；使用有时限租约、续租及 attempt id。
- Agent 失联后任务进入待核对状态；**不要直接假设原任务已停止并并发重试**。可重试性按模板声明，对非幂等任务默认不自动重试。
- 停止新任务与强制中断已有任务须为两个独立操作。

### 4.3 执行隔离与受控模板（MVP）

- 普通用户**不可提交任意 shell 命令、任意 Python 源码或本地文件路径**；只允许选择管理员登记的任务模板及白名单参数。
- MVP 可先交付安全的受控计算模板与 AI 推理模板；运行任意第三方代码的沙箱能力属于后续版本，未验收隔离前不得对普通用户开放。
- Linux 执行使用非特权独立进程/受限容器；Windows 使用受限账号/Job Objects 等机制限制子进程和资源；两端隔离强度不默认等价。
- 对 CPU、内存、运行时间、并发量、输出大小、网络及文件访问权限建立可执行限制；隔离能力不足时**拒绝**相应模板，而不是静默降级为宿主机全权限运行。
- 严禁未授权任意命令、主机目录挂载、容器特权模式、Docker socket 直通及敏感凭证下发。

### 4.4 AI 服务生命周期（MVP）

- 注册模型/推理后端及其资源需求；模型服务经 Agent 按需启动、健康检查、停止或卸载。
- 默认空闲阈值可配置（初始建议 300 秒）；正在执行请求时不得卸载。
- 优先使用推理后端的原生卸载功能；需要保证完全释放 GPU 时可终止**由 PCC 启动且独立管理**的 Worker 进程。
- 同一 GPU 上模型服务启动应受资源锁和可分配显存限制；不能杀死本机不属于 PCC 的进程。
- 支持异步图片生成；Chat 接口可独立使用流式响应（SSE）。不要求第一版支持所有 OpenAI API 细节。

### 4.5 本地保护与独占（MVP + V1）

- Owner 可在 WebUI 把指定节点切换为 Shared / Private / Reserved / Maintenance / Offline。
- Private 默认拒绝非 Owner 的新任务；Maintenance 停止新任务并允许管理员选择排空或中止受管任务。
- **MVP：实现手动模式、可分配资源上限与基本负载阈值保护。**
- **V1：自动检测本机活动**（如游戏进程、CUDA 占用、显存压力）；根据配置暂停新任务，且不覆盖手动 Private 决定。
- 原 Agent/网络失联时不允许云端将本机自动改回 Shared；节点本地策略优先，默认 fail-closed。

### 4.6 用户、WebUI 与 Portal（MVP）

- Owner：全部节点、服务、用户、凭证、策略、审计权限。
- Admin：仅管理被授予的资源；不得自动成为宿主机管理员。
- User：查看已授权模型/任务模板、提交任务、查看自己的日志和结果。
- Admin WebUI：节点列表与指标、任务列表、服务启停、权限管理、日志/审计、节点共享模式。
- User Portal：任务面板、基础 Chat、Image Generation、历史结果。
- API Key 可独立设置作用域、过期时间、撤销；管理 UI 使用会话认证与 CSRF 防护，不把长期密钥存入浏览器公开脚本。

### 4.7 存储（MVP）

- PostgreSQL 仅存任务、文件元数据、权限和状态；二进制结果不放入数据库。
- Storage Adapter 接口支持 `put/get/delete`、校验和、授权下载；先实现本地文件存储，后续可换 S3 兼容存储。
- 防止路径穿越、超大上传、恶意文件名；上传/下载需鉴权，有上限与保留期限。
- 计算节点通过授权的临时传输接口收发工件，不持有整个对象存储的长期管理员密钥。

## 5. API 契约（V1 路径前缀，MVP 子集）

所有外部请求通过 `https://<domain>/`，统一 JSON 错误格式：`{error: {code, message, request_id}}`。列表接口支持分页。客户端认证使用短作用域 Bearer API Key 或 Web 会话；不暴露内部节点凭证。

### 5.1 公共 HTTPS API

| 方法 | 路径 | 行为 | 典型响应 |
|---|---|---|---|
| POST | `/api/v1/jobs` | 提交受控任务；可提供 `Idempotency-Key` | `202 {job_id,status}` |
| GET | `/api/v1/jobs/{id}` | 查询状态及进度 | `200 {job_id,status,...}` |
| POST | `/api/v1/jobs/{id}/cancel` | 请求取消 | `202 {job_id,status}` |
| GET | `/api/v1/jobs/{id}/artifacts` | 查询授权结果文件 | `200 {items:[...]}` |
| GET | `/api/v1/models` | 查询可用模型与能力 | `200 {data:[...]}` |
| POST | `/v1/chat/completions` | OpenAI 兼容 Chat（支持流式子集） | JSON / SSE |
| POST | `/api/v1/images/generations` | 图片生成，返回异步 Job | `202 {job_id,status}` |
| GET | `/api/v1/nodes` | 查询允许查看的节点（管理员更完整） | `200 {items:[...]}` |

**兼容性边界：** `/v1/chat/completions` 按所声明支持的 OpenAI 字段实现；对于不支持的参数返回明确 4xx，不静默忽略。图像生成走 PCC 原生异步 API，不假装完全兼容同步图像 API。

### 5.2 Agent 内部 API（仅节点身份）

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/internal/v1/nodes/register` | 消耗一次性注册令牌，获取节点凭证 |
| POST | `/internal/v1/nodes/heartbeat` | 上报能力、状态、指标与节点本地策略 |
| POST | `/internal/v1/jobs/claim` | 长轮询领取匹配任务，返回 attempt + lease token |
| POST | `/internal/v1/jobs/{id}/renew` | 带 attempt、lease token 续租 |
| POST | `/internal/v1/jobs/{id}/complete` | 原子提交任务结果和工件元数据 |
| POST | `/internal/v1/jobs/{id}/fail` | 报告失败与可重试标志 |
| POST | `/internal/v1/jobs/{id}/events` | 有界增量日志/进度 |
| POST | `/internal/v1/jobs/{id}/artifacts` | 受限工件上传 |

内部调用须 HTTPS + 节点专用凭证。Agent 只允许领取授权模板；Server 下发完整参数和资源限制，而不是任意 shell 字符串。对重复 complete/renew/claim 按 attempt 和幂等规则处理。

### 5.3 任务输入示例（契约示意，不是应用代码）

```json
{
  "task_type": "llm.generate",
  "template_id": "approved-llm-template",
  "input": {"prompt": "Explain binary search."},
  "requirements": {"gpu": true, "min_vram_gb": 8},
  "timeout_seconds": 120,
  "allow_paid_cloud": false
}
```

提交的 `requirements` 只是请求，必须由服务器和模板的限制共同校验；用户不能利用它绕过配额或节点策略。云资源付费使用默认关闭。

## 6. 状态机与重要不变量

### 6.1 Job

`queued → claimed → running → completed`；可转为 `cancel_requested → cancelled`、`failed` 或 `reconciling`。超时/失联先进入 `reconciling`，按模板可重试策略重新入队或失败。

**不变量：**
- 一个 Job 同时只能有一个被认可的有效 attempt；旧租约不能覆写新 attempt 的结果。
- 同一任务的重复提交可通过用户范围内幂等键去重。
- `completed/failed/cancelled` 为终态；完成后的结果不可被迟到 Agent 覆写。
- 节点失联不等于确认其子进程已停止；涉及副作用的任务不自动并行重做。

### 6.2 Service

`stopped → starting → ready → idle → stopping → stopped`，允许 `error`；重启、健康检查失败和资源不足均有明确事件记录。空闲计时仅在请求计数为 0、没有关联活动 Job 时启动。

### 6.3 Node

`pending → online → stale → offline`；管理员还可以设置 `disabled`。网络状态与共享策略是不同字段；在线并不意味着允许调度。

## 7. 非功能与安全要求

| 领域 | 基线要求 |
|---|---|
| 安全 | 全部外网入口使用 HTTPS；节点凭证与用户凭证分离；最小权限；限制任务模板；审计高危操作 |
| 稳定性 | Server 重启后持久化队列恢复；Agent 自动重连；任务租约与迟到结果核对 |
| 可观测性 | 请求 ID、结构化日志、任务事件、节点心跳、GPU/CPU/RAM 指标 |
| 性能 | 控制端不依赖 GPU；空闲 Agent 不持续推理；状态查询不触发模型加载 |
| 资源保护 | 计算任务严格受节点 Allocatable 限制；私人模式优先；不得终止非 PCC 进程 |
| 维护性 | 模块化单体；API Schema/OpenAPI；版本化配置与数据库迁移；适配器可替换 |
| 可迁移性 | Server 可用 Docker Compose 一键重建；配置和持久化数据可备份/恢复 |
| 用户体验 | Admin 与 Portal 中文优先、响应式网页；失败提示明确，不暴露敏感堆栈 |

**必须明确声明：** MVP 的“隔离执行环境”只对已批准、受限的任务模板提供保障，不等同于允许互联网上的任意不可信用户安全执行任意代码。若未来开放任意代码执行，须专项威胁建模和独立沙箱评审。

## 8. 验收标准（可测试）

| 编号 | 验收场景 | 通过条件 |
|---|---|---|
| A01 | 部署 | 在一台 Linux 云服务器用 Docker Compose 启动，HTTPS 访问正常，重启后数据库数据保留 |
| A02 | 跨平台安装与接入 | 在任意符合最低要求的 Windows 与 Linux 机器安装、填写服务端地址及注册凭证后，Agent 能注册、心跳、断线重连并上报能力；无需修改 Server 代码 |
| A03 | 无入站端口 | 任意 NAT/防火墙后的节点在允许出站 HTTPS 时，无需入站端口或虚拟局域网即可领取并完成任务 |
| A04 | 普通用户 API | 仅凭 HTTPS + API Key 提交受控任务、查状态、取结果，无需安装客户端 |
| A05 | 权限 | 用户 A 不能查看用户 B 私有任务、工件及未授权节点 |
| A06 | 安全执行 | 未授权模板、任意 shell 或路径穿越输入均被拒绝且产生日志 |
| A07 | 调度 | 多个不同能力的节点均可接入；Shared 节点符合条件时可被调度，Private 节点不接收未授权任务；不依赖设备名称 |
| A08 | 租约恢复 | 模拟 Agent 掉线及迟到完成；不能出现两个被认可的终态或旧 attempt 覆写 |
| A09 | 模型生命周期 | 已注册服务能自动启动、健康检查、空闲卸载；活动请求中不会卸载 |
| A10 | 本地保护 | 手动 Private 立即阻止新任务；强制终止只影响 PCC 管理的 Worker |
| A11 | Portal | 登录后可操作基础 Chat、图片生成和任务面板，并查看自己的历史 |
| A12 | 文件存储 | 上传/下载鉴权，文件校验正确，数据库不存二进制文件内容 |
| A13 | 异常处理 | 无可用节点、显存不足、任务超时、节点离线返回可理解状态，不导致 Server 崩溃 |
| A14 | 文档 | 提供部署、备份/恢复、Agent 安装、API 使用、安全配置及故障排查文档 |

若本地硬件或某个模型暂不可用，允许使用可控测试 Worker 验证协议；但不能把模拟测试说成已经通过真实 GPU 集成验收。

## 9. Codex 实施顺序与每阶段交付

**要求 Codex 严格按阶段交付，先交付可运行纵向闭环，避免一次生成全部模块。每阶段先给出修改计划、文件清单及测试，再实施；不要自行扩大范围。**

| 阶段 | 开发范围 | 完成标志 |
|---|---|---|
| P0 骨架 | Monorepo、Server/Agent/WebUI、Docker Compose、配置、数据库迁移、CI 基础 | 本机能启动 Server、健康检查和空白网页 |
| P1 节点链路 | Agent 安装与初始化配置、注册凭证、心跳、能力发现、Windows/Linux 适配、节点状态展示 | 不修改 Server 即可增加新节点；两种 OS 均可主动连接、断线恢复 |
| P2 任务闭环 | 模板注册、原子领取、租约、受控 Worker、任务/日志/结果 API | HTTPS 提交→领取→执行→查看结果，全链路通过 |
| P3 权限与保护 | 用户/API Key、任务鉴权、节点策略、Private、审计 | 普通用户隔离，私人节点拒绝远程调度 |
| P4 AI 服务 | 统一模型注册、首个推理适配器、启停/卸载、Chat/图片 API | 在真实 GPU 上至少完成一次模型加载→推理→卸载 |
| P5 WebUI | Admin 节点/服务/任务页、Portal Chat/Image/任务页 | 浏览器可完成主要操作，移动端可用 |
| P6 加固发布 | 错误恢复、上传/下载、限额、备份、部署说明、端到端测试 | A01–A14 对应测试/证据齐备，完成 MVP 发布 |

**建议 P0–P2 在云服务器 + 任意一台符合系统要求的计算机上联调。** 先验证 Agent 安装、配置、注册、资源发现、任务分配与结果回传；不依赖任何特定型号的 CPU/GPU。协议稳定后再接入具体 AI Worker。

## 10. 实现约束（交给 Codex 的工作规则）

1. 不引入 Kubernetes、分布式消息队列或微服务，除非现有实现证明数据库事务方案无法满足需求，并先取得同意。
2. 不允许普通用户通过 API 向 Agent 下发任意命令、路径或宿主机脚本。
3. API 契约、数据库迁移、任务状态机及权限检查必须有自动化测试；关键流程包含失败注入测试。
4. 不得把任何设备名、显卡型号、固定节点数量、特定系统路径或默认模型写死在调度核心；以节点实时上报的能力和管理员策略为准。
5. Windows/Linux 差异放进平台适配层；不因某平台缺少隔离能力而静默降低安全标准。
6. 优先选用成熟依赖；在文档中记录版本、许可证和必要的升级策略。
7. 环境变量/密钥文件配置，不提交真实凭证；提供 `.env.example` 与首次安装指引。
8. WebUI 必须通过后端授权，不允许仅在前端隐藏按钮来实现权限限制。
9. 每阶段提交：变更摘要、架构影响、运行命令、测试结果、尚未实现范围。
10. 首先确保一个真实的“请求 → 排队 → 执行 → 结果”闭环，不以静态 UI 或伪数据作为 MVP 完成标准。

## 11. 待填部署参数（不阻塞 P0 设计）

- 云服务器操作系统版本、CPU/RAM、可用存储、网络带宽及是否开放 80/443。
- 域名及 DNS、证书策略；无域名时先在开发环境验证 HTTP，生产环境必须 HTTPS。
- 第一批用户规模、单用户并发/每日额度、默认任务运行时长。
- 工件上传大小、存储期限、备份保留策略。
- 模型部署位置、磁盘路径、具体首选推理后端。
- Agent 的最低系统版本、服务运行账号、是否允许休眠、各类 Worker 的环境依赖与安装说明。
- Owner 的登录方式（初版建议本地账号 + 强密码，后续可加 OIDC/MFA）。

## 12. MVP 之后的版本方向

- **V1：** 更可靠的本地活动检测和自动保护；更丰富的隔离执行器；队列优先级、模型路由、资源预留、对象存储、性能指标。
- **V2：** 自动创建/停止租赁云 GPU、预算控制、跨区域故障转移、更多执行环境、复杂工作流和多租户能力。

---

**当前结论：** 架构边界与需求基线已明确。PCC 的节点接入是通用能力：安装 Agent、配置环境、注册上线，即可参与符合自身能力与权限的统一调度。下一步先让 Codex 审查本规格的安全与可实施性，形成 P0/P1 文件树和测试计划，再进入编码。
