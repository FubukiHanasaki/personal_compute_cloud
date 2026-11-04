# 模型支持复验 · 2026-11-04

作者：FubukiHanasaki。

## 结果

本轮在 Ubuntu 对模型适配、配置、权限、任务持久化和浏览器重新验收。SQLite 全量 172 通过 / 15 条件跳过；独立 PostgreSQL 全量 174 通过 / 13 Windows 原生跳过，包含真实 HTTPS 与备份恢复。启用后台线程异常和不可回收异常的 pytest 错误模式，没有此类警告。保留一条已有 Starlette/httpx 弃用提示。

生产前端构建、前端 API 3 项测试、Python F/E9、格式与 Git 空白检查通过。wheel / sdist 离线构建通过，已核对 wheel 含模型模块、最新配置校验和 GUI 静态资源。Ubuntu Chromium 完成 25 张桌面 / 手机截图和完整交互检查，没有页面脚本异常或 HTTP 5xx。

## 模型矩阵

| 接入                           | 已实际检查                                                                                                  | 未实际检查                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Ollama Qwen3.5 / 3.6 / 3.8 27B | 三个预设逐一通过浏览器登记和服务端持久化；参数、上下文、KV、视觉请求、权限和进程生命周期契约                | 三份真实 Qwen 27B 权重加载、质量与显存需求                              |
| Ollama Qwen3-VL-8B-Instruct    | 预设登记、视觉消息转换、非思考模型省略不支持的开关、拒绝未授权能力                                          | 真实 8B 视觉权重问答和目标节点性能                                      |
| NInfer Qwen3.6 / 3.8 27B       | 两个预设登记；启动参数、CUDA UUID、显存预算、上下文池、模型别名与 OpenAI 多模态请求契约，并对照官方文档复查 | 匹配的 NInfer 二进制、RTX 5090 和真实权重执行；Windows 原生支持         |
| ComfyUI Qwen Image 2.1         | 预设登记；示例图与官方节点定义对照；提示词、画幅、步数/CFG/种子、0–4 张参考图绑定、危险路径拒绝与工件限制   | Qwen Image 2.1 的匹配 diffusion / text encoder / VAE 实际加载和编辑质量 |

7 个预设均从浏览器选择并提交到真实本地 HTTP 服务，再读取服务端模板核对适配器、模型 ID、视觉 / KV / 上下文或图像默认值；不只是检查选项存在。

NInfer 官方当前构建为 Linux RTX 5090 / DGX Spark，Qwen3.6 / 3.8 27B 列为 RTX 5090 支持项。文档和 GUI 原先使用 Windows `.exe` 示例，容易被误认为官方 Windows 运行已验证，已改为 Linux 示例并标明边界。配置入口不等于目标环境已可用。

## 本地真实 GPU 后端复验

当前 GPU 为 RTX 3060 Laptop、6144 MiB。使用 tmp 中已有二进制和权重，不下载大模型、不连接生产服务；分别使用独立回环端口和受管 systemd scope，保留 CPU / 内存 / GPU 配额及监听归属检查。

- Ollama / smollm2:135m：上下文 2048、输出上限 32、温度 0，实际返回 `Hello!`；模型上报显存 332545391 字节，耗时 31.07 秒，原生卸载后确认受管进程停止。
- ComfyUI / SD1.5：256×256、10 步、CFG 7、种子 123456；实际 PNG 76338 字节，SHA-256 `f72ec2ab3d5cda59d7ced1263ea24c399df2b17182ded538be3666d3ab91e88d`，耗时 15.34 秒，图片可解码，生成后清理并确认受管进程停止。

以上证明当前适配器仍能使用真实 GPU 完成推理及停止，不替代 Qwen / NInfer 目标模型验收，也不评价小模型答案或 SD1.5 图像质量。

## 修复

`ComfyWorkflow.image_nodes` 原先允许重复 ID；多个参考图片绑定同一 LoadImage 时，后续绑定可覆盖或删除前一绑定。现在配置解析直接拒绝重复，并增加回归测试；不更改已正确配置的工作流。接口权限、CSRF、用户参数白名单、管理员输出上限、内嵌媒体尺寸与总量、GPU 锁、停止未确认时禁止复用等既有边界复查通过。

## 证据与限制

忽略目录 `tmp/model-recheck/` 保存 GPU 诊断脚本、生成 PNG、真实推理摘要和 SQLite / PostgreSQL JUnit。`tmp/ui-qa/visual-report.json` 与截图目录保存 25 张截图和交互结果。生产服务、实际用户节点、凭证和权重配置均未修改。

Windows Job/AppContainer 无法在当前 Ubuntu 本机运行，保留原生条件跳过并通过 GitHub Windows CI 执行协议 / Job 用例；实际 Windows Ollama / ComfyUI 权重、驱动与重连仍需目标机验收。视频仍为浏览器抽帧方案，本轮未用真实视频文件执行端到端问答。未将这些缺项计为实测通过。

官方依据：[NInfer 支持矩阵](https://github.com/Neroued/ninfer#supported-models-and-deployments)、[启动配置](https://github.com/Neroued/ninfer/blob/master/docs/startup-configuration.md)、[Serving API](https://github.com/Neroued/ninfer/blob/master/docs/serving.md)、[ComfyUI Qwen 节点](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_qwen.py)。
