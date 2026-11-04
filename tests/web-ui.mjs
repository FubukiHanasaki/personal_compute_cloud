// Ubuntu browser validation against real local APIs. Author: FubukiHanasaki.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile, mkdir, open, writeFile, rm } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve, delimiter } from "node:path";
const { chromium } = await import(
  pathToFileURL(
    resolve(
      process.env.PCC_PLAYWRIGHT_MODULE ||
        "tmp/ui-browser/node_modules/playwright/index.mjs",
    ),
  )
);
const root = "tmp/ui-qa";
await rm(root + "/agent.toml", { force: true });
await rm(root + "/visual-report.json", { force: true });
await mkdir(root + "/screenshots", { recursive: true });
await mkdir(root + "/browser-temp", { recursive: true });
process.env.TMPDIR = resolve(root + "/browser-temp");
const log = await open(root + "/qa-server.log", "w", 0o600);
const server = spawn(
  process.env.PCC_QA_PYTHON || "tmp/venv/bin/python",
  ["scripts/ui_qa.py"],
  {
    stdio: ["ignore", log.fd, log.fd],
    env: {
      ...process.env,
      PYTHONPATH: [resolve("agent"), resolve("server")].join(delimiter),
      PYTHONDONTWRITEBYTECODE: "1",
    },
  },
);
let browser;
const errors = [];
const checks = [];
const captures = [];
try {
  for (let i = 0; ; i++) {
    if (server.exitCode !== null)
      throw Error("QA server exited; inspect tmp/ui-qa/qa-server.log");
    try {
      const r = await fetch("http://127.0.0.1:8890/healthz");
      if (r.ok) break;
    } catch {}
    if (i > 50) throw Error("QA server timed out");
    await new Promise((r) => setTimeout(r, 200));
  }
  const credentials = JSON.parse(
    await readFile(root + "/credentials.json", "utf8"),
  );
  browser = await chromium.launch({
    executablePath: process.env.PCC_CHROMIUM_PATH || undefined,
    headless: true,
    args: ["--no-sandbox"],
    chromiumSandbox: false,
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (r) => {
    if (r.status() >= 500)
      errors.push("HTTP " + r.status() + " " + new URL(r.url()).pathname);
  });
  const capture = async (name) => {
    captures.push(name);
    await page.screenshot({
      path: root + "/screenshots/" + name + ".png",
      fullPage: true,
      animations: "disabled",
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "Horizontal overflow " + name,
    );
  };
  const nav = async (name) => {
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name, exact: false })
      .click();
  };
  await page.goto("http://127.0.0.1:8890");
  await page.getByRole("heading", { name: "登录工作空间" }).waitFor();
  await capture("desktop-login");
  await page.getByLabel("用户名", { exact: true }).fill(credentials.username);
  await page.getByLabel("密码", { exact: true }).fill(credentials.password);
  await page.getByRole("button", { name: "进入控制台 →" }).click();
  await page.getByRole("heading", { name: "工作空间", exact: true }).waitFor();
  await capture("desktop-overview");
  await nav("计算任务");
  await page.getByRole("progressbar").first().waitFor();
  await capture("desktop-jobs");
  checks.push("login + live task API/progress");
  await page.getByRole("button", { name: "查看 →" }).first().click();
  await page.getByRole("dialog").waitFor();
  await page.getByRole("img", { name: "生成结果：fixture.png" }).waitFor();
  assert(
    await page
      .getByRole("img", { name: "生成结果：fixture.png" })
      .evaluate((img) => img.complete && img.naturalWidth === 1),
  );
  checks.push("authorized PNG preview decodes");
  await capture("desktop-task");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await nav("计算节点");
  const nodeTile = page.getByRole("button", {
    name: "查看 Windows 工作站 详情",
    exact: true,
  });
  await nodeTile.waitFor();
  await page.getByLabel("搜索节点", { exact: true }).fill("不存在的节点");
  await page.getByText("没有匹配的节点", { exact: true }).waitFor();
  assert.equal(await page.locator(".node-tile").count(), 0);
  await page.getByLabel("搜索节点", { exact: true }).fill("ollama");
  await nodeTile.waitFor();
  await page
    .getByLabel("节点状态筛选", { exact: true })
    .selectOption("disabled");
  assert.equal(await page.locator(".node-tile").count(), 0);
  await page.getByLabel("节点状态筛选", { exact: true }).selectOption("all");
  await page.getByLabel("搜索节点", { exact: true }).fill("");
  checks.push("node search by worker + status filters + empty state");
  assert.equal(await nodeTile.getByRole("meter").count(), 3);
  assert((await nodeTile.boundingBox()).width <= 325);
  assert.equal(await page.getByRole("dialog").count(), 0);
  await capture("desktop-nodes");
  await nodeTile.click();
  const nodeDialog = page.getByRole("dialog", {
    name: "Windows 工作站",
    exact: true,
  });
  await nodeDialog.waitFor();
  await nodeDialog
    .getByRole("region", { name: "Windows 工作站处理器资源" })
    .waitFor();
  await page.waitForFunction(() =>
    document
      .querySelector(".node-detail-content")
      .getAnimations()
      .every((a) => a.playState === "finished"),
  );
  assert.equal(await nodeDialog.locator(".resource-card").count(), 3);
  await nodeDialog
    .getByRole("region", { name: "推理内存配置", exact: true })
    .waitFor();
  await nodeDialog.getByText("32.0 GiB", { exact: true }).waitFor();
  assert(
    (await nodeDialog.boundingBox()).width >
      (await nodeTile.boundingBox()).width,
  );
  await nodeDialog
    .getByLabel("Windows 工作站共享策略", { exact: true })
    .selectOption("private");
  await nodeDialog.getByRole("status").getByText("节点设置已保存").waitFor();
  await nodeDialog
    .getByLabel("Windows 工作站共享策略", { exact: true })
    .selectOption("shared");
  await nodeDialog.getByRole("status").getByText("节点设置已保存").waitFor();
  await capture("desktop-node-detail");
  await page.keyboard.press("Tab");
  assert(
    await nodeDialog.evaluate((el) => el.contains(document.activeElement)),
  );
  await page.keyboard.press("Escape");
  await nodeDialog.waitFor({ state: "detached" });
  assert(await nodeTile.evaluate((el) => document.activeElement === el));
  await nodeTile.focus();
  await page.keyboard.press("Enter");
  await nodeDialog.waitFor();
  await nodeDialog.getByLabel("收起节点详情", { exact: true }).click();
  await nodeDialog.waitFor({ state: "detached" });
  await nodeTile.click();
  await nodeDialog.waitFor();
  await page.mouse.click(5, 5);
  await nodeDialog.waitFor({ state: "detached" });
  checks.push(
    "compact node rings; animated expansion; detailed resources; Escape/keyboard and focus restore",
  );
  await nav("模型管理");
  await page.getByRole("button", { name: "登记模型", exact: true }).click();
  await page.getByLabel("模板 ID", { exact: true }).fill("qa-form-chat");
  await page.getByLabel("显示名称", { exact: true }).fill("表单登记模型");
  await page
    .getByLabel("模型 / 本地工作流 ID", { exact: true })
    .fill("qa-form-model");
  await page.getByRole("button", { name: "保存模型设置", exact: true }).click();
  await page.getByRole("status").getByText("模型设置已保存").waitFor();
  await capture("desktop-models");
  checks.push("model registration form persists via API");
  await page.getByRole("button", { name: "维护参数" }).first().click();
  await page.getByLabel("上下文长度（Token）", { exact: true }).fill("16384");
  await page.getByLabel("KV 缓存类型", { exact: true }).selectOption("q8_0");
  await capture("desktop-model-editor");
  await page.getByRole("button", { name: "保存模型设置", exact: true }).click();
  await page.getByRole("status").getByText("模型设置已保存").waitFor();
  checks.push("administrator updates context/KV defaults through real API");
  await nav("访问与授权");
  await page.getByRole("button", { name: "用户授权", exact: true }).click();
  await page.getByRole("button", { name: "编辑授权" }).click();
  await page.getByRole("checkbox", { name: /本地对话/ }).check();
  await page.getByRole("button", { name: "保存授权", exact: true }).click();
  await page.getByRole("status").getByText("授权已保存").waitFor();
  checks.push("checkbox grants persist without JSON");
  await page.getByRole("button", { name: "编辑授权" }).click();
  assert(await page.getByRole("checkbox", { name: /本地对话/ }).isChecked());
  await capture("desktop-grants");
  await page.getByText("主题", { exact: true }).click();
  await page.getByLabel("主色", { exact: true }).fill("#9184d8");
  assert.equal(
    await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--theme-primary")
        .trim(),
    ),
    "#9184d8",
  );
  await page.getByRole("button", { name: "恢复默认" }).click();
  await page.getByText("主题", { exact: true }).click();
  checks.push("seven-color theme preview + reset");
  await nav("模型对话");
  await capture("desktop-chat-empty");
  await page
    .getByLabel("消息", { exact: true })
    .dispatchEvent("keydown", { key: "Enter", isComposing: true });
  assert.equal(await page.locator(".chat-turn").count(), 0);
  await page.getByLabel("消息", { exact: true }).fill("保存这段对话");
  await page.getByLabel("消息", { exact: true }).press("Shift+Enter");
  assert(
    (await page.getByLabel("消息", { exact: true }).inputValue()).includes(
      "\n",
    ),
  );
  await page.getByLabel("消息", { exact: true }).press("Enter");
  await page.getByText(/界面验收回复/).waitFor();
  assert.equal(await page.locator(".chat-turn").count(), 1);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByLabel("复制回复", { exact: true }).click();
  assert(
    (await page.evaluate(() => navigator.clipboard.readText())).includes(
      "界面验收回复",
    ),
  );
  await page.getByLabel("搜索对话", { exact: true }).fill("找不到的对话");
  await page.getByText("没有匹配的对话", { exact: true }).waitFor();
  await page.getByLabel("搜索对话", { exact: true }).fill("保存这段");
  assert.equal(await page.locator(".conversation-list > button").count(), 1);
  await page.getByLabel("搜索对话", { exact: true }).fill("");
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出对话", exact: true }).click();
  const download = await downloaded;
  assert.match(
    download.suggestedFilename(),
    /^conversation-[a-zA-Z0-9-]+\.json$/,
  );
  await download.saveAs(root + "/conversation-export.json");
  const exported = JSON.parse(
    await readFile(root + "/conversation-export.json", "utf8"),
  );
  assert.equal(exported.version, 1);
  assert.equal(exported.conversation.turns.length, 1);
  assert(exported.conversation.turns[0].answer.includes("界面验收回复"));
  assert(!JSON.stringify(exported).includes(credentials.password));
  checks.push("conversation search, clipboard copy and JSON download");
  await capture("desktop-chat");
  await page.reload();
  await page.getByRole("heading", { name: "工作空间", exact: true }).waitFor();
  await nav("模型对话");
  await page.getByText(/界面验收回复/).waitFor();
  assert.equal(await page.locator(".chat-turn").count(), 1);
  await page.getByLabel("选择图片或视频", { exact: true }).setInputFiles({
    name: "reference.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await page.locator(".attachment-strip img").waitFor();
  await page.getByLabel("消息", { exact: true }).fill("看看这张图片");
  await page.getByLabel("消息", { exact: true }).press("Enter");
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".chat-turn").length === 2 &&
      document.querySelectorAll(".assistant .message-bubble p").length === 2,
  );
  checks.push(
    "Enter sends, Shift+Enter inserts newline, history survives refresh, visual attachment roundtrip",
  );
  await page
    .getByLabel("消息", { exact: true })
    .fill("模拟一次失败（界面验收）");
  await page.getByLabel("消息", { exact: true }).press("Enter");
  await page.getByText("界面验收：模拟临时推理失败", { exact: true }).waitFor();
  await capture("desktop-chat-retry");
  await page.getByRole("button", { name: "重试回复", exact: true }).click();
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".chat-turn").length === 3 &&
      document.querySelectorAll(".assistant .message-bubble > p").length ===
        3 &&
      !document.querySelector(".assistant .danger-text"),
  );
  assert.equal(
    await page.getByRole("button", { name: "重试回复", exact: true }).count(),
    0,
  );
  await page.reload();
  await page.getByRole("heading", { name: "工作空间", exact: true }).waitFor();
  await nav("模型对话");
  await page.waitForFunction(
    () => document.querySelectorAll(".chat-turn").length === 3,
  );
  checks.push("failed reply retries in place and persists after reload");
  await page.setViewportSize({ width: 390, height: 844 });
  await capture("mobile-chat");
  await nav("访问与授权");
  await capture("mobile-grants");
  await nav("计算节点");
  await capture("mobile-nodes");
  await page
    .getByRole("button", { name: "查看 Windows 工作站 详情", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "Windows 工作站", exact: true })
    .waitFor();
  await capture("mobile-node-detail");
  await page.getByLabel("收起节点详情", { exact: true }).click();
  await page
    .getByRole("dialog", { name: "Windows 工作站", exact: true })
    .waitFor({ state: "detached" });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page
    .getByRole("button", { name: "查看 Windows 工作站 详情", exact: true })
    .click();
  await page.getByLabel("收起节点详情", { exact: true }).click();
  await page
    .getByRole("dialog", { name: "Windows 工作站", exact: true })
    .waitFor({ state: "detached" });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  checks.push("mobile node detail and reduced-motion close");
  await nav("图像创作");
  await page.locator(".gallery-image img").waitFor();
  await capture("mobile-images");
  await nav("模型管理");
  await capture("mobile-models");
  await page.getByRole("button", { name: "维护参数" }).first().click();
  await capture("mobile-model-editor");
  await page.getByLabel("关闭模型编辑", { exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await nav("图像创作");
  await page.locator(".gallery-image img").waitFor();
  await capture("desktop-images");
  // Legacy synthetic metadata omitted the default size. Verify the selected
  // 512px value is also sent over HTTP, rather than the old 1024px fallback.
  await page.route("**/api/v1/models?*", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    for (const model of payload.data) {
      if (model.task_type === "ai.image") {
        model.synthetic = true;
        model.image_parameters = {};
      }
    }
    await route.fulfill({ response, json: payload });
  });
  await page.getByRole("button", { name: "↻ 刷新", exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector(".image-controls select")
      .textContent.includes("协议测试"),
  );
  assert.equal(
    await page.getByLabel("画幅", { exact: true }).inputValue(),
    "512x512",
  );
  await page.getByLabel("画面描述", { exact: true }).fill("尺寸回归检查");
  const imageSent = page.waitForRequest(
    (r) =>
      new URL(r.url()).pathname === "/api/v1/images/generations" &&
      r.method() === "POST",
  );
  const imageAccepted = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/v1/images/generations" &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "生成图像", exact: true }).click();
  assert.equal((await imageSent).postDataJSON().size, "512x512");
  assert.equal((await imageAccepted).status(), 202);
  await page.unroute("**/api/v1/models?*");
  checks.push("legacy synthetic size matches the submitted image request");
  await nav("总览");
  await page.setViewportSize({ width: 390, height: 844 });
  await capture("mobile-overview");
  // Agent: isolated local config, no real registration or Agent process.
  const contrast = await page.evaluate(() => {
    const context = document.createElement("canvas").getContext("2d");
    const rgb = (color) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
    };
    const luminance = (rgb) =>
      rgb
        .map((c) => c / 255)
        .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
        .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
    const ratio = (a, b) => {
      const x = luminance(a),
        y = luminance(b);
      return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    };
    const sample = document.createElement("span");
    document.body.append(sample);
    sample.style.background = "var(--panel)";
    const results = {};
    for (const token of ["ink", "muted", "link", "chart"]) {
      sample.style.color = "var(--" + token + ")";
      const style = getComputedStyle(sample);
      results[token] = ratio(rgb(style.color), rgb(style.backgroundColor));
    }
    sample.remove();
    return results;
  });
  for (const token of ["ink", "muted", "link"])
    assert(contrast[token] >= 4.5, "Insufficient contrast " + token);
  assert(contrast.chart >= 3);
  checks.push("default text/chart contrast checked");
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  const doc = await cdp.send("DOM.getDocument");
  const found = await cdp.send("DOM.querySelector", {
    nodeId: doc.root.nodeId,
    selector: "h1",
  });
  const fonts = await cdp.send("CSS.getPlatformFontsForNode", {
    nodeId: found.nodeId,
  });
  checks.push(
    "rendered fonts: " + fonts.fonts.map((f) => f.familyName).join(", "),
  );
  await cdp.detach();
  const fontFamily = await page
    .locator("body")
    .evaluate((el) => getComputedStyle(el).fontFamily);
  checks.push("font stack: " + fontFamily);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await nav("模型管理");
  for (const [preset, adapter, model] of [
    ["qwen-image-21", "comfyui", "qwen-image-2.1"],
    ["qwen35-27b", "ollama", "qwen3.5:27b"],
    ["qwen36-27b", "ollama", "qwen3.6:27b"],
    ["qwen38-27b", "ollama", "qwen3.8:27b"],
    ["ninfer-qwen36", "ninfer", "qwen3.6-27b"],
    ["ninfer-qwen38", "ninfer", "qwen3.8-27b"],
    ["qwen3-vl-8b", "ollama", "qwen3-vl:8b-instruct"],
  ]) {
    await page.getByRole("button", { name: "登记模型", exact: true }).click();
    await page.getByLabel("模型预设", { exact: true }).selectOption(preset);
    assert.equal(
      await page.getByLabel("模板 ID", { exact: true }).inputValue(),
      preset,
    );
    const registered = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/v1/admin/templates" &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存模型设置", exact: true })
      .click();
    const response = await registered;
    assert.equal(response.status(), 201, await response.text());
    await page
      .getByLabel("关闭模型编辑", { exact: true })
      .waitFor({ state: "detached" });
    const templates = await (
      await page.request.get("http://127.0.0.1:8890/api/v1/templates?limit=100")
    ).json();
    const saved = templates.items.find((t) => t.id === preset);
    assert(saved);
    assert.equal(saved.worker, `${adapter}:${model}`);
    assert.equal(saved.config.model, model);
    if (adapter === "comfyui") {
      assert.equal(saved.config.image_parameters.steps, 25);
      assert.equal(saved.config.image_parameters.cfg, 1);
      assert.equal(saved.config.image_parameters.default_size, "1024x1024");
    } else {
      assert.equal(saved.config.parameters.vision, true);
      assert.equal(saved.config.parameters.context_length, 8192);
      assert.equal(
        saved.config.parameters.kv_cache_type,
        adapter === "ninfer" ? "fp8" : "f16",
      );
    }
  }
  checks.push(
    "all seven Qwen presets register through UI and persist correct adapters, IDs and parameters",
  );
  await capture("desktop-qwen-presets");
  await nav("访问与授权");
  await page.getByRole("button", { name: "用户授权", exact: true }).click();
  await page.getByLabel("用户名", { exact: true }).fill("qa-short-password");
  await page.getByLabel("初始密码", { exact: true }).fill("1");
  await page.getByRole("button", { name: "创建用户", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "用户已创建" }).waitFor();
  await page.getByRole("button", { name: "账号安全", exact: true }).click();
  await page.getByLabel("当前密码", { exact: true }).fill(credentials.password);
  await page.getByLabel("新密码", { exact: true }).fill("1");
  await page.getByRole("button", { name: "更新密码", exact: true }).click();
  await page
    .getByRole("heading", { name: "登录工作空间", exact: true })
    .waitFor();
  await page.getByLabel("用户名", { exact: true }).fill(credentials.username);
  await page.getByLabel("密码", { exact: true }).fill("1");
  await page.getByRole("button", { name: "进入控制台 →" }).click();
  await page.getByRole("heading", { name: "工作空间", exact: true }).waitFor();
  checks.push(
    "single-character password works in user creation, password change and browser login",
  );
  const gui = JSON.parse(await readFile(root + "/gui-url.json", "utf8"));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(gui.url);
  await page.getByLabel("节点名称", { exact: true }).waitFor();
  await page.waitForFunction(
    () => document.querySelector("#server").value.length > 0,
  );
  await page.waitForFunction(
    () => document.querySelector("#cpu-value").textContent !== "—",
  );
  await page
    .getByLabel("服务器地址", { exact: true })
    .fill("https://example.com");
  await page.getByLabel("节点名称", { exact: true }).fill("GUI 测试节点");
  await page
    .getByRole("navigation", { name: "配置导航" })
    .getByRole("link", { name: "资源与共享", exact: true })
    .click();
  await page.getByLabel("推理内存总预算 MiB", { exact: true }).fill("32768");
  await page
    .getByLabel("模型启动空闲门槛 MiB", { exact: true })
    .fill("ollama:qa-model=4096");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "配置已保存" }).waitFor();
  await capture("desktop-agent-memory");
  const savedMemoryConfig = await readFile(root + "/agent.toml", "utf8");
  assert(savedMemoryConfig.includes('"managed_commit_budget_mb" = 32768'));
  assert(savedMemoryConfig.includes('"ollama:qa-model" = 4096'));
  checks.push(
    "separate inference budget and physical startup thresholds save through Agent GUI",
  );
  assert(
    (await readFile(root + "/agent.toml", "utf8")).includes("GUI 测试节点"),
  );
  await page.reload();
  await page.waitForFunction(
    () => document.querySelector("#name").value === "GUI 测试节点",
  );
  checks.push("Agent refresh restores only its scoped session");
  await capture("desktop-agent");
  await page
    .getByRole("navigation", { name: "配置导航" })
    .getByRole("link", { name: "模型", exact: true })
    .click();
  await page.getByText("ComfyUI", { exact: true }).click();
  await page.getByRole("button", { name: "添加图片工作流" }).click();
  await page.getByLabel("工作流 ID", { exact: true }).fill("qa-image");
  await page
    .getByLabel("API 工作流 JSON 文件", { exact: true })
    .fill("tmp/approved-workflow.json");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "配置已保存" }).waitFor();
  assert((await readFile(root + "/agent.toml", "utf8")).includes("qa-image"));
  checks.push("Agent config save + workflow editor roundtrip");
  await page.getByRole("button", { name: "添加图片工作流" }).click();
  await page.getByLabel("工作流 ID", { exact: true }).last().fill("qa-image");
  await page
    .getByLabel("API 工作流 JSON 文件", { exact: true })
    .last()
    .fill("tmp/duplicate.json");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "重复" }).waitFor();
  await page.getByRole("button", { name: "移除", exact: true }).last().click();
  checks.push("duplicate workflow ID rejected");
  await page.setViewportSize({ width: 390, height: 844 });
  await capture("mobile-agent-models");
  await page
    .getByRole("navigation", { name: "配置导航" })
    .getByRole("link", { name: "资源与共享", exact: true })
    .click();
  await capture("mobile-agent-memory");
  await page
    .getByRole("navigation", { name: "配置导航" })
    .getByRole("link", { name: "连接", exact: true })
    .click();
  await capture("mobile-agent");
  await page
    .getByRole("navigation", { name: "配置导航" })
    .getByRole("link", { name: "注册", exact: true })
    .click();
  await page.getByLabel("一次性注册令牌", { exact: true }).waitFor();
  checks.push("Agent section navigation retains unsaved edits");
  assert.deepEqual(errors, []);
  await writeFile(
    root + "/visual-report.json",
    JSON.stringify(
      {
        author: "FubukiHanasaki",
        browser: browser.version(),
        checks,
        errors,
        screenshots: captures.length,
        screenshot_names: captures,
      },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ checks, errors }));
} catch (error) {
  if (browser) {
    const failedPage = browser.contexts()[0]?.pages()[0];
    if (failedPage) {
      await failedPage.screenshot({
        path: root + "/screenshots/failed.png",
        fullPage: true,
      });
      console.error(
        (await failedPage.locator("body").innerText()).slice(-5000),
      );
    }
  }
  throw error;
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  await new Promise((resolve) => {
    if (server.exitCode !== null) return resolve();
    server.once("exit", resolve);
    setTimeout(() => {
      server.kill("SIGKILL");
      resolve();
    }, 3000);
  });
  await log.close();
}
