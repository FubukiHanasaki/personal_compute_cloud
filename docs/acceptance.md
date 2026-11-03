# 验收矩阵

作者：Fubuki。记录日期：2026-11-03（Asia/Shanghai）。本次为本地开发交付，尚不宣布所有 MVP 验收场景完成。

## 实测环境与证据

Python 3.13.16、Node.js 22.16.0；临时 PostgreSQL 18.6（回环监听、专用非超级用户角色）。CI 另外配置 PostgreSQL 17 与 Windows/Linux Agent 测试矩阵，但未触发远程 CI，不能视为已运行证据。

最终 PostgreSQL **59 项通过、5 项 Windows 原生测试跳过**；SQLite **58 项通过、6 项跳过**（额外跳过需 PostgreSQL 的真实恢复演练，该用例已在 PostgreSQL 运行通过）。前端传输契约 **3/3 通过**。跳过不计通过。自动化覆盖实际 Caddy HTTPS → Server → 出站 Agent → Linux bubblewrap 子进程 → 工件上传/授权下载、Server 重启持久性、迁移/权限/租约/并发/失败注入，以及真实 PostgreSQL 备份恢复。新增 Windows ABI/运行时边界/SID/调度契约、图片历史分页、SSE 截断、GPU 预算和重复启停回归。

前端生产构建、Python wheel/sdist 构建、Caddy 原生配置及 Compose 配置结构校验通过。已核对分发包包含新适配器和前端测试，未包含 tmp 凭证、模型或依赖。实际 HTTPS 测试使用私有测试 CA 并保持证书校验开启，未关闭验证、安装系统信任或替代公网 ACME 验收。Server 不接收数据库超级用户密码。当前环境无 Docker Engine，sudo 需要交互认证；没有远程 Git 仓库，新增 Windows/Compose CI 尚未触发。浏览器视觉校验沿用用户先跳过的安排，未执行。详见 [本轮补齐记录](completion-v0.4.md)与[前轮自检快照](self-check-v0.4.md)。

真实 GPU 证据：[gpu-lifecycle.json](evidence/gpu-lifecycle.json)。Ollama 0.40.14、公开 smollm2:135m 测试模型；原生报告显存 381,828,463 bytes，非 synthetic。模型在非 root 的 systemd 用户 scope 中加载和推理，空闲后调用原生卸载并确认自有进程退出。测试只断言推理返回非空文本，不评价小模型回答正确性。运行时和模型位于被忽略的 tmp；模型各层按官方 registry 清单逐一校验 SHA256 和大小。

真实图像 GPU 证据：[image-lifecycle.json](evidence/image-lifecycle.json)。ComfyUI 0.43.0、Torch 2.11.0+cu128、SHA256 校验后的 SD1.5 权重，非 synthetic。已完成 256×256 直接生命周期检查及 512×512、20 步的普通用户 HTTPS API → 出站 Agent → GPU 模型 → 授权 PNG 下载及 SHA256 核验；空闲原生卸载并确认自有进程退出，显存恢复 13 MiB 基线。最终配置禁用自定义/API 节点与绕过 Torch 池限额的 DynamicVRAM；限制 CPU/RAM、Torch 显存池与输出，保留本地 GPU 选择与资源预算。测试不代表任意模型或图像质量均已验收。

## A01–A14

| 编号 | 状态 | 已有证据 / 尚缺条件 |
|---|---|---|
| A01 部署 | 待生产实机验收 | 实际 Caddy HTTPS 和原生 Server 重启持久性通过，Compose 配置及部署/恢复 CI 已准备；缺 Docker 主机、公网域名证书和容器卷实测 |
| A02 跨平台 | 实现已补齐，Windows 待实测 | Linux 真实 Agent 通过；Windows AppContainer/Job Object、独立运行时、SID ACL、恢复清理及原生测试已提供，5 项原生测试在 Ubuntu 跳过 |
| A03 无入站 | 本地 HTTPS 通过 | 实际出站 HTTPS 完成统计和真实 GPU 图片任务，Agent 无监听服务；真实 NAT/防火墙联调仍待部署 |
| A04 普通用户 API | 本地 HTTPS 通过 | 普通用户短作用域 Key 经验证 TLS 提交/查询/下载统计与真实图片；公网部署随 A01 验收 |
| A05 权限 | 通过 | 跨用户任务/直接工件 ID 隔离、角色/作用域/资源授权、CSRF、密钥撤销测试 |
| A06 安全执行 | Linux 通过，Windows 待实测 | Linux 非法输入拒绝与真实隔离通过；Windows 仅在独立运行时实际探测成功后报告能力，原生文件/网络/资源限制测试待 Windows 运行 |
| A07 调度 | 通过 | 多节点能力匹配、Shared 优先、Private 与 Reserved、本地优先、CPU/RAM 阈值和额度；不按设备名判断 |
| A08 租约恢复 | 通过 | 并发有效 attempt 唯一、过期待核对、明确停止后重试、非幂等默认失败、迟到结果拒绝、丢确认幂等恢复 |
| A09 模型生命周期 | 两个适配器本地通过 | Ollama 与 ComfyUI 均完成真实 GPU 加载/推理/卸载/进程退出；真实图片经普通用户 HTTPS API 完成全链路；活动保护、超时与重复启停回归通过 |
| A10 本地保护 | 本地通过 | Private/Reserved 启动前再次校验；Maintenance 和中止分开；只杀自有进程组/scope；无权限终止外部进程的接口 |
| A11 Portal | 视觉校验本轮跳过 | 用户明确要求先跳过；类型/构建与 API 闭环通过，图片历史分页、传输错误恢复与窄屏样式已修复；浏览器点击、截图与移动端实测仍待完成 |
| A12 存储 | 通过 | 本地适配器、所有者鉴权、SHA256、文件与总量限制、路径防护、保留期限及清理测试；DB 仅元数据 |
| A13 异常处理 | 通过 | 排队可解释、配额/输入拒绝、超时/失联核对、统一错误和请求 ID；不会因等待而并发重做 |
| A14 文档与恢复 | 本地恢复通过 | 文档、可执行备份/恢复工具、恶意归档拒绝和 PostgreSQL 清空恢复后登录/工件校验通过；实际 Compose 卷恢复 CI 已提供，尚未运行 |

## 本地复验

```bash
python3 scripts/dev.py test
python3 scripts/dev.py build
npm run test:api --prefix web
# 仅指向专用、可重建的测试库：
PCC_TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1/pcc_test python3 scripts/dev.py test
# 模型必须由本地管理员先行准备，配置真实 GPU UUID 和足够预算：
tmp/venv/bin/python scripts/verify_gpu.py --config agent.toml --model ADMIN_INSTALLED_MODEL
```

隔离探测失败会显式 skip 对应硬件测试；不可把 skip 算作实测通过。当前控制模板不支持任意代码，S3、自动本地活动检测、云 GPU 付费、复杂 DAG 均不在此版本交付范围。
