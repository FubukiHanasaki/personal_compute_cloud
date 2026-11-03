# Windows Ollama / ComfyUI

作者：FubukiHanasaki。日期：2026-11-03（Asia/Shanghai）。

Windows 节点已接入受管 Ollama Chat 与 ComfyUI 图片模型的同一套注册、能力发现、服务预热、按需推理、空闲卸载、取消和停止核对流程。Server API/模板结构与 Linux 保持一致，无需另一套控制面。支持 CPU 或显式选择的一张 NVIDIA CUDA GPU；AMD/Vulkan 适配不在本轮实现范围，避免使用未受当前资源报告覆盖的设备。

最新逻辑与安全复查已修复监听 PID 复用、关停并发、资源额度复用、预热负载保护、响应上限和图片临时数据积累问题，定向模拟检查 57 项通过、7 项 Windows 用例跳过。详见 [复查记录](windows-models-review.md)。下文原 29 项结果保留为初次实现记录。

## 准备与配置

1. 使用 64 位 Windows 和 Python 3.12–3.14，按 [Agent 安装](agent.md) 安装到专用标准账号；运行时不使用 Administrator/最高权限任务。
2. 准备本地批准的 Ollama CLI；使用 ollama.exe，不运行托盘应用来代替受管进程。可使用官方独立 Windows CLI 包，保留同目录所需 GPU 库；按官方流程将模型预先安装到配置指定的模型目录。
3. 准备手动安装的 ComfyUI、对应 Python/Torch 环境及本地 checkpoint。配置只允许批准的工作流，关闭自定义节点/API 节点。PCC 不代用户安装不可信节点或下载未知模型。
4. 新节点将 [配置示例](../deploy/agent-windows-models.example.toml) 复制到 checkout 根目录的 agent.toml，替换服务器、运行时、模型名及工作流路径；已有配置时合并模型字段，保留原身份/路径/策略。只启用 Ollama 时移除 comfyui_* 和工作流段；只启用 ComfyUI 时移除 ollama_* 配置。

```powershell
# 在 checkout 根目录，用专用标准账号运行：
Copy-Item deploy\agent-windows-models.example.toml agent.toml
notepad agent.toml
# 编辑批准工作流，ckpt_name 必须是 ComfyUI/models/checkpoints 中已安装的文件名。
notepad deploy\workflows\sd15-api.example.json
& tmp\agent-venv\Scripts\pcc-agent.exe register --config agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe check --config agent.toml
& tmp\agent-venv\Scripts\pcc-agent.exe run --config agent.toml
```

示例默认 Maintenance、CPU 模式。确认配置后，在本地和云端均设为 Shared 或所需授权模式。check 应报告 windows-managed-job 和 ollama:模型名 / comfyui:local-image；探测失败不报告这些能力。模型服务不依赖 windows_worker_runtime，后者仅用于 AppContainer 计算模板。

Ollama 默认使用 PCC 独立回环端口 11435，ComfyUI 使用 8189，可以保留用户自己的 11434/8188 服务；不要将 PCC 端口改成已有服务端口，PCC 会拒绝接管。Windows 使用排他端口预检，API 请求前还核对监听器属于自有 Job 的进程。

在 Server 批准 ai.chat 模板，worker=ollama:APPROVED_LOCAL_MODEL，model=APPROVED_LOCAL_MODEL；批准 ai.image 模板，worker=comfyui:local-image，model=local-image。模板资源必须足以容纳模型、后端及子进程的峰值 CPU/RAM/VRAM，且不超过本地 allocatable_*。使用现有 Portal、/v1/chat/completions 和 /api/v1/images/generations。

## GPU 与资源边界

CPU 模式保留 GPU 配置为空，禁用 CUDA/HIP/ROCm/Vulkan 自动设备选择。启用 NVIDIA 时，使用 nvidia-smi -L 得到管理员批准的 GPU UUID，分别填写 ollama_gpu_device/comfyui_gpu_device，设置 allocatable_vram_mb、ollama_vram_mb 和工作流 min_vram_mb，并使模板预算匹配实际峰值。ComfyUI 保留 Torch 池预算及 context 余量、关闭 DynamicVRAM；Ollama 按原生 /api/ps 核验预算。显存预算不是硬件分区。

