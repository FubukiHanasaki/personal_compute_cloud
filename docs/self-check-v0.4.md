# V0.4 对照自检记录

作者：FubukiHanasaki。日期：2026-11-03，Asia/Shanghai。环境：Ubuntu 26.04.1 LTS，Python 3.13.16、Node.js 22.16.0、PostgreSQL 18.6。

这是前一轮自检的历史快照。后续实现与验收状态以 [补齐记录](completion-v0.4.md)和[最新验收矩阵](acceptance.md)为准。

## 结论

本轮完成代码、接口、权限、迁移、调度、恢复及构建自检，并修复发现的问题。SQLite 与 PostgreSQL 各 40 项自动化测试全部通过，无跳过；前端传输契约 3 项通过。对照基线仍有实机验收缺口，不能据此宣布 V0.4 全部完成或生产 MVP 发布。

用户明确要求“先跳过”视觉校验。本轮没有运行 Playwright、截图或浏览器点击；下面的响应式和键盘修改仅经过代码检查与构建，不能代替浏览器视觉通过证据。临时 Ubuntu Server 与真实 Agent 已用于准备独立验证环境，并在结束时关闭；没有修改正式账号或数据。

## 已修复

| 问题 | 修改 | 验证 |
|---|---|---|
| 图片历史从当前 30 条混合任务筛选，较早图片无法访问 | 服务端先按 task_type 筛选再分页；图像页使用独立列表和分页 | 构造较新的计算任务和 32 条历史图片，检查两页无丢失、无重复、无跨用户数据 |
| 模型与模板接口缺分页；资源选择器只加载第一页 | 模型/模板增加有界分页，授权后分页；前端读取节点、服务、模板、模型、用户及 Key 后续页 | 授权分页回归、前端 100+3 条分页传输测试 |
| SSE 提前断流会被当作完成，失败对话留下空 assistant | 要求明确的 DONE；显示中断错误，恢复用户输入和已有对话 | 中文字节分片、正常结束、截断与服务端错误帧测试 |
| 新操作失败后仍显示上次成功提示；重新登录可能保留旧页面状态 | 操作开始时清除旧提示，登录时清空页面数据及分页，丢弃旧用户的迟到查询 | TypeScript/生产构建；实际跨账号界面流程待浏览器验收 |
| 320px 窄屏节点网格固定最小宽度可能裁切；长 ID/日志可能溢出 | 网格最小宽度不超过容器；详情、日志、节点和工件长文本可换行 | 样式检查与构建；截图未执行 |
| 任务详情键盘焦点可离开弹窗 | 限制 Tab/Shift+Tab 范围，Escape 关闭并恢复原焦点，锁定背景滚动 | TypeScript/生产构建；键盘实测待执行 |
| 测试与 CI 的临时路径不统一 | pytest basetemp 固定到 tmp/pytest，前端测试产物位于 tmp/web-api；CI 复用 tmp 安装流程 | 两种数据库复验，Git 忽略检查 |

## 基线缺口

| 基线 | 当前状态与缺少的证据 |
|---|---|
| A01、A03、A04 | 尚未在 Docker 主机上完成真实域名 TLS、NAT 出站 HTTPS、重启持久性验证。本地真实 HTTP 闭环已通过 |
| A02、A06 | Linux 隔离计算已实测；Windows Agent 配置与安装脚本已提供，但 Windows 实机接入和受限 Worker 尚未完成。Windows 不会降级执行 |
| A09 | 首个 Ollama 适配器已有真实 GPU 加载、推理、卸载证据。本轮没有重复 GPU 测试；ComfyUI 仍仅协议测试，缺真实图片权重验收 |
| A11 / P5 | 本轮按用户要求跳过浏览器与移动端视觉校验，不能标记通过 |
| A14 / P6 | 部署、备份、恢复和排障文档已提供，缺实际 Docker 备份恢复演练 |

A05、A07、A08、A10、A12、A13 的本地证据与完整逐项状态见 [验收矩阵](acceptance.md)。自动本地活动检测、S3 实现及任意代码沙箱属于后续范围；首版 SSE 是完成后的分块输出，不声称后端逐 token 流式推理。

## 复验

```bash
PYTHONDONTWRITEBYTECODE=1 tmp/venv/bin/pytest -q
# 仅指向专用、可重建测试库，测试会重建 PCC 表：
PCC_TEST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1/pcc_test \
  PYTHONDONTWRITEBYTECODE=1 tmp/venv/bin/pytest -q
npm run test:api --prefix web
npm run build --prefix web
tmp/docker-compose --env-file .env.example -f compose.yaml config --quiet
```

工具、测试库、凭证、Agent 工作目录、准备好的但未运行的浏览器脚本全部位于被 Git 忽略的 tmp。远程 CI 已更新但未触发，不算本次运行证据。Python 测试有一条上游 Starlette/httpx 弃用提示，不影响本轮结果。
