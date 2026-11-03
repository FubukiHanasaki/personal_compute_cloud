# API 使用

作者：Fubuki。生产统一 HTTPS；浏览器使用 HttpOnly 会话 + CSRF，程序使用作用域 Bearer API Key。节点凭证与用户密钥独立，禁止互换。

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

`GET /api/v1/models` 返回已授权模型、任务类型、synthetic 标记与在线能力。该查询不加载模型。

```bash
curl "$PCC_URL/v1/chat/completions" -H "Authorization: Bearer $PCC_API_KEY" \
  -H 'Content-Type: application/json' -N \
  -d '{"model":"YOUR_REGISTERED_MODEL","messages":[{"role":"user","content":"解释二分查找"}],"stream":true}'
```

支持字段仅 model、messages、stream；messages 支持 system/user/assistant + 字符串 content。不支持 tools、temperature、max_tokens、图像消息等字段，明确返回 422。SSE 事件为 `data: {...}`，以 `[DONE]` 结束；等待阶段仍执行持久化任务。首版完成推理后分块 SSE，不声称原生 token streaming。

`POST /api/v1/images/generations` 输入 model、prompt、size（256x256 / 512x512），返回异步 Job，按任务 API 查询/下载。可选 comfyui:<model> 连接本地批准的真实图片工作流，尚待实际权重验收；synthetic.image 只验证协议。

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
