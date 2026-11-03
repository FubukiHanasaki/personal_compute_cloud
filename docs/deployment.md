# 部署、备份与恢复

作者：Fubuki。

## 生产前提

Linux Docker Engine 与 Compose v2、域名 DNS 指向服务器、80/443 可达。Server 不需要 GPU。数据库和 Server 不发布宿主机端口；Caddy 为唯一外部入口。控制进程使用 UID 10001，节点使用非 root/非 Administrator 账号。

```bash
cp .env.example .env
chmod 600 .env
# 三次独立生成，分别填 PCC_SECRET、POSTGRES_PASSWORD、PCC_APP_DB_PASSWORD：
openssl rand -hex 32
# PCC_DATABASE_URL 中使用 PCC_APP_DB_PASSWORD；随机 hex 避免 URL 转义问题。
# 设置 PCC_DOMAIN、PCC_OWNER_USERNAME=Fubuki 和至少 16 字符的独立强密码。
docker compose up -d --build
docker compose logs --tail=100 server caddy
```

必须替换全部 REPLACE 占位符。首次自动迁移至 0005 并创建 Owner；已存在 Owner 时不会用环境变量覆盖密码。首次登录后可在界面修改密码，再从部署 `.env` 删除 `PCC_OWNER_PASSWORD`。保留 `PCC_SECRET`，更换它会令现有密钥和节点凭证失效，需要重新签发。

Server 容器仅注入显式 PCC 应用配置，不向它传递数据库超级用户密码。新数据库卷的初始化脚本使用 `pcc_admin` 初始化，再创建无超级用户/建库/建角色权限的 `pcc` 应用账号。已有卷不会重新执行初始化脚本，必须按实际角色迁移，不能依赖改环境变量更改数据库密码。不要使用 `docker compose down -v` 作为升级步骤。

Compose 中只在专用 edge 网络信任代理头。不要把 Server 直接发布到公网。DNS/TLS 参数未提供，仓库中的 Compose 和 HTTPS 需要在实际服务器完成 A01 验收，不能以配置文件存在视为部署已成功。

## 配置

配置前缀均为 `PCC_`，定义在 `server/pcc_server/config.py`；`.env` 只在本地读取，真实值不进入 Git。

| 参数 | 默认 / 含义 |
|---|---|
| ENVIRONMENT | production；仅显式 development 允许 SQLite/不安全 Cookie |
| DATABASE_URL | 生产 PostgreSQL + psycopg |
| SECRET | 至少 32 字符强随机值，用于哈希令牌 |
| OWNER_USERNAME / OWNER_PASSWORD | 仅首次创建 Owner；密码不低于 16 字符 |
| STORAGE_ROOT | Compose 为 /data/artifacts，数据库不存二进制 |
| HEARTBEAT_TIMEOUT / LEASE_SECONDS | 45 / 30 秒 |
| USER_ACTIVE_JOB_LIMIT / USER_DAILY_JOB_LIMIT | 4 / 滚动 24 小时 1000 |
| ARTIFACT_MAX_BYTES / ARTIFACT_RETENTION_DAYS | 单文件及每 attempt 总量 10 MiB / 7 天，每 attempt 至多 8 件 |
| CHAT_WAIT_SECONDS | 120；等待超时不等于确认节点执行结束 |
| SECURE_COOKIES | 生产必须 true |
| BIND_HOST | 默认回环；Compose 内显式 0.0.0.0 |

如提高工件大小上限，须同步提高 Caddy 的 max_size，至少覆盖 Base64 膨胀后的 4/3 大小及 JSON 开销。生产按负载配置磁盘监控，安排每日清理：

```bash
docker compose exec -T server pcc-server cleanup
```

清理已过期工件/会话，以及超过一天的孤立文件；不会删除任务审计和历史元数据。可通过宿主机 cron 调用上述命令。不要把节点凭证复制到云端存储管理配置。

## 备份

```bash
sh deploy/backup.sh tmp/backups/$(date +%Y%m%d-%H%M%S)
```

脚本暂停 Server，以同一停写窗口备份 PostgreSQL 和工件，再恢复 Server；活跃 Agent 会因租约失败停止自己的 Worker，恢复后可能需要核对。目录权限受 umask 077 保护。将备份复制到受控异地介质并加密；`.env` 和 `PCC_SECRET` 必须另行加密保存，默认不混入数据备份。

## 恢复

恢复脚本只接受新数据库和空工件目录，拒绝覆盖已有数据。先在隔离环境演练，保持相同版本和 `PCC_SECRET`；在新主机配置 `.env` 后执行：

```bash
sh deploy/restore.sh tmp/backups/SNAPSHOT_DIRECTORY
```

脚本按流校验和解包工件，不将整个归档落入容器 64 MiB tmpfs；校验固定文件名的 SHA256 清单，检查归档路径、重复条目、符号/硬链接、展开大小和目标目录；恢复前通过 PostgreSQL 系统目录拒绝含用户表、视图或序列的目标库，包括与 PCC 无关的表。数据库使用单事务恢复且不执行 `--clean`。全部恢复完成前不启动 Server。失败后保留离线状态，检查原因并在新的空目标重试；不要把半恢复状态开放给用户。

本轮起备份包含 manifest version 1、可搬移的 SHA256SUMS、pcc.dump 和 artifacts.tar.gz；使用只包含不透明工件文件名的归档格式。旧脚本生成的含目录条目的归档不能直接交给新恢复脚本，应在隔离目录审查后迁移。校验和用于检测损坏，不代替可信保管和加密。

检查健康、登录、节点重连、工件 SHA256；旧租约进入 reconciling 后先确认原节点进程终止，再人工核对。已在本地 PostgreSQL 18.6 非超级用户角色完成真实备份→清空测试表与工件→恢复→重新登录/授权下载演练；这不能替代生产 Docker 卷和公网 TLS 的实机验收。

## 原生 PostgreSQL 备份与演练

不使用 Compose 的管理环境也可调用同一格式的 Python 工具。需要与服务端相同或更新版本的 pg_dump/pg_restore，连接参数从 PCC_DATABASE_URL 环境变量读取；密码不会出现在命令行或快照中。先停止所有 Server 写入，再显式确认 quiesced：

```bash
# PCC_DATABASE_URL 指向要备份的库；工件目录按部署配置填写。
python -m pcc_server.backup create tmp/backups/SNAPSHOT --storage-root tmp/artifacts --quiesced
python -m pcc_server.backup verify tmp/backups/SNAPSHOT
# 修改 PCC_DATABASE_URL，指向专用的全新空恢复库；保持 Server 停止。
python -m pcc_server.backup restore tmp/backups/SNAPSHOT --storage-root tmp/restored-artifacts --quiesced
# 客户端未在 PATH 时可添加 --pg-bin /absolute/postgresql/bin。
```

恢复目标必须为空；工具不会自动删除旧数据。重启 Server 前核验数据库、文件、身份和迁移版本。需要预留足够的磁盘与备份传输空间；快照不包含 .env、私钥或模型权重。
