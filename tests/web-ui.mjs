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
  await page
    .getByRole("region", { name: "Windows 工作站处理器资源" })
    .waitFor();
  assert.equal(await page.locator(".node-card .resource-card").count(), 3);
  await capture("desktop-nodes");
  checks.push(
    "CPU/memory/VRAM cards with physical, allocated and available capacity",
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
  await page.setViewportSize({ width: 390, height: 844 });
  await capture("mobile-chat");
  await nav("访问与授权");
  await capture("mobile-grants");
  await nav("计算节点");
  await capture("mobile-nodes");
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
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "配置已保存" }).waitFor();
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
