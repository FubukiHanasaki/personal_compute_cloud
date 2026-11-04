# 变更记录

作者：FubukiHanasaki。

## 未发布 · 完整工作空间重构、模型维护与持久对话 · 2026-11-04

重组总览、模型对话、图像创作、计算任务、计算节点与模型管理；采用可更换的薄荷绿 / 雾蓝七色主题和系统中文字体。节点 CPU、内存与显存用独立资源卡呈现，图像结果改为画廊，模型维护使用独立编辑视图。

管理员可维护已有模型的上下文、输出上限、KV、采样、资源及卸载默认值；保留 Owner 的登记和授权权限，拒绝在未结束任务期间变更配置。Agent 安全重建旧参数进程；增加 NInfer 回环受管服务及本地模型映射 GUI，提供 Qwen3.5/3.6/3.8 27B、NInfer Qwen3.6/3.8 27B、Qwen3-VL-8B-Instruct 和 Qwen Image 2.1 预设 / 原生 ComfyUI 示例。

新增数据库迁移 0006：按用户持久保存对话、重命名 / 删除、消息重试防重、刷新恢复。支持 Enter 发送、Shift+Enter 换行与中文输入法保护；增加受限内嵌图片、视频四帧采样问答、参考图编辑。长中文回复保持有界输出并可继续作为历史，不再受普通任务的 64 KB 元数据上限阻断。真实权重与目标 Windows/GPU 未执行时仅记录协议和逻辑验证。升级需先备份，停止进程后执行数据库迁移再启动。

验证与范围见[本轮记录](docs/workspace-review-2026-11-04.md)，用法见[模型与会话](docs/models-and-conversations.md)。

## 未发布 · CI 构建与 Windows 帮助输出修复 · 2026-11-04

修复 Docker 前端构建阶段缺少共用 theme.css：显式复制到实际导入位置，并新增基于 Docker COPY 布局的 CSS 依赖检查。更新脚本在解析参数前将输出流统一为 UTF-8，避免 Windows 重定向旧代码页使中文帮助文本报错；补充 cp1252 回归。平台矩阵关闭 fail-fast，让其他平台继续给出独立验证结果。

## 未发布 · 界面与本机配置 · 2026-11-04

重做服务端浅色界面，使用共用七色主题与系统中文字体；减少重复说明，增加任务进度、节点资源仪表、模型登记表单、勾选授权和公开范围切换。Agent 新增仅本机可访问的配置 GUI、实际资源采样、连接检查和注册入口，保留 HTTPS、受限凭证写入、配置冲突与跨站防护。

新增 Windows、Linux、macOS GitHub 检查/快进更新脚本；默认只检查，拒绝覆盖本地改动，不自动重启或迁移。使用入口、行为与验证边界见[界面与更新说明](docs/ui-and-updates.md)。SQLite/PostgreSQL 各 151 项通过、15 项条件跳过；Ubuntu 实际浏览器校验、前端构建和安装包检查通过，详见[收尾记录](docs/ui-review-2026-11-04.md)。

## 未发布 · Windows 超时停止竞态 · 2026-11-03

修复 Worker 已经通过 Job 请求终止后，再次调用 TerminateProcess 导致 WinError 5 的竞态；保留进程退出和 Job 活动进程归零的停止确认。Windows CI 将后台线程和不可报告异常警告视为失败，避免测试通过计数掩盖停止线程异常。

Ubuntu 定向检查 66 项通过、13 项 Windows 用例跳过；新增 4 项回归覆盖退出延迟、无 Job 分支、终止错误和停止未确认。目标 Windows 需再次严格复测，继续保持计算禁用。详见 [启动与清理复查](docs/windows-startup-review.md)。

## 未发布 · Windows 启动问题复查 · 2026-11-03

补齐 Worker 私有配置目录环境；运行时 ACL 区分目录和文件，先保留当前账号访问并执行安装后检查；限制授权/回收为私有 AppContainer SID，保留探测与执行的原生退出码。原生 Job 清理测试改用具体进程句柄与退出握手，避免只用 PID 存在判断泄漏。

Ubuntu 定向检查 62 项通过、13 项 Windows 用例跳过。`0xC0000022` 根因和既有账号 ACL 异常来源仍需目标机定位，未宣称 Windows 验收通过。详见 [复查记录](docs/windows-startup-review.md)。

