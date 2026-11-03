# Personal Compute Cloud

将自己的 Windows / Linux 设备接入统一控制面，通过网页和 HTTPS API 使用经批准的计算任务、Ollama 对话模型与 ComfyUI 图片工作流。

作者：**Fubuki** · 许可证：[MIT](LICENSE) · 当前状态：开发版 · 需求基线：[V0.4](docs/spec-v0.4.md)

项目已提供控制面、出站 Agent、中文管理台和用户 Portal。Windows 模型支持已完成逻辑、安全及模拟检查；Windows 原生执行、生产 Docker 部署与 Portal 视觉验收仍待完成，具体证据见[当前验证状态](#当前验证状态)。

## 功能

- **节点管理**：一次性令牌注册、独立节点凭证、心跳、CPU / RAM / GPU 资源发现与在线状态；节点名称仅用于显示。
- **共享策略**：Shared、Private、Reserved、Maintenance、Offline；本地策略和资源上限优先，维护模式停止接收新任务，活动任务可单独申请中止。
- **任务执行**：管理员批准的模板、持久化队列、幂等提交、租约续期、取消与停止核对；首个计算模板为数字统计 `compute.stats`。
- **模型生命周期**：Windows / Linux 受管 Ollama 与 ComfyUI，按需启动、预热、健康检查、活动请求保护、空闲卸载和自有进程树清理。
- **身份与工件**：Owner / Admin / User、资源授权、会话与 CSRF、作用域 API Key、授权下载、SHA256 校验、保留期限与清理。
- **中文界面与 API**：节点、任务、模型、访问授权管理；用户任务面板、Chat、图片生成；提供 PCC 原生 API 和有限的 OpenAI 兼容 Chat 接口。

## 架构

```mermaid
flowchart LR
    Client["浏览器 / API 客户端"] -->|HTTPS| Caddy["Caddy"]
    Agent["Windows / Linux Agent"] -->|主动出站 HTTPS| Caddy
    Caddy --> Server["PCC Server · FastAPI"]
    Server --> DB["PostgreSQL · 元数据 / 租约"]
    Server --> Storage["本地工件存储"]
    Agent --> Compute["批准的计算 Worker"]
    Agent --> Models["本机 Ollama / ComfyUI"]
```

Server 使用 FastAPI / SQLAlchemy，WebUI 使用 React / TypeScript / Vite；生产入口采用 Caddy + Docker Compose。Agent 主动访问 Server，节点无需公网 IP、入站端口映射或 Tailscale；模型服务仅监听本机回环。

| 组件            | 环境要求                                                                         |
| --------------- | -------------------------------------------------------------------------------- |
| 生产控制面      | Linux、Docker Engine、Compose v2、可用域名与 80/443；Server 不需要 GPU           |
| Agent           | Python **3.12–3.14**、专用普通账号；Windows 执行层要求 64 位环境                 |
| 本地开发        | Git、Python 3.12–3.14、uv、Node.js 22 / npm                                      |
| 计算 / 模型后端 | 按需准备隔离环境、受信运行时、合法模型权重和驱动，见 [Agent 文档](docs/agent.md) |

## 快速开始：本地开发

以下命令在 Linux / Ubuntu 的项目根目录执行。开发环境默认使用 SQLite，仅在回环地址提供 HTTP；生产环境使用 PostgreSQL 与 HTTPS。

```bash
git clone https://github.com/FubukiHanasaki/personal_compute_cloud.git
cd personal_compute_cloud

python3 scripts/dev.py setup
python3 scripts/dev.py init-dev  # 仅首次执行；已有 .env 时跳过
python3 scripts/dev.py migrate
python3 scripts/dev.py build
python3 scripts/dev.py serve
```

打开 [本地 Portal](http://127.0.0.1:8000)，以 `Fubuki` 登录。初始随机密码保存在本地 `.env` 的 `PCC_OWNER_PASSWORD` 中；该文件不进入 Git。交互式接口文档位于 [Swagger UI](http://127.0.0.1:8000/docs)。

依赖、缓存、开发数据库、测试日志及构建产物统一放在被 Git 忽略的 `tmp/`；`web/node_modules` 指向其中的前端依赖目录。`init-dev` 仅用于本地开发，配置不会自动覆盖现有文件。

## 部署到 Linux 服务器

在服务器克隆仓库并进入项目目录后：

```bash
cp .env.example .env
chmod 600 .env
# 独立执行三次，分别生成应用密钥、数据库管理密码、数据库应用账号密码：
openssl rand -hex 32
# 编辑 .env，替换全部 REPLACE 占位符并设置域名与 Owner 强密码。
docker compose up -d --build
docker compose logs --tail=100 server caddy
```

`PCC_DATABASE_URL` 中的密码须与 `PCC_APP_DB_PASSWORD` 一致；Owner 密码至少 16 字符。首次启动自动迁移数据库并创建 Owner，已有账号的密码不会被环境变量覆盖。仅 Caddy 发布外部端口，Server 与数据库保持在容器网络内。

升级和恢复前先备份，保留现有 `PCC_SECRET` 与数据卷；完整配置、备份及新目标恢复步骤见[部署文档](docs/deployment.md)。生产 Docker、域名证书和卷恢复需要在目标服务器验收。

## 接入计算节点

Server 和节点可以运行在不同设备。先以 Owner / 授权 Admin 登录控制面，为节点签发一次性注册令牌；在节点的源码目录安装 Agent，按提示隐藏输入令牌。

### Linux

使用专用非 root 账号：

```bash
sh deploy/install-agent.sh
# 编辑 agent.toml：server、name、mode、允许分配的资源及后端配置。
tmp/agent-venv/bin/pcc-agent register --config agent.toml
tmp/agent-venv/bin/pcc-agent check --config agent.toml
tmp/agent-venv/bin/pcc-agent run --config agent.toml
```

计算模板需要可实际探测通过的 bubblewrap；受管模型需要 systemd 用户 scope / cgroup CPU、内存限制。常驻运行配置见 [Agent 文档](docs/agent.md)。

### Windows

在 64 位 Windows 上使用专用标准账号打开 PowerShell：

```powershell
powershell -File deploy\install-agent.ps1
notepad agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe register --config agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe check --config agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe run --config agent.toml
```

受管模型通过 Job Object 限制 CPU、提交内存与进程树。计算模板另需独立的 embeddable Python 运行时及 AppContainer 探测；模型服务不要求安装该计算运行时。当前提供前台运行和任务计划程序配置方式，原生 Windows Service 包装器尚未提供。

新节点默认 `maintenance`。接收共享任务前，在本地 `agent.toml` 设置 `mode = "shared"`，并在控制面将该节点策略设为 Shared；根据任务类型显式启用所需后端。隔离或资源限制探测失败时拒绝对应能力。

### Windows / Linux 的 Ollama 与 ComfyUI

由节点管理员预先安装可信运行时和合法权重，PCC 根据本地配置启动自己的服务实例。真实权重、运行时和节点私有配置由本地保管；远程用户只能提交批准的参数。

| 后端         | 本地配置与模板绑定                                                                             | 默认端口          |
| ------------ | ---------------------------------------------------------------------------------------------- | ----------------- |
| Ollama Chat  | 本地 `ollama_models` 批准模型；云端 `worker=ollama:<模型名>`，`model=<模型名>`                 | `127.0.0.1:11435` |
| ComfyUI 图片 | 本地 `comfyui_workflows` 批准 API 工作流；云端 `worker=comfyui:<工作流名>`，`model=<工作流名>` | `127.0.0.1:8189`  |

支持 CPU 或显式选择的一张 NVIDIA CUDA GPU。CPU 模式将 GPU 字段留空；GPU 模式填写 `nvidia-smi` 返回的完整 GPU UUID，并配置足够的 RAM / VRAM 预算。模型名称、模板与本地批准项必须一致。

Windows 可参考[模型配置示例](deploy/agent-windows-models.example.toml)和 [SD1.5 API 工作流示例](deploy/workflows/sd15-api.example.json)，按本机路径与 checkpoint 修改；已有 `agent.toml` 时合并配置，保留原注册身份。详细步骤与限制见 [Windows 模型支持](docs/windows-models.md)及 [Agent 模型配置](docs/agent.md#ollamawindows--linux)。

## 完成第一个任务

1. Owner 在“访问与授权”批准 `compute.stats` 模板，并授予需要使用它的用户；模型任务则批准对应 `ai.chat` / `ai.image` 模板。
2. 接入节点，确认本地与云端策略允许任务、后端能力在线且资源额度足够。
3. 在 Portal 提交数字列表，查看排队、执行状态与结果；Chat 和 Image 页面使用已授权模型。

程序可使用有对应作用域的 API Key：

```bash
# PCC_URL 为服务器 origin；PCC_API_KEY 为你签发的 Key，不要写入源码。
curl "$PCC_URL/api/v1/jobs" \
  -H "Authorization: Bearer $PCC_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: first-stats-001' \
  -d '{"task_type":"compute.stats","template_id":"stats","input":{"values":[1,2,3]},"timeout_seconds":120}'
```

上例要求已批准 ID 为 `stats` 的模板。接口返回异步 Job；没有兼容节点时保持排队。使用返回的 `job_id` 查询 `/api/v1/jobs/{job_id}`，通过工件接口取得受授权的下载地址。

| 接口                              | 行为                                         |
| --------------------------------- | -------------------------------------------- |
| `GET /api/v1/models`              | 列出当前用户获准使用的模型                   |
| `POST /api/v1/jobs`               | 提交批准的异步计算 / AI 任务                 |
| `GET /api/v1/jobs/{id}`           | 查询状态与结果                               |
| `POST /v1/chat/completions`       | 支持 `model`、文本 `messages`、`stream`      |
| `POST /api/v1/images/generations` | 输入 `model`、`prompt`、`size`，返回异步 Job |

Chat 的 SSE 在后端完成推理后分块返回；当前不支持 tools、temperature、max_tokens 或多模态消息。图片尺寸限 `256x256` / `512x512`；`synthetic.*` 仅供协议测试。完整权限、字段、取消与恢复说明见 [API 文档](docs/api.md)。

## 安全与运行边界

- 生产外部入口使用 HTTPS；节点凭证与用户 Key 独立，网页会话使用 HttpOnly Cookie 与 CSRF 校验。Agent 保持证书校验，不使用环境代理。
- 计算仅开放已批准的受限模板；Linux 采用 bubblewrap，Windows 采用 AppContainer / Job Object。当前不提供任意不可信代码执行能力。
- 受管模型是节点管理员批准的代码，运行在专用标准账号下。Job / cgroup 约束进程与资源；文件和网络权限仍以该账号及运行时为边界，回环端口也不是本机账号之间的认证边界。
- 活动请求受保护；模型切换先确认旧实例停止。PCC 只终止自有进程树，显存预算不等同于硬件分区，后端缓存和日志没有磁盘硬配额。
- 租约过期进入待核对状态，确认原任务停止后再决定是否重试。遗留 `active-attempt.json` 时按[恢复流程](docs/agent.md#断线与核对)处理，保留原始记录。

真实 `.env`、`agent.toml`、凭证、证书私钥、数据库、模型权重与运行日志均应保留在本地。安全配置和故障排查见[安全文档](docs/security.md)。

## 当前验证状态

项目继续标记为开发版，历史证据与当前修改分开记录：

| 范围                             | 状态                                                                                                                                                                  |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 最新 Windows 模型逻辑 / 安全复查 | Ubuntu 定向模拟与契约检查 **57 项通过、7 项 Windows 用例跳过**；语法、静态检查及 wheel / sdist 校验通过，见[复查记录](docs/windows-models-review.md)                  |
| 既有 Linux 全链路验证            | 历史提交已完成真实 HTTPS → Server → Agent → 隔离 Worker → 授权工件，以及 PostgreSQL 备份恢复；详见[验收矩阵](docs/acceptance.md)                                      |
| 既有真实 GPU 验证                | 历史提交已完成 Linux Ollama 与 ComfyUI / SD1.5 加载、推理和空闲卸载；证据见 [Ollama](docs/evidence/gpu-lifecycle.json)、[ComfyUI](docs/evidence/image-lifecycle.json) |
| 待目标环境验收                   | Windows 原生隔离及 CPU / GPU 模型、驱动与重连；生产 Docker / 公网 TLS / NAT / 卷恢复；Portal 浏览器与移动端视觉检查                                                   |

CI 已配置 PostgreSQL、Windows / Linux Agent 和 Compose 演练入口；工作流是否成功以 GitHub Actions 的实际运行结果为准。模拟检查和历史实测不替代最新代码的目标环境验收。

### 开发检查

准备好开发依赖后：

```bash
python3 scripts/dev.py test
npm run test:api --prefix web
python3 scripts/dev.py build
```

测试输出及构建产物位于 `tmp/`。硬件或隔离条件不足时相关用例会跳过；Windows 真实模型用例另需本地显式配置。需要 PostgreSQL 回归时仅使用专用测试库，测试会重建该库的 PCC 表：

```bash
PCC_TEST_DATABASE_URL=postgresql+psycopg://pcc:password@localhost/pcc_test \
  python3 scripts/dev.py test
```

## 项目目录与文档

```text
server/pcc_server/  控制面、鉴权、调度、模型 API、存储和备份
agent/pcc_agent/    出站 Agent、隔离 Worker、受管模型与恢复
web/src/           中文管理台和用户 Portal
migrations/        数据库迁移
deploy/            安装脚本、容器配置、节点与工作流示例
tests/             权限、任务、隔离、模型及协议回归
docs/              部署、API、验收证据和审查记录
tmp/               本地临时文件与运行数据（Git 忽略）
```

- [服务器部署、配置与备份恢复](docs/deployment.md)
- [Windows / Linux Agent 安装与运行](docs/agent.md)
- [Windows Ollama / ComfyUI 配置](docs/windows-models.md)
- [API 使用与兼容范围](docs/api.md)
- [安全边界与故障排查](docs/security.md)
- [验收矩阵](docs/acceptance.md) · [Windows 模型逻辑与安全复查](docs/windows-models-review.md)
- [需求基线 V0.4](docs/spec-v0.4.md) · [阶段实施](docs/implementation.md) · [变更记录](CHANGELOG.md)
- [依赖、许可与升级策略](docs/dependencies.md)

MIT License · Copyright © 2026 Fubuki
