# 模型、会话与多模态

作者：FubukiHanasaki

## 工作空间

总览提供模型对话、图像创作、计算任务入口。计算节点默认使用紧凑小卡片，以 CPU、内存、显存圆环展示当前占用率和分配额度。点击后平滑放大为详情层，展示物理总量、设备可用量、已启用能力及管理操作；关闭时反向收起。支持 Escape、遮罩关闭、焦点恢复和减少动态效果偏好。卡片中的使用率来自节点心跳，不是任务完成百分比；离线以节点状态和心跳时间为准。

主题采用薄荷绿、雾蓝和少量暖沙色。七个基础颜色仍集中在 `agent/pcc_agent/gui_assets/theme.css`，网页主题面板支持覆盖和重置。字体使用本机中文无衬线字体，不加载外部字体。

## 管理模型

Owner 在“模型管理”登记模型，Owner / 管理员可维护已有模型的名称、资源额度、超时、空闲卸载和推理默认值。访问范围和用户授权仍由 Owner 管理。运行文件、模型文件与 ComfyUI 工作流只能由节点本地管理员批准，云端表单不接受执行命令、路径或任意工作流。

模型仍有排队、运行、取消确认或待核对任务时，参数更新返回 409；先处理这些任务再保存。成功保存后参数同步到服务定义，Agent 空闲时停止旧进程，下次加载使用新参数。不会更换正在执行任务的模型配置。

对话模型可维护上下文 Token 数、输出 Token 上限、KV 缓存类型、温度、Top P、Top K、种子、思考与视觉能力。Ollama 另有 Flash Attention、重复惩罚；NInfer 另有 KV/State 池大小和前缀复用。用户的采样参数默认使用管理员配置，可在输出额度内调整；上下文、KV、视觉权限不能由用户扩大。

| 模型                    | 适配器  | 本地 ID / 注意事项                                      |
| ----------------------- | ------- | ------------------------------------------------------- |
| Qwen3.5 / 3.6 / 3.8 27B | Ollama  | `qwen3.5:27b`、`qwen3.6:27b`、`qwen3.8:27b`；预设可编辑 |
| Qwen3.6 / 3.8 27B       | NInfer  | `qwen3.6-27b`、`qwen3.8-27b` 映射到本地 `.ninfer` 文件  |
| Qwen3-VL-8B-Instruct    | Ollama  | `qwen3-vl:8b-instruct`，启用视觉                        |
| Qwen Image 2.1          | ComfyUI | `qwen-image-2.1` 映射到已批准的 API 工作流              |

预设不会下载权重、运行安装程序或扩大节点本地资源限制。显存预留是待管理员按量化方式、上下文和硬件调整的配置，不是“此显存一定可运行”的保证。

## NInfer 节点配置

仅接入管理员安装的 `ninfer-serve`。在 Agent GUI 的“NInfer”中填写可执行文件、GPU UUID、显存预算，以及逐行的 `模型ID=完整路径`。也可在 `agent.toml` 配置：

```toml
ninfer_executable = 'C:\NInfer\ninfer-serve.exe'
ninfer_port = 8089
ninfer_gpu_device = 'GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
ninfer_vram_mb = 24576

[ninfer_models]
'qwen3.8-27b' = 'C:\Models\qwen3_8_27b.ninfer'
```

替换示例 GPU UUID。NInfer 必须具备当前平台的进程 CPU/内存隔离能力；使用专属回环监听，不接管用户已运行的其他服务，固定单请求。上下文和 KV 通过启动参数传入；显存预算通过 `NINFER_VRAM_BUDGET_MIB` 传入。本文对接官方当前的 `--context-capacity` 字节池接口，旧版仅有 `--kv-capacity` Token 池接口不兼容，需更新 NInfer；不要盲目互换两种单位。当前选择 `bf16`、`fp8`、`int8`，不宣称其他存储格式已通过适配验证。

## Qwen Image 2.1

使用支持 `TextEncodeQwenImage21` / `QwenImage21Cache` 的 ComfyUI，并在其模型目录安装官方兼容的 diffusion model、Qwen3-VL text encoder 与 VAE。示例图是原生节点 API 格式，不使用 UI 子图或自定义插件：`deploy/workflows/qwen-image-2.1-api.example.json`。

```toml
[comfyui_workflows.'qwen-image-2.1']
file = 'deploy/workflows/qwen-image-2.1-api.example.json'
prompt_node = 'text'
latent_node = 'latent'
sampler_node = 'sampler'
image_nodes = ['ref1', 'ref2', 'ref3', 'ref4']
min_vram_mb = 24576
```

示例使用 INT8 模型文件名，可在本地批准的 JSON 中修改为已安装的匹配权重。默认 1024 方图、25 步、CFG 1、Euler / simple；这些是可维护的起点，不保证所有量化模型的最佳效果。提示词不会被静默改写。图像尺寸支持 256、512、768、1024 方图及 1536×1024、1024×1536。参考图最多四张，尺寸由选择的画幅决定；编辑时建议保持第一张参考图的宽高比。每次请求后清理暂存图片和输出。需要输出 RGBA 时可在提示词说明，工件仍以 PNG 保存。

## 对话保存与附件

网页对话保存在服务器数据库，以用户 ID 隔离，包含标题、模型、用户消息和关联推理任务。回复直接读取该任务持久化的结果，刷新、切页或重新登录后均可恢复；数据库备份自然包含会话。可新建、重命名、删除会话。删除会话不会删除关联任务，任务仍按原策略保留；界面会明确确认这一点。

Enter 发送，Shift + Enter 换行；中文输入法组词期间的 Enter 不发送。每个会话同时只允许一个未结束回复，重试标识防止重复提交。单会话最多 31 轮，用户最多 500 个会话，仍受原有活动和每日任务配额控制。

启用视觉的模型支持 PNG、JPEG、WebP。浏览器把图片缩放至最长边 1280 后上传；单张内嵌图片最大 2 MB，服务端校验格式与像素上限。上下文最多八张图片或视频采样帧，总输入最大 12 MB。不会按附件内容读取任何远程 URL 或本地文件。

视频支持是**浏览器抽取四个带时间信息的画面帧**，不是原生连续视频、音频或视频生成：附件最大 32 MB、时长最多十分钟。图像创作可上传最多四张参考图片，由节点批准的工作流进行编辑。不支持的能力会拒绝执行，不回退为虚假的生成结果。

## 接口来源与验证边界

实现依据 [Ollama Chat API](https://docs.ollama.com/api/chat)、[Ollama 上下文配置](https://docs.ollama.com/context-length)、[NInfer Serving](https://github.com/Neroued/ninfer/blob/master/docs/serving.md)、[NInfer Startup Configuration](https://github.com/Neroued/ninfer/blob/master/docs/startup-configuration.md)、[Qwen Image 2.1](https://github.com/QwenLM/Qwen-Image-2.1)、[ComfyUI 官方工作流](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json)、[Qwen3-VL 模型卡](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)。

Ubuntu 可实际检查网页、数据库迁移和适配器请求契约。大型权重、目标 GPU 和 Windows 原生模型进程若当前不可用，只报告逻辑与协议验证，不将合成回复或 HTTP 模拟等同于真实模型验收。目标节点仍需在接入生产前通过本机检查和实际推理。
