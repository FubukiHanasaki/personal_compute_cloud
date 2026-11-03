# 验收矩阵

作者：Fubuki。记录日期：2026-11-03（Asia/Shanghai）。本次为本地开发交付，尚不宣布所有 MVP 验收场景完成。

## 实测环境与证据

Python 3.13.16、Node.js 22.16.0；临时 PostgreSQL 18.6（回环监听、专用非超级用户角色）。CI 另外配置 PostgreSQL 17 与 Windows/Linux Agent 测试矩阵，但未触发远程 CI，不能视为已运行证据。

SQLite 与 PostgreSQL 最终各 **40/40 测试通过，无 skip**；前端传输契约另有 **3/3 测试通过**。自动化覆盖真实 HTTP Server → 出站 Agent → Linux bubblewrap 子进程 → 工件上传/授权下载；请求、迁移、权限、并发幂等/领取、旧租约、取消、负载、人工核对、网络阻塞和完成确认丢失均有测试。新增历史筛选先于分页、跨用户隔离、模型/模板授权后分页、中文 SSE 分片/截断/错误及 CSRF 回归。

前端通过 TypeScript + Vite 生产构建；Python wheel/sdist 构建通过；Caddy 原生配置验证及 Compose 原生配置结构验证通过。Compose 校验使用 `.env.example` 提供占位参数，实际部署仍须按说明复制并填写 `.env`。Server 只接收显式应用配置，不接收数据库超级用户密码。当前环境没有 Docker Engine；Ubuntu 有 Chromium，但电脑控制工具未连接 Ubuntu 浏览器。用户在本次自检中明确要求先跳过视觉校验，因此没有执行浏览器自动化、截图或移动端实测。详见 [本轮自检记录](self-check-v0.4.md)。

真实 GPU 证据：[gpu-lifecycle.json](evidence/gpu-lifecycle.json)。Ollama 0.40.14、公开 smollm2:135m 测试模型；原生报告显存 381,828,463 bytes，非 synthetic。模型在非 root 的 systemd 用户 scope 中加载和推理，空闲后调用原生卸载并确认自有进程退出。测试只断言推理返回非空文本，不评价小模型回答正确性。运行时和模型位于被忽略的 tmp；模型各层按官方 registry 清单逐一校验 SHA256 和大小。

## A01–A14

| 编号 | 状态 | 已有证据 / 尚缺条件 |
|---|---|---|
| A01 部署 | 待实机验收 | Compose/Caddy 校验通过，生产迁移/卷/TLS 配置齐备；缺 Docker 主机与真实域名证书/重启持久性实测 |
| A02 跨平台 | 部分 | Linux 真实 Agent 链路通过，Windows 配置/安装脚本与 CI 已提供；缺 Windows 机器实测，Windows 隔离 Worker 尚未实现 |
| A03 无入站 | 部分 | Agent 无监听服务，实际出站 HTTP 完成任务；真实 NAT + HTTPS 网络联调待部署 |
| A04 普通用户 API | 本地通过 | 真实 HTTP + API Key 提交/查询/下载，无用户客户端依赖；HTTPS 随 A01 验收 |
| A05 权限 | 通过 | 跨用户任务/直接工件 ID 隔离、角色/作用域/资源授权、CSRF、密钥撤销测试 |
| A06 安全执行 | Linux 通过 | 非法模板/命令/路径输入拒绝；真实进程限制和隔离计算；Windows 执行故意关闭，未静默降级 |
| A07 调度 | 通过 | 多节点能力匹配、Shared 优先、Private 与 Reserved、本地优先、CPU/RAM 阈值和额度；不按设备名判断 |
| A08 租约恢复 | 通过 | 并发有效 attempt 唯一、过期待核对、明确停止后重试、非幂等默认失败、迟到结果拒绝、丢确认幂等恢复 |
| A09 模型生命周期 | 首适配器通过 | Ollama 真实 GPU 加载→推理→空闲卸载→进程退出，活动保护/慢启动不阻塞心跳测试；ComfyUI 仅协议测试，实际图片权重待验收 |
| A10 本地保护 | 本地通过 | Private/Reserved 启动前再次校验；Maintenance 和中止分开；只杀自有进程组/scope；无权限终止外部进程的接口 |
| A11 Portal | 视觉校验本轮跳过 | 用户明确要求先跳过；类型/构建与 API 闭环通过，图片历史分页、传输错误恢复与窄屏样式已修复；浏览器点击、截图与移动端实测仍待完成 |
| A12 存储 | 通过 | 本地适配器、所有者鉴权、SHA256、文件与总量限制、路径防护、保留期限及清理测试；DB 仅元数据 |
| A13 异常处理 | 通过 | 排队可解释、配额/输入拒绝、超时/失联核对、统一错误和请求 ID；不会因等待而并发重做 |
| A14 文档 | 文档交付 | 部署、安装、API、安全、备份恢复、依赖和排障齐备；实际备份恢复演练仍需 Docker 部署环境 |

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
