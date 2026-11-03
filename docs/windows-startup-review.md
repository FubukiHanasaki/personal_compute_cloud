# Windows 启动与清理问题复查

作者：FubukiHanasaki。日期：2026-11-03。

本次依据反馈截图核对当前仓库；未访问产生截图的 Windows 主机，也未取得该机尚未提交的本地修改。以下修复已在 Ubuntu 完成定向模拟与静态检查，不能视为 Windows 原生验收通过。Ubuntu Server 的依赖安装可以继续。

## 后续 Windows 复测与超时停止修复

用户提供的目标机记录显示：更新至 `fd50210`，在新路径安装运行时后，普通执行、AppContainer 计算、账号权限跨清理保留、私有文件与回环隔离、模型 Job 句柄停止确认已跑通；此前 `0xC0000022` 未再复现。首轮为 30 passed / 2 skipped（真实模型未配置），但伴有后台 Timer 线程异常，不能作为干净验收结果。启用线程异常即失败后，资源限制定向复测为 1 failed / 2 passed / 3 deselected；失败定位到超时停止中的 `TerminateProcess`，WinError 5。以上是用户提供的复测记录，本环境未直接读取目标机 XML。

代码确认存在重复终止的竞态：Worker 在 `CreateProcessW` 时已原子归入 Job，`stop()` 调用 `TerminateJobObject` 后又在退出尚未完全结束时调用 `TerminateProcess`。删除 Job 路径的第二次终止；仅在没有 Job 的防御分支保留直接终止。仍等待具体进程退出，并查询 Job 的活动进程数归零；不捕获后忽略 WinError 5，也不把终止请求成功视为退出已确认。[Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)说明终止为异步操作，且再次终止已退出进程可能返回拒绝访问。

新增 4 项回归检查：Job 退出延迟与重复停止、不带 Job 的防御分支、真实终止错误继续抛出、进程退出后 Job 仍活动时拒绝确认并保留句柄。Windows CI 将 `PytestUnhandledThreadExceptionWarning` 和 `PytestUnraisableExceptionWarning` 视为失败。

本次 Ubuntu 定向复查 **66 passed / 13 skipped**，上述两类警告已设为错误；无后台线程/不可报告异常警告，仍有既有 Starlette/httpx 弃用提示。结果位于 Git 忽略的 `tmp/windows-timeout-review/contracts.xml`。Windows 超时、输出和内存原生用例仍需拉取修复后以同等严格警告设置重跑；当前继续保持 maintenance / enable_compute=false，未满足注册、运行或自启动的验收条件。

## 首次四项问题复查（历史结论）

| 反馈                              | 仓库核对与处理                                                                                                                                                                                                                  | 尚需确认                                                                                                   |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Worker 缺少 `LOCALAPPDATA`        | 代码确实未提供。补齐 `LOCALAPPDATA`、`APPDATA`、`USERPROFILE`、`WINDIR`，路径均指向本次任务目录；不继承宿主个人目录、代理或凭证。Python 的 UTF-8 改用 `-X utf8`，避免 `-I` 忽略环境设置。                                       | 目标机需要重新探测；不能由此断言所有 WinError 203 都已消失。                                               |
| 独立 Python 的账号 ACL 异常       | 旧安装脚本对文件和目录统一使用目录继承标志。改为先授权当前账号，再移除继承，文件/目录分别处理，安装结束实际执行 stdlib 导入检查。Worker 只添加/移除经验证的私有 AppContainer SID 权限，不重置运行时 DACL。                      | 截图所述账号权限丢失已发生，但尚未证明由安装还是后续操作引起；新版不会自动修复已有安装。                   |
| AppContainer 以 `0xC0000022` 退出 | 仍未定位根因。错误与探测日志现在保留十六进制原生状态；启动阶段写管道失败时也尽量取回退出码。运行时逐文件授予私有 SID 的 RX 权限，避免对文件使用目录继承标志。                                                                   | 需要 Windows 检查 EXE、DLL、标准库 ZIP、祖先目录可达性和 AppLocker/WDAC 策略。无输出不足以确认具体拒绝点。 |
| Job 清理后 PID 仍存在             | 生产停止逻辑已有 Job 活动进程归零和主进程等待。原测试只判断 PID 是否存在，证据不足；改为预先打开具体子进程句柄，停止后等待其进入已退出状态，并检查 Job 无活动进程。Agent 异常退出用例增加握手，先捕获子进程句柄再释放控制进程。 | 新原生测试未执行，不能宣布泄漏已修复或不存在；若句柄等待超时或 Job 仍活动，继续视为停止未确认。            |

Windows 的进程终止可能异步完成，进程对象也可能因其他句柄而保留；`pid_exists` 不能单独证明进程仍在执行。依据：[TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)、[WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)。ACL 参数含义见 [icacls](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/icacls)。

## Windows 后续定位顺序

1. 保留现有未提交的本地补丁、错误日志和运行时 ACL 快照，再合并本次修改；不要直接 reset 覆盖现场。
2. 用运行 Agent 的同一个标准账号，执行独立运行时的 `python.exe -I -B -c "import json, statistics"`。普通执行也失败时，优先处理运行时账号权限或主机策略。按 `deploy/prepare-windows-worker.ps1` 在新的绝对路径安装批准的干净运行时并配置，保留旧目录供对照；脚本拒绝覆盖已有目录。
3. 普通执行成功后再运行 `pcc-agent check` 与 Windows 原生测试。新增用例在 AppContainer 使用前后都验证账号仍能运行独立 Python，帮助判断权限是否在清理过程中改变。
4. 若仍为 `0xC0000022`，用目标机 Process Monitor 按本次 `python.exe` 进程和 `ACCESS DENIED` 查看被拒路径，同时查看 AppLocker 与 CodeIntegrity 日志。记录具体文件及其 ACL，再决定修正；不要给共享 ALL APPLICATION PACKAGES 授权整个用户目录，也不要以管理员运行或关闭隔离来伪装通过。
5. Job 清理以具体进程句柄已退出且 Job 无活动进程为依据；停止无法确认时继续保留恢复记录并拒绝新增执行。计算探测失败仍不报告 compute 能力。模型 Job 能力另行探测，不因 AppContainer 失败而绕过模型限制。

## 已执行验证与边界

Ubuntu 定向测试 **62 项通过、13 项跳过**，结果保存于 Git 忽略的 `tmp/windows-startup-review/contracts.xml`。覆盖环境白名单、私有 SID 授权/回收参数、原生状态诊断、探测失败不开放能力、现有停止确认与模型契约；包含 13 项新增可移植检查。跳过项是 6 项 AppContainer 和 7 项模型原生/可选真实模型用例。存在一条既有 Starlette/httpx 弃用提示。

Python Ruff、CI YAML 格式、内嵌原生测试脚本语法与 Git 差异空白检查通过。Windows CI 已加入新增可移植测试，但本轮未取得远端 CI 结果。当前没有 PowerShell，安装脚本仅静态核对，未在 PowerShell 执行；未启动 Windows 进程、真实模型或 Server。最终 Windows 验收仍需目标机执行上述步骤。
