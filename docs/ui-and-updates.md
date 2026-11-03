# 界面、Agent 配置与源码更新

作者：FubukiHanasaki。2026-11-04。

## 服务端

登录后的导航保留任务、对话、图片、节点、服务、访问授权和审计，按角色显示。任务以进度条和百分比呈现，节点以 CPU/内存仪表和资源分配条呈现；数据来自现有 API 与心跳，显示刷新时间，不生成模拟趋势。离线节点的资源值应结合心跳时间判断。图片任务详情可预览有效 PNG 并继续下载原始文件。

“访问与授权”分为 API 密钥、用户授权、模型与模板、账号安全。Owner 可用表单登记 Ollama、ComfyUI、统计或合成协议测试模板，填写模型 ID、额度、超时和卸载时间；切换模板的公开/按授权开放范围。模板登记不会替节点下载模型或启用执行。

编辑用户授权时，勾选模型/计算模板、节点管理和节点查看权限，原有任务授权与未列出的资源授权会保留。保存时保留用户启用状态，避免误启用禁用用户。公开模板本来就对所有用户可用；取消某用户的模板勾选不会收回公开访问。只有 Owner 能登记模板、修改开放范围或管理用户；已有服务控制仍受节点管理授权约束。

## 共用主题与字体

七个主题源变量统一在 `agent/pcc_agent/gui_assets/theme.css`，前端直接引用，Agent 安装包也携带该文件：

| 变量                 | 默认值    | 用途           |
| -------------------- | --------- | -------------- |
| `--theme-primary`    | `#5AAAFF` | 主操作与选中态 |
| `--theme-surface`    | `#FAF5ED` | 工作区底色     |
| `--theme-accent`     | `#FFD36B` | 等待与强调     |
| `--theme-soft`       | `#FFEED6` | 柔和容器       |
| `--theme-warm`       | `#FFA96B` | 暖色与负载警示 |
| `--theme-background` | `#FFFFFF` | 面板背景       |
| `--theme-text`       | `#797979` | 文字基色       |

正文、链接、图表线条及危险操作颜色从这七个变量派生；正文使用更深的文字色，避免直接将浅蓝/灰色用于小字。服务端页脚和 Agent 配置页提供七色选择与恢复默认，选择仅保存在当前浏览器来源的 localStorage，不影响其他用户。两套页面共享默认色源；由于服务端和本机 GUI 属于不同来源，浏览器自选主题不会跨来源同步。修改源文件后需重新构建服务端前端、重新安装 Agent 包。后续更换色卡只需调整这七个源值，同时复核新背景与文字的对比度。

字体使用操作系统界面字体，依次回退到 Segoe UI、Noto Sans CJK SC、微软雅黑、苹方与 sans-serif。表单正文 14–15px；数字使用等宽数字，日志与 ID 使用等宽字体。没有在线字体请求。保留标签、键盘焦点、错误、输入限制、重要操作确认及 reduced-motion 支持。

## Agent 本机 GUI

在已安装最新 Agent 的环境中运行：

```sh
pcc-agent gui --config agent.toml
```

未激活虚拟环境时，Linux 可用 `tmp/agent-venv/bin/pcc-agent gui --config agent.toml`，Windows 可用 `./tmp/agent-venv/Scripts/pcc-agent.exe gui --config agent.toml`；若安装在 tmp/venv，请相应替换环境目录。

自动打开浏览器，默认随机端口，只绑定 `127.0.0.1`。无桌面环境可以使用 `--no-browser --port 8891`，但仍只允许本机访问。终端显示带临时会话片段的链接；请只在本机使用，不要公开该链接。页面完成会话握手后会从地址栏移除片段；关闭 GUI 进程后旧链接失效。页面会话密钥仅保存在该来源标签页的 sessionStorage，支持刷新；节点凭证和注册令牌不进入浏览器存储。不需要把 GUI 端口开放到公网。

GUI 支持创建/修改配置、服务端地址与私有 CA、共享模式、资源额度、负载保护、独立 Windows 运行时、Ollama 模型及 ComfyUI 工作流绑定。资源监测读取本机 CPU、内存和可探测的 NVIDIA 显存，每 3 秒发起采样，上一轮未结束时不叠加，隐藏页面暂停刷新；采样失败会标注旧数据。GUI 不执行隔离探测、不启动模型或受控任务。

先保存配置，再测试连接或输入一次性令牌注册。连接与注册始终验证 HTTPS；GUI 不提供跳过证书校验选项。令牌不写入配置或浏览器存储，节点凭证通过现有受限文件写入流程保存，不返回浏览器。已有凭证时拒绝覆盖。注册成功后仍需按原部署方式启动 Agent，GUI 不启动 Agent、不安装自启动。

