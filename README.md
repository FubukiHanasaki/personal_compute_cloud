# Personal Compute Cloud

作者：**Fubuki**。自托管个人计算云：FastAPI / PostgreSQL 控制面、仅出站 HTTPS 的 Python Agent、中文 React 管理台与 Portal、Caddy + Docker Compose。

已实现节点注册/监控/策略、任务租约和失败核对、Linux 隔离统计模板、工件、用户/API Key/会话/CSRF、服务生命周期、Chat/SSE、异步图片协议及管理界面。ComfyUI 图片适配器已提供；实际图片权重、Windows 执行隔离和完整生产部署验收仍待完成；**此版本不是 A01–A14 全部验收通过的 MVP 发布**。GPU 实测状态见[验收矩阵](docs/acceptance.md)。

## 本地启动

需要 Python 3.12+、uv、Node.js 22 / npm；所有临时依赖、缓存、开发数据、构建产物统一存入被 Git 忽略的 `tmp/`。

```bash
python3 scripts/dev.py setup
python3 scripts/dev.py init-dev
# 打开 .env 查看随机生成的 Fubuki 初始密码，不提交该文件。
python3 scripts/dev.py migrate
python3 scripts/dev.py build
python3 scripts/dev.py serve
```

浏览器打开 [本地 Portal](http://127.0.0.1:8000)，使用 `Fubuki` 登录。开发 HTTP 仅限回环。Owner 在“访问与授权”批准 `compute.stats` 模板，在“计算节点”签发一次性注册令牌。安装并接入 Agent 后，提交数字统计任务即可得到真实执行结果。

```bash
# Linux 节点，使用专用普通用户；Windows 流程见 docs/agent.md
sh deploy/install-agent.sh
# 编辑 agent.toml：服务器地址、名称、分配额度；启用 compute 需可用 bubblewrap。
tmp/agent-venv/bin/pcc-agent register --config agent.toml
tmp/agent-venv/bin/pcc-agent run --config agent.toml
```

节点默认 Maintenance。必须在本地 `agent.toml` 和云端节点策略中均明确设为 Shared 才接收共享任务。仅安装 Agent 即可监控接入；无合格隔离层时拒绝计算，不回退宿主机执行。

## 验证

```bash
python3 scripts/dev.py test
python3 scripts/dev.py build
# 可选：专用测试 PostgreSQL，测试会重建该库的 PCC 表，严禁指向生产。
PCC_TEST_DATABASE_URL=postgresql+psycopg://pcc:password@localhost/pcc_test python3 scripts/dev.py test
```

CI 包含 PostgreSQL、迁移升降级、权限、并发领取、失败注入、真实 HTTP/Agent/隔离 Worker 链路与前端构建。缺少 bubblewrap 的主机明确跳过隔离执行测试。

## 文档

- [Linux Compose / HTTPS 部署、配置、备份恢复](docs/deployment.md)
- [Windows / Linux Agent 安装、隔离与模型配置](docs/agent.md)
- [API 使用与兼容范围](docs/api.md)
- [安全边界与故障排查](docs/security.md)
- [验收证据与尚未完成项](docs/acceptance.md)
- [V0.4 对照自检与修复记录](docs/self-check-v0.4.md)
- [阶段实施记录](docs/implementation.md) · [需求基线 V0.4](docs/spec-v0.4.md)
- [依赖版本、许可与升级策略](docs/dependencies.md)

MIT License · Copyright © 2026 Fubuki
