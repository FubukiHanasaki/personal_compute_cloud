# 依赖与升级策略

作者：Fubuki。精确 Python 版本和下载哈希在 uv.lock，前端版本及完整依赖树在 web/package-lock.json。开发临时运行时和工具不打入 Git，不作为平台依赖。

| 组件 | 固定范围 / 本次锁定 | 许可 |
|---|---|---|
| Python | 3.12–3.14；本机使用 3.13.16 | PSF |
| FastAPI / Starlette | uv.lock，0.162.0 / 1.10.0 | MIT / BSD-3-Clause |
| SQLAlchemy / Alembic | 2.0.x / 1.x，锁定 2.0.55 / 1.20.1 | MIT |
| psycopg | 3.x，锁定 3.3.7 | LGPL-3.0（独立库及 binary 分发保留许可） |
| Pydantic / pydantic-settings | 2.x | MIT |
| httpx / uvicorn | 0.x，锁定 0.28.1 / 0.54.0 | BSD-3-Clause |
| psutil | 6–7.x，锁定 7.2.2 | BSD-3-Clause |
| React / React DOM | 19.x | MIT |
| TypeScript / Vite | 5.7.x / 6.x | Apache-2.0 / MIT |
| PostgreSQL | 生产镜像 17-alpine | PostgreSQL License |
| Caddy | 2-alpine | Apache-2.0 |
| bubblewrap | 系统提供，不随 wheel 分发 | LGPL-2.0-or-later |
| systemd | Linux 用户服务/资源限制，系统提供 | LGPL-2.1-or-later / GPL-2.0-or-later（按组件） |
| ComfyUI（可选） | 本次实测 0.43.0，commit ab08f7b2caa6d4007ecde3762116c6193613f43b，Torch 2.11.0+cu128 | GPL-3.0 / Torch BSD-3-Clause；权重各自许可 |
| Ollama（可选） | 节点管理员安装，PCC 不内置权重 | MIT；模型权重各自许可 |

月度检查安全更新，先更新锁文件，再运行 SQLite/PostgreSQL 的权限、状态机、故障注入、迁移升降级和端到端测试以及前端构建。FastAPI/Starlette 组合和 httpx 测试客户端有弃用提示，升级需核对兼容层；当前测试通过，不能直接无测试跨主版本升级。

镜像主版本已限制，实际生产应在验收后固定镜像 digest，并记录操作系统、内核、驱动、后端及模型版本。模型许可由节点管理员确认；不得以 PCC 的 MIT 许可替代权重许可。

Ollama 适配器协议依据[官方 API 文档](https://docs.ollama.com/api)以及[原生卸载说明](https://github.com/ollama/ollama/blob/main/docs/api.md)。

图片实测使用 [SD1.5 safetensors](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5)，CreativeML OpenRAIL-M，权重 SHA256 和运行证据见 docs/evidence/image-lifecycle.json。代码仓库不分发模型或 ComfyUI/PyTorch；本地依赖版本清单位于 tmp/comfy-validation/runtime-lock.txt。当前 ComfyUI 启动选项按上述版本实测，升级或更换版本后应先复验实际生成、资源预算和卸载，不能静默移除不支持的隔离参数。
