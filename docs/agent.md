# Agent 安装与运行

作者：Fubuki。Python 3.12–3.14；Linux / Windows 10/11 或受支持 Windows Server。Agent 仅主动访问服务器 HTTPS，不监听入站端口，无需公网 IP 或 Tailscale。

## Linux

用专用普通用户克隆源码，安装 Python venv 支持后运行：

```bash
sh deploy/install-agent.sh
# 编辑 agent.toml（版本 1），填写自己的 HTTPS 地址与资源上限。
tmp/agent-venv/bin/pcc-agent register --config agent.toml
# 在隐藏输入提示中粘贴 Owner/Admin 签发的一次性令牌。
tmp/agent-venv/bin/pcc-agent check --config agent.toml
tmp/agent-venv/bin/pcc-agent run --config agent.toml
```

需要常驻时，修改 `deploy/pcc-agent.service` 中 checkout 路径，安装到用户 systemd 目录并 `systemctl --user enable --now pcc-agent`。管理员可按本机政策配置用户 linger，但 Agent 本身不应提权运行。

## Windows

PowerShell 使用专用**标准用户**从 checkout 运行：

```powershell
powershell -File deploy\install-agent.ps1
notepad agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe register --config agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe run --config agent.toml
```

脚本将凭证所在 tmp 目录 ACL 收紧到当前账号。可用任务计划程序以“当前用户登录时”运行上述命令，取消“使用最高权限运行”，设置失败重试。当前版本提供清晰的安装和前台运行流程；尚未提供原生 Windows Service 包装器，不宣称其已验收。

Windows 已增加 AppContainer + Job Object 执行层，供受控统计和显式合成测试 Worker 使用，要求 64 位 Agent 与运行时；其他架构仅保留可探测的接入能力，不冒险执行。实际安装/隔离验收仍须在 Windows 执行，Ubuntu 上的结构与协议测试不能代替该证据。准备独立的官方 embeddable Python 运行时：

```powershell
powershell -File deploy\prepare-windows-worker.ps1
# 在 agent.toml 设置绝对路径，不能复用 Agent venv 或放置凭证：
# windows_worker_runtime = 'C:\PCC\tmp\windows-worker-runtime'
# work_root = 'C:\PCC\tmp\agent'
# enable_compute = true
& tmp\agent-venv\Scripts\pcc-agent.exe check --config agent.toml
# 应报告 windows-appcontainer-job 与 compute.stats，否则保持拒绝执行。
$env:PCC_WINDOWS_RUNTIME = (Resolve-Path tmp/windows-worker-runtime).Path
& tmp\agent-venv\Scripts\python.exe -m pytest tests/test_windows_native.py -q
```

安装脚本校验 Python 官方包的固定 SHA256。每次执行采用唯一 AppContainer SID、零网络 capability、仅显式运行时/任务目录权限、三条标准 I/O 句柄白名单；进程在创建时原子归入 Job，限制 CPU、内存、单进程、输出和墙钟时间，Agent 退出会触发 kill-on-close。凭证在写入前收紧 DACL；本地工作目录也只授权当前账号。AppContainer 的 OS 安全配置由 Windows 管理，正常停止后删除；运行时、任务文件和日志均留在 tmp。独立运行时只能包含 vendor 文件，不能包含 Agent 代码、凭证、用户主目录或配置。

