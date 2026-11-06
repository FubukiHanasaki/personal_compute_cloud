# API 使用

作者：FubukiHanasaki。生产统一 HTTPS；浏览器使用 HttpOnly 会话 + CSRF，程序使用作用域 Bearer API Key。节点凭证与用户密钥独立，禁止互换。

## 提交并下载结果

Owner 首先批准模板（或在 WebUI 访问与授权中登记）：

```bash
curl "$PCC_URL/api/v1/admin/templates" -H "Authorization: Bearer $PCC_ADMIN_KEY" \
  -H 'Content-Type: application/json' -d '{"id":"stats","name":"数字统计","kind":"compute.stats","worker":"compute.stats","public":true,"retry_safe":true}'
```

普通用户只需 HTTPS，不安装 Agent：

```bash
curl "$PCC_URL/api/v1/jobs" -H "Authorization: Bearer $PCC_API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: example-001' \
  -d '{"task_type":"compute.stats","template_id":"stats","input":{"values":[1,2,3]},"timeout_seconds":120}'
# 返回 202，记录 job_id；排队不会因浏览器断开而丢失。
curl "$PCC_URL/api/v1/jobs/$PCC_JOB_ID" -H "Authorization: Bearer $PCC_API_KEY"
curl "$PCC_URL/api/v1/jobs/$PCC_JOB_ID/events?limit=50&offset=0" -H "Authorization: Bearer $PCC_API_KEY"
curl "$PCC_URL/api/v1/jobs/$PCC_JOB_ID/artifacts" -H "Authorization: Bearer $PCC_API_KEY"
# 对返回的 download_url 使用同一身份下载，再核验 sha256。
```

同一用户的幂等键去重；相同键配不同内容返回 409。输入只接受白名单参数，未知字段和命令/源码/路径均不允许。无兼容节点时保持 queued；负载过高、额度不足和私人策略都会阻止领取，任务面板展示实际状态。

列表支持 limit（默认 50，最大 100）与 offset；`/api/v1/models` 使用 data，其他列表使用 items。任务列表可用 task_type（compute.stats / ai.chat / ai.image）先筛选再分页；图片历史独立于计算任务页。前端模型、模板和管理资源选择器会读取后续页。错误格式：`{"error":{"code":"...","message":"...","request_id":"..."}}`，响应头包含 `X-Request-ID`。5xx 不返回堆栈。

## 身份

`POST /api/v1/auth/login` 返回会话 Cookie 与 csrf_token；后续 Cookie 身份的 POST/PUT 操作必须传 `X-CSRF-Token`。`GET /api/v1/auth/me` 恢复当前用户与 CSRF，浏览器不使用 localStorage 保存长期密钥。

`POST /api/v1/keys` 输入 `scopes`、`expires_seconds`（60 秒–90 天），只返回一次明文。允许作用域：jobs:read、jobs:write、models:read、nodes:read、admin。作用域不提升用户角色。`GET /api/v1/keys` 只返回自身元数据；`POST /api/v1/keys/{id}/revoke` 撤销凭证。以上密钥管理操作要求网页登录会话（写入仍需 CSRF）或 Owner/Admin 的 admin Key；只有任务、模型或节点读写作用域的程序 Key 无权列出、创建或撤销账号密钥。

禁用账号会撤销其全部现有 API Key、网页登录会话及尚未使用的节点注册令牌。重新启用账号不会恢复这些旧凭证，需要重新登录或签发；注册时还会独立检查令牌签发者当前是否仍具备管理员权限。

Owner 通过 `/api/v1/admin/users` 创建 User/Admin，通过 PUT 更新 status 和明确的资源 grants（nodes/view_nodes/templates/jobs）；Admin 只能操作授予的资源。Owner 本机遗失浏览器会话时可用 `pcc-server bootstrap-key` 在密码校验后生成一小时管理密钥，不能绕过密码。

## Chat 与图片