配置通过 AgentConfig 校验后写入同文件系统的 `tmp/gui-config`，再原子替换；使用修订摘要拒绝过期窗口覆盖，拒绝符号链接配置文件。原 TOML 注释与排版不保留，字段内容保留。模式/资源/负载保护沿用现有心跳重载；连接、路径、模型等设置需重启 Agent。默认新配置继续使用 maintenance 与计算禁用。

本机 GUI 使用启动时随机会话能力、HttpOnly/SameSite Cookie、按来源隔离的会话请求头、来源/Host/CSRF 校验和严格静态文件白名单。GUI 不能替代标准账号运行要求，`pcc-agent run` 仍拒绝 root/Administrator。Windows/Linux 为节点目标；macOS 可维护源码和配置，但本轮不增加 macOS 原生执行或注册支持。

## 各平台更新脚本

均从自己的脚本位置定位仓库，不依赖终端当前目录。默认只 fetch 官方 GitHub main 并报告版本差异，不修改工作区、不安装依赖、不重启进程。

| 平台               | 检查命令                      |
| ------------------ | ----------------------------- |
| Ubuntu / Linux     | `sh deploy/update-linux.sh`   |
| Windows PowerShell | `./deploy/update-windows.ps1` |
| macOS              | `sh deploy/update-macos.sh`   |

先通过现有 PM2/systemd/终端流程停止本机 PCC 进程并确认任务已结束，再传 `--apply --confirm-stopped` 执行快进拉取。例如：

```sh
sh deploy/update-linux.sh --apply --confirm-stopped --target server --install
```

```powershell
./deploy/update-windows.ps1 --apply --confirm-stopped --target agent --install
```

不加 `--install` 时只更新源码；Server 的安装选项同步锁定依赖并构建前端，Agent 的安装选项使用现有 `tmp/agent-venv` 或 `tmp/venv` 重新安装包；有 uv 时使用锁文件同步，无 uv 时仅在该虚拟环境内引导 pip 并安装。没有环境时先按原安装文档创建。macOS 脚本支持同一源码检查/快进逻辑，不代表已支持 macOS Agent 运行。

脚本检查 origin 必须为指定官方仓库、当前分支必须为 main；本地未提交/未跟踪修改、独有提交或分叉都会拒绝拉取。不会 reset、stash、强制切换或覆盖本地 agent.toml/.env/tmp，保留 Git reflog。通过 `tmp/update.lock` 阻止并发更新；中断后需确认无更新进程，再处理旧锁。更新锁不是任务运行锁，停止确认必须由操作者完成。

安装失败时源码可能已快进，脚本返回非零并保留现场；按错误修正后可再次安装。脚本不会迁移数据库、自动重启或操作其他进程。Server 更新后按部署说明备份、检查迁移需求，再手动启动。

## 可重复验证

常规后端测试使用 `python scripts/dev.py test`；前端使用 `npm run test:api --prefix web`、`python scripts/dev.py build`。新增测试真实启动回环 GUI，覆盖会话/跨站/DNS rebinding 防护、校验、冲突、凭证不覆盖和模型开放权限；更新测试只使用 tmp 下的本地临时 Git 远端，不接触正式仓库。

真实 UI 检查：先完成项目 setup/build，在 tmp 下安装浏览器测试依赖，然后运行：

```sh
npm install --prefix tmp/ui-browser --cache tmp/npm-cache --no-save playwright@1.64.1
```

```sh
PCC_CHROMIUM_PATH=/path/to/chrome node tests/web-ui.mjs
```

也可将 `PCC_PLAYWRIGHT_MODULE` 指向已安装的 Playwright `index.mjs`，Windows 环境可通过 `PCC_QA_PYTHON` 选择其 Python 路径。未指定浏览器路径时使用 Playwright 已安装的 Chromium；浏览器安装与测试必须在可运行环境中完成。

脚本启动 `scripts/ui_qa.py`，仅使用 `tmp/ui-qa/qa.sqlite` 的隔离夹具，端口 8890/8891；覆盖登录、任务进度、节点仪表、模型表单、授权保存、主题和 Agent 表单/工作流，保存桌面与 390px 截图。夹具中的节点和任务是页面测试数据，不证明真实 Windows 节点或模型运行；Agent GUI 的本机资源监测为实际采样。脚本退出后关闭其自己启动的测试进程，所有凭证、日志、截图与数据库留在 Git 忽略的 tmp。
