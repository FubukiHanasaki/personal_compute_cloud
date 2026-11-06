# 原生通用模型接口复查 · 2026-11-06

作者：FubukiHanasaki。

本次直接完善主分支 PCC 原生 HTTP 接口，不合并实验分支中的独立 Text2Visual 网关、分镜提示词、storyboard Schema 或额外业务依赖。通用调用方按请求选择模型，文本和图片独立提交；原资源调度、模型授权、按需加载、管理员默认参数与 Agent 执行协议继续生效。

## 实现

- `server/pcc_server/ai.py`：增加 `/v1/models` 别名与 kind 筛选，保留 `/api/v1/models`；支持 Chat 顶层受限采样参数和 max_completion_tokens 别名，冲突返回 422；消息和文本结果不增加业务内容。模型身份出现歧义时拒绝请求。
- 新增 `/v1/images/generations` 同步 URL / Base64 输出，保留 `/api/v1/images/generations` 异步 Job；两条路径共用提交、幂等和校验。支持成对 width / height；保留 seed、负向提示词与参考图传递。
- 同步接口响应携带 X-PCC-Job-ID。等待超时不取消任务，复用 Idempotency-Key 可继续核对相同执行。同步图片需要 jobs:read 和 jobs:write，下载继续要求 PCC 身份；不生成公开 URL 或共享本机路径。
- `server/pcc_server/jobs.py`：抽取工件读取校验，下载与同步图片共用归属、当前执行、过期和 SHA256 校验。同步接口还验证 PNG 签名，并拒绝缺失、过期、损坏或超限工件。
- `server/pcc_server/app.py`：保留 HTTPException 的响应头，确保失败与超时也能返回任务 ID。
- `server/pcc_server/config.py`：新增 PCC_IMAGE_WAIT_SECONDS，默认 300 秒；文本仍默认 120 秒。两者范围均为 1–3600 秒。
- API 文档与 README 说明调用格式、范围和原独立网关的迁移方式，无数据库迁移。

## 验证

| 验证 | 结果 |
| --- | --- |
| 新增原生 API 测试 | 30 项通过；模拟节点完成真实 HTTP 提交、领取、上传、完成、下载流程，不加载模型权重 |
| SQLite 完整回归 | 287 通过、15 跳过；日志 `tmp/native-api-final-sqlite.xml` |
| PostgreSQL 完整回归 | 289 通过、13 跳过；含 HTTPS、备份恢复；日志 `tmp/native-api-final-postgres.xml` |
| 后台线程异常与不可回收异常 | 作为测试错误处理；最终无此类错误 |
| Ruff / Git diff 空白检查 | 通过 |
| wheel / sdist | 构建通过；检查原生新路由已打包，不含 text2visual / storyboard 文件或 jsonschema 业务依赖 |

测试覆盖模型列表筛选、无查询副作用、鉴权与作用域、模型身份歧义、原始多模态消息、SSE 与普通文本、采样别名/冲突/类型/管理员上限、图片同步两种格式、旧异步默认值、width/height、seed/负向提示词、幂等与变更冲突、超时任务保留、执行失败、工件缺失/过期/损坏/错误格式。

Windows 原生隔离与模型进程测试的 13 个用例需要 Windows，本机 Ubuntu 不宣称完成这些测试。SQLite 另跳过 PostgreSQL 恢复和 HTTPS 用例，已在配备 PostgreSQL/Caddy 的另一轮通过。现有 Starlette TestClient 有一条依赖弃用警告。未调用真实 27B 或其他模型权重，未访问或重新部署生产服务器。

## 部署边界

只需更新主分支 pcc-server 并重启原服务。用户自行部署，保留现有数据库、模型参数、授权、Windows Agent 配置、Caddy 与证书。调用端使用原 PCC 地址和 PCC API Key，原生调用确认成功后可停止旧 pcc-text2visual 进程。Text2Visual 分镜业务仍由外部调用方负责，原生接口不声称直接支持其 storyboards:plan 业务契约；当前 SSE 仍在推理完成后分块，图片 n 仅支持 1。