`GET /v1/models` 与 `GET /api/v1/models` 返回相同的已授权模型列表（`object: "list"`、`data`），支持 `kind=text|image`、limit、offset；每项包括 id、kind、task_type、管理员默认参数、synthetic 与 online。该查询不加载模型；online 只表示在线节点声明对应 worker，不保证任务立即执行。每次请求通过 `model` 选择模型，文本和图片可独立调用。模型 ID 在同一任务类型的已授权模板中必须唯一，否则调用返回 409，请管理员消除歧义。

```bash
curl "$PCC_URL/v1/chat/completions" -H "Authorization: Bearer $PCC_API_KEY" \
  -H 'Content-Type: application/json' -N \
  -d '{"model":"YOUR_REGISTERED_MODEL","messages":[{"role":"user","content":"解释二分查找"}],"stream":true}'
```

支持 model、messages、stream、parameters；messages 支持 system/user/assistant + 字符串 content，已启用视觉的模型可在 user content 中传 text / image_url 数组，图片仅支持内嵌 PNG/JPEG/WebP data URI。parameters 可传 temperature、top_p、top_k、max_tokens、seed、thinking；不传时使用管理员默认值。max_tokens 不得超过管理员上限，上下文、KV、视觉设置由管理员维护。也可直接在顶层传 temperature、top_p、top_k、max_tokens（或同义 max_completion_tokens）、seed、thinking；与 parameters 中的同名值冲突返回 422。tools、response_format 等未实现的字段明确返回 422。SSE 事件为 `data: {...}`，以 `[DONE]` 结束；等待阶段仍执行持久化任务。首版完成推理后分块 SSE，不声称原生 token streaming。

`POST /api/v1/images/generations` 输入 model、prompt、可选 size（256 / 512 / 768 / 1024 方图、1536x1024、1024x1536）及可选 images（最多四个内嵌图片 data URI），size 默认使用管理员配置，返回异步 Job，按任务 API 查询/下载。可选 comfyui:<model> 连接本地批准的真实图片工作流；synthetic.image 只验证协议。历史 Linux 真实权重验证与当前 Windows 待验收范围见[验收矩阵](acceptance.md)及[模型复查记录](windows-models-review.md)。

### 原生同步图片接口

`POST /v1/images/generations` 等待任务完成，返回图片 URL 或 Base64；无需独立兼容层进程、额外兼容层令牌或业务 Schema。使用 PCC API Key（`jobs:write` + `jobs:read`，列模型还需 `models:read`）及现有模型授权。

```bash
curl "$PCC_URL/v1/images/generations" -H "Authorization: Bearer $PCC_API_KEY" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: image-example-001' \
  -d '{"model":"YOUR_REGISTERED_IMAGE_MODEL","prompt":"A white rabbit on grass","negative_prompt":"watermark","seed":12345,"size":"1024x1024","n":1,"response_format":"b64_json"}'
```

返回 `{"created": Unix时间秒, "job_id": "...", "data": [{"b64_json": "...", "artifact_id": "...", "sha256": "...", "expires_at": Unix时间秒, "seed": 12345, "negative_prompt": "watermark"}]}`。`response_format` 默认为 `url`，该模式将 b64_json 换为 url；它是需携带同一 PCC 身份授权下载的工件地址，不是公开分享链接或本机文件路径。跨主机调用方可直接使用 b64_json。SHA256 为原始 PNG 字节的摘要。seed / negative_prompt 取实际执行结果；节点未报告则为 null，不伪造确认值。

目前 `n` 只接受 1。支持 size 或成对 width、height，两者同时提供须一致，支持画幅仍受原图片白名单约束；不传使用管理员默认画幅。seed 为 0–2^53−1 的整数，省略或 null 使用服务端/管理员默认值。negative_prompt 省略或 null 保留已批准工作流默认，空串清空绑定，非空文本通过节点已批准的负向提示绑定执行。images 的参考图约束和原异步接口相同；不接受远程 URL，不修改节点工作流权限。

