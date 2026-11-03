# 变更记录

作者：Fubuki。

## 未发布 · Windows 模型逻辑与安全复查 · 2026-11-03

修复监听 PID 复用核验、后台预热与关停竞争、停止失败清理中断、模型复用绕过收紧额度及预热负载保护；限制后端 JSON/Chat/图片响应并核对时间与取消，完整工作流结束后清理图片历史和临时输出。GPU 配置收紧为一张完整 UUID，Windows Job 内存参数防止溢出；推理执行器在停止失败时同样关闭任务入口。

Ubuntu 定向模拟/契约检查 57 项通过，7 项 Windows 用例跳过；未启动真实模型、Server、GPU 或 Windows 进程。署名 Fubuki。详见 [复查记录](docs/windows-models-review.md)。

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
