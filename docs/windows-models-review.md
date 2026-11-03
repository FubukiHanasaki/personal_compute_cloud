# Windows Ollama / ComfyUI 逻辑与安全复查

作者：Fubuki。日期：2026-11-03（Asia/Shanghai）。

对照 V0.4 的受批模板、本地策略优先、资源上限、模型生命周期、活动请求保护、独立租约守卫和停止核对要求，复查 Windows 模型适配、共用生命周期、配置、任务执行和恢复入口。此前用户已跳过视觉验收，本轮不启动真实模型、Server、GPU 或 Windows 进程。

## 发现与修复

| 范围 | 原问题与本次修复 |
| --- | --- |
| Windows 监听归属 | Job 的 PID 快照可能过时，单独按 PID 查询连接存在复用窗口。现在使用只读进程句柄，以 IsProcessInJob 核对实际内核对象属于自有 Job，并在 socket 查询前后检查对象仍存活；退出、非成员或查询失败均不接受。查询与 Job 关闭使用同一锁，句柄在 finally 释放 |
| 关停与预热并发 | 后台预热结束后可能把已停止服务回写 ready/idle；原 stop_all 遇到一个失败就中断清理。增加关闭状态和后端身份核对，阻止重新取得服务或预热；逐个尝试清理，保留失败后端并抛出 WorkerStopUnconfirmed，不掩盖未确认停止 |
| 本地资源变化 | 原本仅新建后端时核对额度，复用已加载模型可能绕过收紧后的本地配置。现在每次取得服务均重新核对，结合能力发现对物理资源的截断值；超额服务等待活动请求结束后卸载。手动预热同样遵守最近心跳的 CPU/内存负载阈值 |
| GPU 与 Job 参数 | GPU 字符串原可配置多个设备或不稳定的序号。现在两个配置字段只接受空值（CPU）或一张完整 NVIDIA GPU UUID；不再接受 0、0,1 或缩写。Windows Job 内存参数增加 SIZE_T 上界校验，避免巨大数值溢出后形成错误限制 |
| 后端响应与时间 | 原 metadata、Chat JSON 完整读取后才处理，Chat 未接收 attempt 的输出额度；图片分块列表也会放大小块开销。现在共用受限流式 bytearray：metadata 最多 1 MiB；Chat 原始 JSON 最多 min(6 × 输出额度 + 64 KiB, 20 MiB)，正文另按 UTF-8 字节核对 attempt 额度；图片按工件额度读取。请求显式要求 identity 编码，拒绝压缩响应，避免解压后才检查上限 |
| 请求停止与超时 | 逐块读取和读取结束均核对停止标记与单调时钟期限，图片提交、历史查询、下载和清理共用本次期限。HTTP 另保留读超时；attempt 的独立租约守卫继续负责超时/失联时停止整个进程树，不能仅靠 HTTP 超时证明任务已停止 |
| ComfyUI 完成与积累 | 只有出现部分图片输出时原代码就可能返回。现在等待 completed 后再取图；完成后仅删除当前 prompt 的服务端历史，并清理自有 staging/output、staging/temp，避免忙碌服务持续积累图片。拒绝顶层符号链接/junction，清理和停止串行；失败由原错误停止路径回收整个 staging。/free 和 /history 的成功空正文正常处理 |
| 执行器与检查用例 | 关闭推理 Worker 时，即使进程停止失败，也会关闭执行器的新任务入口；不等待尚未结束的 future 来伪造停止成功。保留后端及 attempt 日志。修正模拟用例在 Windows 上误用真实监听检查的问题，并将新增回归纳入 Windows CI 入口 |

## 复核覆盖

- 创建进程仍使用固定参数列表、批准的本地路径和模型，挂入 Job 后才恢复执行；不启用 shell、breakaway，不按名称或端口杀外部进程。
- Job 继续限制整个进程树的 CPU、提交内存及活动进程数；主进程先退出仍查询/清理子进程，关闭最后 Job 句柄保留 kill-on-close 行为。名称碰撞拒绝接管。
- 两个后端只监听回环，API 前核对监听归属；环境不继承节点凭证或代理。CPU 模式显式禁止自动 GPU 选择，显式 CUDA 配置不接受 CPU 回退；保留显存预算核对。
- ComfyUI 只接受本地批准的节点类型和工作流，远程用户只绑定 prompt/size；文件名、子目录及工件大小限制保留，关闭自定义/API 节点和 DynamicVRAM。
- 单模型锁、活动请求不空闲卸载、本地用户/模式策略、租约取消与过期结果隔离继续生效；模拟执行覆盖未确认模型停止时不发送 complete/fail(stopped)，并保留恢复日志。
- 恢复入口仍拒绝存活的原 Agent、自有活动 Job、错误平台或无法确认的状态，不通过恢复工具终止按名称打开的 Job。

Windows API 合约依据 [IsProcessInJob](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob) 和 [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess)；ComfyUI 删除历史与空成功正文依据[官方 server.py](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py)。这些是接口核对，不是目标 Windows 的执行证据。

## 检查结果

Ubuntu 下定向模拟/契约回归 **57 项通过，7 项 Windows 用例跳过**。覆盖新增边界以及既有 Job ABI、ACL 写入次序、GPU 回退、原子启动、名称碰撞、部分启动、停止失败、跨平台恢复拒绝、负载和额度变化、慢速流、取消、超大响应、压缩响应、UTF-8 正文额度及部分图片历史。仅用 fake Win32/process、mock HTTP 和临时文件；没有真实服务端、模型、Windows 或 GPU 测试。仍有一条既有 Starlette/httpx 弃用提示。

复现清单、JUnit、日志与静态扫描输出位于 Git 忽略的 tmp/windows-model-review；新增用例位于 tests/test_windows_model_review.py，原有用例也补充了 PID 复用与图片清理检查。Windows CI 已纳入新增文件，尚未触发远程 CI。

Agent 全量 Ruff S 类扫描产生 19 条提示并逐项复核：S603 × 11 与 S607 × 4 为批准路径/固定本机命令；S606 为受控 execv 包装；S311 为重连抖动；S108 为计算沙箱私有 tmpfs；S110 为读取已停止 future 的异常，随后必须检查完成状态及错误后端。未引入新的用户命令入口。语法、关键错误规则、变更格式及包内容检查见同目录 static-checks.json；wheel/sdist 构建产物同样位于 tmp。

## 结论边界

本轮已修复所列逻辑与安全问题，并完成静态及模拟验证。Windows 原生 Job、CPU/GPU 真实模型、驱动、重连与目标服务器联调仍未执行；既有早期实测结果不能替代本轮修改的目标环境验收。

受管模型是节点管理员批准的代码，运行在专用标准账号下；Job 是进程/资源边界，不是计算模板的 AppContainer 文件/网络沙箱。回环端口不是本机用户间的认证边界，部署假设节点本地账号可信。显存不是硬件分区；后端日志/运行时缓存也没有磁盘硬配额。本轮未进行依赖漏洞数据库审计或渗透测试，不作“绝无漏洞”结论。