Windows 已支持受管 Ollama/ComfyUI：标准账号下通过独立 Job Object 约束 CPU、提交内存和子进程数量，启动前实际探测限制可用性。模型进程只监听回环，按其自有 Job 核对端口归属；空闲卸载、取消和 Agent 退出只回收自己的进程树。安装配置及 CPU/NVIDIA GPU 说明见 [Windows 模型支持](windows-models.md)。模型服务不要求配置 compute 的 embeddable Python；需要受控统计模板时才另行配置上述 AppContainer 运行时。Windows 真实模型和原生隔离验收仍待实机执行。实现依据：[AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)、[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[Python 包来源与哈希](https://www.python.org/downloads/release/python-31316/)。

Agent 被强制终止时，Job 的 kill-on-close 会停止 Worker，OS profile/只读 runtime ACE 可能需要清理。先停止原 Agent，再运行 `pcc-agent cleanup-windows --config agent.toml --confirm-stopped`；工具核对控制进程创建时间、独立推导的包 SID 和本地 runtime，只移除该工作目录中已停止的 PCC profile。活动任务日志仍须按 reconcile 流程核对，清理 profile 不会擅自重试任务。

## 配置示例

```toml
# Author: Fubuki
version = 1
server = "https://compute.example.com"
# 私有 CA 可显式指定 ca_file；默认使用标准 CA 信任库，绝不关闭证书校验。
# ca_file = "tmp/private-root-ca.pem"
name = "my-node"
credential_file = "tmp/agent-credential.json"
work_root = "tmp/agent"
mode = "shared"
allocatable_cpu = 2
allocatable_memory_mb = 2048
allocatable_vram_mb = 0
enable_compute = true
max_cpu_load = 85
max_memory_load = 90
heartbeat_seconds = 10
```

相对路径以配置文件目录解析；节点名称只用于显示。默认 Maintenance，只有本地与云端策略同时允许时才接新任务。Private 不会被云端 Shared 覆盖。修改模式、资源上限和负载阈值可热更新，传输、工作目录和后端配置变更需要重启。

Linux compute 要求系统安装 bubblewrap 且内核允许非特权命名空间；Agent 会实际探测，失败则不报告该能力。Worker 使用只读运行时、私有网络/PID/文件命名空间、CPU affinity、CPU 时间、地址空间、文件大小和句柄限制。不对任意第三方代码开放沙箱。CPU 模板配额至少一核，按向下取整的核数执行；小数核的通用计算尚不开放。

## Ollama（Windows / Linux）

由节点管理员从 [Ollama 官方](https://ollama.com/download/linux) 安装运行时，并预先准备有权使用的模型。PCC 不自动下载用户传入的模型、不连接云付费模型。参数由本地配置选择：

```toml
ollama_executable = "/absolute/path/to/ollama"
ollama_model_root = "tmp/ollama-models"
ollama_models = ["replace-with-admin-installed-model"]
ollama_port = 11435
# 默认 CPU；GPU 时明确指定本机设备 UUID，并设置经过验证的显存预算。
ollama_gpu_device = ""
ollama_vram_mb = 0
service_idle_seconds = 300
```

将已安装模型放在 `ollama_model_root` 指向的目录；若使用独立下载服务，同样设置 `OLLAMA_MODELS` 后再 pull。模型名称必须与云端模板 `model` 及 `worker=ollama:<model>` 一致，模板资源不得低于本地显存预算。GPU 设备 UUID 可通过 nvidia-smi 查询，不写入调度核心。

Linux Agent 必须有可用的 systemd 用户 scope 和 cgroup v2 CPU/内存限制；Windows 必须有探测通过的 Job Object 限制，缺少时拒绝能力。自有进程只监听回环端口；端口已占用时拒绝接管已有服务。服务按原生 `keep_alive: 0` 卸载，再终止 PCC 独立 scope/Job 以释放资源。活动请求期间不空闲卸载；多个模型由本地资源锁串行管理。显存为经过本地验证的预算、调度预留及加载/完成后的原生占用检查，**不具备硬件级显存分区**；不应与不可预测的本地 GPU 高负载共用超额预算。

Chat 请求当前在后端完成后由 Server 以 SSE 分块返回。图片提供 synthetic 协议测试和下文的 ComfyUI 适配器；不能将合成图片视为真实模型效果。

## 断线与核对

Agent 持续重试出站连接；本地模式不会因断线被改回 Shared。执行中独立租约守卫会停止自有 Worker。领取 ID 持久化，响应丢失后复用；完成确认丢失时按原结果幂等重发。手动核对前先停止原 Agent；新日志记录控制进程 PID/创建时间，以及 Linux scope 或 Windows Job 标识，原 Agent 仍存活时拒绝核对。

丢失领取响应后，即使租约已过期，仍能取回原 attempt 并报告未启动/已停止；过期租约不会恢复执行权限。存在 active-attempt 日志时，启动阶段不运行隔离探测 Worker，也不预热新的模型；reconcile 命令同样跳过探测。Windows 创建过程如不能确认清理完成，会保留该日志并停止 Agent，等待人工核对，不会上报 stopped=true。新模型启动前必须确认旧模型已停止。

如 `tmp/agent/active-attempt.json` 遗留，Agent 停止接新任务。先查看其中记录的 PID/创建时间或 PCC scope，确认原进程和子进程终止后运行：

```bash
pcc-agent reconcile --config agent.toml --confirm-stopped
```

若凭证已被吊销，由 Owner 在服务端人工核对 API 记录停止证据。非幂等任务默认不自动重试。不要通过删除 journal 强行恢复，以免发生并行副作用。

## ComfyUI 图片适配器（Linux）

实现了真实本地 ComfyUI 队列/历史/结果协议，当前只完成自动化协议测试，尚未在实际图片权重上验收。节点管理员预先安装 ComfyUI、PyTorch 与合法权重，导出 API 格式工作流，再配置：

```toml
comfyui_python = "/absolute/path/to/comfy-venv/bin/python"
comfyui_script = "/absolute/path/to/ComfyUI/main.py"
comfyui_port = 8189
comfyui_gpu_device = "LOCAL_GPU_UUID"

[comfyui_workflows.my-image-model]
file = "approved-image-workflow.json"
prompt_node = "6"
latent_node = "5"
min_vram_mb = 4096
```

工作流必须由管理员审查，节点类型只允许 CheckpointLoaderSimple、CLIPTextEncode、EmptyLatentImage、KSampler、VAEDecode、SaveImage。启动禁用所有 custom nodes；普通用户不能上传/替换图，不接入付费 API 节点。仅替换 CLIP 文本和有界 latent 尺寸（batch=1）。模型名为本地 workflow 映射名称，云端使用 `kind=ai.image`、`worker=comfyui:my-image-model`、`model=my-image-model` 和对应资源预算。

实例仍使用 Linux 用户 cgroup 与回环监听，只管理自己的进程。单次请求下载有界 PNG，路径只接受批准的输出形式；PCC 专用临时输出目录在服务停止后清理。空闲先调用原生 `/free`，再终止自己的 scope；不会向现存 ComfyUI 服务发送全局终止。

协议依据 [ComfyUI 官方示例](https://github.com/comfyanonymous/ComfyUI/blob/master/script_examples/basic_api_example.py) 与[服务器接口](https://github.com/comfyanonymous/ComfyUI/blob/master/server.py)。上线前需在管理员实际版本、工作流、模型与 GPU 上完成一次生成和卸载验收。