同步文本/图片响应的 `X-PCC-Job-ID` 可用于查询原生任务状态。默认等待文本 120 秒、图片 300 秒，可分别用 `PCC_CHAT_WAIT_SECONDS` / `PCC_IMAGE_WAIT_SECONDS` 配置（1–3600 秒）。等待超时返回 504 并保留任务 ID，**不会取消已经提交的任务**；执行失败/取消/待核对返回 503。SSE 已发响应头后的失败通过错误事件返回。调用端超时也不表示任务停止。重试提交时复用相同 `Idempotency-Key` 与执行参数，避免重复生成；可先通过 `/api/v1/jobs/{job_id}` 查询和下载已有结果。

原 `POST /api/v1/images/generations` 仍返回 202 + Job，不等待推理，也支持成对 width / height。同步接口与异步接口共用提交、授权、参数、资源、隔离、排队、工件校验与幂等逻辑；没有单独的推理服务或模型配置副本。

### 通用接口边界与旧兼容层迁移

PCC 原样传递调用方提供的 messages / prompt；不注入分镜提示词，不将输出解析成 storyboard，不校验业务 JSON Schema。通用文本返回 assistant content，通用图片返回上述 data。Text2Visual 的 `storyboards:plan`、业务标识与 storyboard 契约应由调用方自己的业务适配器实现；原生接口不宣称直接满足这一业务协议，也不宣称支持完整 OpenAI API 功能。

现有部署可仅更新主分支 `pcc-server`，保留数据库、模型参数、用户授权和 Agent 配置。调用端改用原 PCC 地址与 PCC API Key；不再使用 8010 端口或 PCC_ADAPTER_TOKEN。验证原生调用成功后，再停止原先独立的 pcc-text2visual 进程；不要误停 pcc-server。无需为本次接口更新修改 Caddy、证书或安装额外业务依赖。

## Agent 协议与人工核对

注册令牌通过 `/api/v1/admin/registration-tokens` 签发，十分钟默认、最长一小时；在 `/internal/v1/nodes/register` 消耗一次换取独立节点凭证。

心跳报告 physical / allocatable / available 与本地策略，领取包含 claim_id（断线重试复用）与最长 20 秒的 wait_seconds。任务相关 renew/complete/fail/events/artifacts 必须附 attempt_id 与 lease_token。上传使用有界 Base64、合法文件名和 SHA256；没有长期对象存储管理员凭证。节点只能操作自己的有效 attempt。

重复 claim_id 返回原 attempt；原租约已过期时也可取回其身份用于停止核对，但不能续租、上传或完成，Agent 必须先续租成功才能启动 Worker。已确认停止或终结的 claim 返回 204，客户端可清理 pending claim，再用新的领取 ID 轮询。取消意图在租约过期后保留，确认停止时进入 cancelled，不因 retryable 标志重新排队。

租约过期进入 reconciling，不能直接重领。Owner 在检查原节点进程后可调用：

```json
POST /api/v1/admin/jobs/{id}/reconcile
{"confirmed_stopped":true,"retry":false,"evidence":"原节点管理员已核实原进程和全部子进程终止"}
```

retry=true 仅允许声明 retry_safe 的模板；操作留审计。取消与节点 Maintenance 为不同概念；节点策略接口停止新任务，`POST /api/v1/nodes/{id}/abort` 请求中止已执行 PCC 任务。完整字段与响应以服务器 `/docs` 和 `/openapi.json` 为准。

## 持久对话与模型维护

`GET/POST /api/v1/conversations` 列出 / 创建当前用户对话；`GET/PATCH/DELETE /api/v1/conversations/{id}` 查询、重命名、删除。`POST /api/v1/conversations/{id}/messages` 接收 content、唯一 request_id、可选 parameters，返回异步 job_id；读取对话可获得每轮状态及持久化回复。同一对话只允许一个活动任务，重试使用同一 request_id。

`PUT /api/v1/admin/templates/{id}` 允许 Owner / 管理员维护 name、parameters、image_parameters、resources、max_timeout_seconds、idle_seconds。模型有未结束任务时返回 409；不允许通过此接口更改 worker、模型身份、执行文件或授权。详见[模型与会话](models-and-conversations.md)。
