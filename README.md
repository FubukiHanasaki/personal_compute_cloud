# Personal Compute Cloud

作者：**Fubuki**。自托管个人计算控制面，计算节点仅通过出站 HTTPS 接入。

需求基线：[V0.4](docs/spec-v0.4.md)；阶段计划、测试与限制：[实施记录](docs/implementation.md)。

技术栈：FastAPI / PostgreSQL / Python Agent / React + TypeScript / Caddy / Docker Compose。

开发环境统一把虚拟环境、缓存、日志、数据库、构建输出放入 `tmp/`（已忽略）。

```bash
mkdir -p tmp
UV_PROJECT_ENVIRONMENT=tmp/venv UV_CACHE_DIR=tmp/uv-cache uv sync --extra test --frozen
export PCC_ENVIRONMENT=development PCC_SECRET=$(openssl rand -hex 32) PCC_SECURE_COOKIES=false
export PYTHONDONTWRITEBYTECODE=1
tmp/venv/bin/alembic upgrade head
tmp/venv/bin/pcc-server
# 另一终端
npm ci --prefix web --cache tmp/npm-cache
npm run build --prefix web
tmp/venv/bin/pytest
```

生产必须使用 PostgreSQL、HTTPS、受限账户和独立强随机凭证。开发 SQLite 仅用于快速测试。