## 未发布 · Windows 模型逻辑与安全复查 · 2026-11-03

修复监听 PID 复用核验、后台预热与关停竞争、停止失败清理中断、模型复用绕过收紧额度及预热负载保护；限制后端 JSON/Chat/图片响应并核对时间与取消，完整工作流结束后清理图片历史和临时输出。GPU 配置收紧为一张完整 UUID，Windows Job 内存参数防止溢出；推理执行器在停止失败时同样关闭任务入口。

Ubuntu 定向模拟/契约检查 57 项通过，7 项 Windows 用例跳过；未启动真实模型、Server、GPU 或 Windows 进程。署名 FubukiHanasaki。详见 [复查记录](docs/windows-models-review.md)。

## 未发布 · Windows 受管模型 · 2026-11-03

新增 Windows Ollama/ComfyUI 支持：实际 Job Object 能力探测、创建时原子加入 Job、CPU/提交内存/进程数限制、整个自有进程树停止与 kill-on-close、回环端口归属校验、白名单环境和 Windows 恢复标识。统一两个平台的受管进程接口，保留 Linux cgroup 行为；CPU 模式禁用自动 GPU，显式 GPU 不静默回退 CPU。

新增 Windows 模型配置及 SD1.5 工作流示例、原生 Job/协议夹具/可选真实模型测试和 CI 入口。Ubuntu 定向模拟/契约检查 29 项通过，7 项 Windows 用例跳过；未执行 Windows 实机或真实模型测试。具体范围见 [Windows 模型支持](docs/windows-models.md)。

## 未发布 · 后续实现补齐 · 2026-11-03

新增 Windows AppContainer/Job Object 受控 Worker、独立 Python 运行时校验、写入前 SID ACL 与停止后 profile 清理；实际计算探测成功后才报告能力。提供 Windows 原生安全测试和 CI，当前 Ubuntu 未运行这些用例。

新增可校验、可搬移的 PostgreSQL/工件快照及新目标单事务恢复，Compose 包装脚本使用流式归档；真实清空恢复、登录与授权下载演练通过。实际 Caddy 验证 TLS、出站 Agent、Server 重启持久性通过。新增完整 Compose 部署/恢复 CI，尚未触发。

ComfyUI/SD1.5 真实 GPU、普通用户 HTTPS API、512×512 图片任务与 SHA256 下载核验通过，空闲卸载后显存恢复基线。增加 Torch 显存池预算与独立可写目录，关闭自定义/API 节点及 DynamicVRAM，限制等待时间；修复 TIME_WAIT 误判和 HTTP 连接释放。

最终 PostgreSQL 59 项通过/5 项 Windows 跳过，SQLite 58 项通过/6 项跳过，前端 3 项通过，生产构建与 Python 包检查通过。Windows 实机、生产 Docker/公网域名/NAT 及此前暂跳过的 Portal 视觉验收仍未完成；保持开发版。详见 [补齐与核验记录](docs/completion-v0.4.md)。

## 未发布 · V0.4 自检修复 · 2026-11-03

修复图片历史独立筛选/分页、管理资源选择器截断、中文 SSE 意外断流与错误恢复、旧用户迟到查询、旧成功提示残留，以及窄屏节点布局和弹窗焦点处理。模型与模板接口补齐授权后的分页；测试/CI 临时目录统一放入 tmp。

SQLite/PostgreSQL 各 40 项测试、前端传输 3 项及生产构建通过；部署配置复验通过。用户要求本轮先跳过视觉校验，未产生浏览器或移动端通过结论。详见 [自检记录](docs/self-check-v0.4.md)。

## v0.1.0-dev · 2026-11-03

按 P0–P6 提供控制面、出站 Agent、节点能力与共享策略、受控统计任务、持久租约与停止核对、授权工件、用户/会话/密钥、模型生命周期、Chat/SSE、图片接口、Ollama/ComfyUI 适配器及中文管理台/Portal。

SQLite/PostgreSQL 各 38 项测试通过，真实 HTTP/隔离计算闭环与真实 GPU 模型生命周期已验证。前端、Python 包、独立安装入口和部署配置校验通过。

本版本为开发版。Windows 等价执行隔离和实机验收、实际图片模型、真实 Docker HTTPS/恢复演练和浏览器 QA 尚待完成。详见 docs/acceptance.md。
