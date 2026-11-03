# V0.4 后续实现与核验

作者：Fubuki。日期：2026-11-03（Asia/Shanghai）。

后续 Windows/Server 逻辑审查及修复见 [逻辑检查记录](logic-review-v0.4.md)。该轮按要求未做运行时测试；本文通过数为此前提交的历史结果，不代表后续修改已经实测通过。

随后已实现 Windows 受管 Ollama/ComfyUI，配置及独立验证范围见 [Windows 模型支持](windows-models.md)。本文件保留当时阶段的实测结果。

## 本轮交付

| 范围 | 实现与检查 |
|---|---|
| Windows 受控执行 | 独立官方 Python 运行时及 SHA256 校验；AppContainer 零网络 capability；按 SID 授权的运行时/任务目录；创建进程时原子加入 Job Object；CPU、内存、进程数、墙钟和输出限制；kill-on-close；异常退出后的 OS profile 清理。默认关闭，探测失败拒绝执行 |
| Windows 凭证保护 | 凭证写入前收紧 DACL，不依赖显示用户名或其代码页；Agent 工作目录仅授权当前账号；运行时不得包含 Agent 代码、凭证或非 vendor 文件 |
| HTTPS | Agent 可显式配置信任私有 CA，默认标准 CA 信任库，不提供关闭验证选项；实际 Caddy TLS、Secure/HttpOnly 会话、短作用域 Key、统计任务/工件与 Server 重启持久性通过 |
| 备份恢复 | 有版本清单、可搬移校验和、未完成标记、可信工件归档、全新目标检查、单事务数据库恢复；生产 Compose 包装脚本保持停写窗口。实际 PostgreSQL 清空恢复后，原用户密码/Key/节点凭证、任务和文件校验通过 |
| 真实图片模型 | ComfyUI 0.43.0 / Torch 2.11.0+cu128 / SD1.5；批准工作流仅绑定 prompt/size。独立 cgroup、离线模式、自定义节点关闭、独立可写目录；Torch 显存池按预算扣除 context 余量，关闭绕过该限制的 DynamicVRAM；生成等待有时限 |
| 实际图片全链路 | 普通用户短作用域 Key 经验证 HTTPS 提交 512×512、20 步任务，真实 Agent 领取/续租、GPU 生成、上传与授权下载，SHA256 一致；空闲卸载、自有进程退出，GPU 回到 13 MiB 基线。证据见 [image-lifecycle.json](evidence/image-lifecycle.json) |
| 重复启停 | 修复 TIME_WAIT 造成的端口误判，停止后关闭 HTTP 连接；允许重用旧连接状态，仍拒绝活动监听器，不接管其他服务 |
| 发布检查 | 前端 3 项传输测试与生产构建；Python wheel/sdist 构建及内容检查。分发包包含新执行器与前端测试，不包含临时凭证、权重和运行时依赖 |
| CI | Windows 原生隔离用例和独立 Compose HTTPS→重启→备份→新卷恢复流水线已提供；本地没有远程 Git 仓库，不能把尚未触发的 CI 当作已通过 |

## 最终本机结果

- PostgreSQL 18.6：59 项通过，5 项 Windows 原生用例跳过。
- SQLite：58 项通过，6 项跳过；额外跳过的 PostgreSQL 恢复用例已在上述 PostgreSQL 运行通过。
- 前端传输：3 项通过；TypeScript/Vite、Python 包、Compose 配置校验通过。
- 实际模型生命周期：既有 Ollama GPU 证据保留，本轮新增 ComfyUI 256×256 和 512×512 真实 GPU / HTTPS 任务证据。
- Python 测试仍有一条上游 Starlette/httpx 弃用提示。

## 尚未满足的验收条件

实现补齐与本机通过不等同于 V0.4 全部验收通过，仍保留以下具体缺口：

1. 当前只有 Ubuntu 执行环境，5 项 AppContainer/Job 原生用例尚未在 Windows 运行，Windows 安装和断线重连也需实机核验。该记录对应阶段尚未实现 Windows 受管 Ollama/ComfyUI；后续实现和新增原生检查入口见上方 Windows 模型文档。
2. 当前没有 Docker Engine，sudo 探测返回需要交互认证；没有生产域名/DNS。实际容器部署、生产卷恢复、公网 ACME 与真实 NAT/防火墙验收尚未完成。新增 Compose CI 提供了可运行的隔离演练，但尚未触发。
3. 浏览器视觉校验沿用用户此前“先跳过”的安排，未重新启用；没有 Portal 点击、桌面/移动端截图通过结论。

因此继续标记开发版，不发布“全部 A01–A14 通过”的结论。完整状态见 [验收矩阵](acceptance.md)。

## 复验入口

```bash
# 已准备好依赖后：
PCC_CADDY_BIN=/absolute/path/to/caddy tmp/venv/bin/pytest -q
# 在专用 PostgreSQL 测试库复验；测试会重建表：
PCC_TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1/pcc_test \
PCC_PG_BIN=/absolute/postgresql/bin PCC_CADDY_BIN=/absolute/path/to/caddy \
  tmp/venv/bin/pytest -q
npm run test:api --prefix web
npm run build --prefix web
# 管理员准备真实模型和 GPU 配置后：
tmp/venv/bin/python scripts/verify_image.py --config agent.toml --model APPROVED_MODEL
```

Windows 的安装、运行和原生测试见 [Agent 文档](agent.md)；备份/恢复见 [部署文档](deployment.md)。本次临时模型、库、测试数据库、证书、凭证、图片和日志均在被 Git 忽略的 tmp；真实模型服务、QA Server/HTTPS 代理在验证后关闭。