显式配置 GPU 后，Ollama 未报告模型显存占用或 ComfyUI 未报告 CUDA 设备时拒绝静默回退 CPU。已有 Agent 升级时先停止旧进程，用其 venv 执行 python -m pip install --cache-dir tmp/pip-cache --upgrade .，合并配置后再启动，不重复初始化或覆盖原注册身份。

GPU 字段现在只接受空值或一张完整的 GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx UUID。升级前若使用设备序号、缩写或多个设备，须替换为 nvidia-smi 返回的完整 UUID；配置错误会拒绝启动。

每个后端启动一个独立命名 Job。CPU 使用硬比例上限；内存限制覆盖每个进程及整个 Job 的提交内存；活动进程数最多 256，未设置 breakaway。进程在创建时加入 Job 后才恢复运行，只继承空 stdin 和日志句柄。启动环境采用白名单，模型 HOME/APPDATA/TEMP 位于工作目录，不继承 PCC_SECRET、代理或节点凭证环境。

受管模型运行时仍使用节点账号，属于管理员批准代码；这些限制不等价于 compute 的 AppContainer 文件/网络沙箱。模型只监听本机回环，不开放入站端口，不允许云端用户指定本机可执行文件、任意图或路径。

活动请求期间不空闲卸载。模型切换需先确认旧后端停止，失败则保留错误状态与预算。停止会终止自有 Job 并检查 ActiveProcesses=0，即使主进程先退出也回收子进程；Agent 崩溃后 kill-on-close 生效。不会按进程名称、端口或全局 taskkill 终止外部进程。

恢复日志记录原 Agent PID/创建时间及 managed_job。先停止原 Agent、确认原进程树，再在原 Windows 主机运行 reconcile --confirm-stopped；仍存活的 Agent/Job 或无法查询状态均拒绝停止确认，不触发自动重复推理。

## 检查入口与当前证据

已提供不依赖 Windows/模型的进程契约和 mock HTTP 检查；Windows 原生检查另行覆盖 Job 限制、父进程先退出后的子进程清理、Agent 退出 kill-on-close，以及 stdlib HTTP 协议夹具。协议夹具不是真实模型。

```powershell
# 在标准账号下安装测试依赖：
& tmp\agent-venv\Scripts\python.exe -m pip install --cache-dir tmp\pip-cache '.[test]'
& tmp\agent-venv\Scripts\python.exe -m pytest tests/test_managed_process.py tests/test_windows_model_review.py tests/test_windows_models_native.py -q
# 使用已批准且已安装的本机模型，显式选择真实模型测试：
$env:PCC_WINDOWS_MODEL_CONFIG = (Resolve-Path agent.toml).Path
& tmp\agent-venv\Scripts\python.exe -m pytest tests/test_windows_models_native.py -k opt_in_real -q
```

Windows CI 已包含原生协议/Job 检查入口；真实模型测试没有配置时明确跳过。当前执行环境为 Ubuntu，未把 Windows 原生或真实模型检查记为已通过；实机 CPU/GPU、驱动、重连与部署验收仍需在目标 Windows 完成。临时环境、日志、凭证、测试输出和构建产物均放在被 Git 忽略的 tmp。

本轮 Ubuntu 定向模拟/契约检查：29 项通过，新增 Windows 原生/真实模型用例 7 项跳过。覆盖原子 Job 归属、名称碰撞不接管、未确认停止保留句柄、Windows 排他端口、启动环境白名单、平台能力上报、Linux cgroup 启动契约、两个模型后端的 mock HTTP 生命周期、CPU 模式、显式 GPU 不静默回退 CPU、示例工作流与恢复拒绝。没有启动真实 Server、模型或 Windows 进程；这不是完整服务端回归或 Windows 实机结果。仍有一条既有 Starlette/httpx 测试工具弃用提示。

63 个 Python 文件与内嵌协议夹具的 AST 解析、变更文件的 Ruff 错误/格式检查、示例 TOML/JSON 与 CI YAML 检查通过。wheel/sdist 已构建并核对含新适配器及配置/工作流示例，不包含真实凭证或模型权重；构建和定向检查报告保存在 tmp/windows-model-support。

实现依据：[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)、[原子进程 Job 属性](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、[CPU 硬比例限制](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information)、[Ollama Windows CLI](https://docs.ollama.com/windows)、[设备选择](https://docs.ollama.com/gpu)、[ComfyUI 启动参数](https://github.com/Comfy-Org/docs/blob/main/development/comfyui-server/startup-flags.mdx)。
