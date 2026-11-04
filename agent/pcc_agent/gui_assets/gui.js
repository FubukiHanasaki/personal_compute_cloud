// Author: FubukiHanasaki. Enrollment tokens and node credentials are never stored in the browser.
"use strict";
const $ = (id) => document.getElementById(id);
const stringFields = [
  "server",
  "name",
  "mode",
  "ca_file",
  "windows_worker_runtime",
  "credential_file",
  "work_root",
  "ollama_executable",
  "ollama_model_root",
  "ollama_gpu_device",
  "comfyui_python",
  "comfyui_script",
  "comfyui_gpu_device",
  "ninfer_executable",
  "ninfer_gpu_device",
];
const numericFields = [
  "allocatable_cpu",
  "allocatable_memory_mb",
  "allocatable_vram_mb",
  "max_cpu_load",
  "max_memory_load",
  "heartbeat_seconds",
  "ollama_port",
  "ollama_vram_mb",
  "comfyui_port",
  "ninfer_port",
  "ninfer_vram_mb",
  "service_idle_seconds",
];
const boolFields = [
  "enable_compute",
  "enable_synthetic_ai",
  "allow_development_http",
];
let guiKey = "";
try {
  guiKey = sessionStorage.getItem("pcc-gui-session") || "";
} catch {}
let state,
  dirty = false,
  locked = false;
const sections = ["connection", "resources", "models", "files", "enrollment"];
function showSection(id) {
  for (const key of sections) $(key).hidden = key !== id;
  $("config-form").hidden = id === "enrollment";
  if (id === "files") $("files").open = true;
  for (const link of document.querySelectorAll("nav a")) {
    if (link.hash === "#" + id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}
for (const link of document.querySelectorAll("nav a"))
  link.addEventListener("click", (event) => {
    event.preventDefault();
    showSection(link.hash.slice(1));
  });
showSection("connection");
function announce(text, error = false) {
  $(error ? "error" : "notice").textContent = text;
  $(error ? "error" : "notice").hidden = false;
  $(error ? "notice" : "error").hidden = true;
}
async function request(path, body) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    headers: { "X-PCC-GUI": guiKey },
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-PCC-GUI": guiKey,
            "X-CSRF-Token": state.csrf,
          },
          body: JSON.stringify(body),
        }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}
async function action(task) {
  if (locked) return;
  locked = true;
  $("notice").hidden = true;
  $("error").hidden = true;
  for (const id of ["save", "reload", "connect", "register"])
    $(id).disabled = true;
  try {
    await task();
  } catch (e) {
    announce(e.message, true);
  } finally {
    locked = false;
    for (const id of ["save", "reload", "connect", "register"])
      $(id).disabled = false;
  }
}
function input(label, value, key, type = "text", min) {
  const wrapper = document.createElement("label");
  wrapper.textContent = label;
  const element = document.createElement("input");
  element.type = type;
  element.value = value;
  element.dataset.key = key;
  element.required = !["sampler_node", "image_nodes"].includes(key);
  if (min !== undefined) element.min = min;
  wrapper.append(element);
  return wrapper;
}
function addWorkflow(name = "", values = {}) {
  const row = document.createElement("article");
  row.className = "workflow";
  const heading = document.createElement("header"),
    title = document.createElement("h3"),
    remove = document.createElement("button");
  title.textContent = "图片工作流";
  remove.type = "button";
  remove.textContent = "移除";
  remove.onclick = () => {
    row.remove();
    markDirty();
  };
  heading.append(title, remove);
  const fields = document.createElement("div");
  fields.className = "grid";
  fields.append(
    input("工作流 ID", name, "id"),
    input("API 工作流 JSON 文件", values.file || "", "file"),
    input("正向提示词节点", values.prompt_node || "6", "prompt_node"),
    input("图像尺寸节点", values.latent_node || "5", "latent_node"),
    input("采样节点（可选）", values.sampler_node || "", "sampler_node"),
    input(
      "参考图片节点（逗号分隔，可选）",
      (values.image_nodes || []).join(", "),
      "image_nodes",
    ),
    input(
      "最低显存 MB",
      values.min_vram_mb ?? 1024,
      "min_vram_mb",
      "number",
      0,
    ),
  );
  row.append(heading, fields);
  $("workflows").append(row);
}
function render() {
  const config = state.config;
  for (const key of [...stringFields, ...numericFields])
    $(key).value = config[key] ?? "";
  for (const key of boolFields) $(key).checked = config[key];
  $("allowed_users").value = config.allowed_users.join(", ");
  $("ollama_models").value = config.ollama_models.join("\n");
  $("ninfer_models").value = Object.entries(config.ninfer_models || {})
    .map(([name, path]) => `${name}=${path}`)
    .join("\n");
  $("workflows").replaceChildren();
  for (const [name, values] of Object.entries(config.comfyui_workflows))
    addWorkflow(name, values);
  $("config-path").textContent = state.path;
  dirty = false;
  $("save-state").textContent = "连接与模型设置保存后需重启 Agent。";
}
function markDirty() {
  dirty = true;
  $("save-state").textContent = "有未保存的修改";
}
$("config-form").addEventListener(
  "invalid",
  (event) => {
    const section = event.target.closest(
      "#connection,#resources,#models,#files",
    );
    if (section) showSection(section.id);
    let container = event.target.parentElement;
    while (container) {
      if (container.tagName === "DETAILS") container.open = true;
      container = container.parentElement;
    }
    requestAnimationFrame(() => event.target.focus());
  },
  true,
);
$("config-form").addEventListener("input", markDirty);
$("config-form").addEventListener("change", markDirty);
$("add-workflow").onclick = () => {
  if ($("workflows").children.length >= 32)
    return announce("最多配置 32 个工作流", true);
  addWorkflow();
  markDirty();
};
$("config-form").onsubmit = (e) => {
  e.preventDefault();
  action(async () => {
    const config = structuredClone(state.config);
    for (const key of stringFields)
      config[key] =
        $(key).value.trim() ||
        (["ca_file", "windows_worker_runtime"].includes(key) ? null : "");
    for (const key of numericFields) config[key] = Number($(key).value);
    for (const key of boolFields) config[key] = $(key).checked;
    config.allowed_users = $("allowed_users")
      .value.split(/[,，\n]/)
      .map((v) => v.trim())
      .filter(Boolean);
    config.ollama_models = $("ollama_models")
      .value.split(/[,，\n]/)
      .map((v) => v.trim())
      .filter(Boolean);
    config.ninfer_models = Object.fromEntries(
      $("ninfer_models")
        .value.split(/\r?\n/)
        .filter((line) => line.trim())
        .map((line) => {
          const index = line.indexOf("=");
          if (index < 1) throw new Error("NInfer 映射格式为 模型ID=完整路径");
          return [line.slice(0, index).trim(), line.slice(index + 1).trim()];
        }),
    );
    config.comfyui_workflows = {};
    for (const row of $("workflows").children) {
      const value = Object.fromEntries(
        [...row.querySelectorAll("input")].map((el) => [
          el.dataset.key,
          el.type === "number" ? Number(el.value) : el.value.trim(),
        ]),
      );
      value.image_nodes = value.image_nodes
        .split(/[,，]/)
        .map((v) => v.trim())
        .filter(Boolean);
      const name = value.id;
      delete value.id;
      if (
        !/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$/.test(name) ||
        Object.hasOwn(config.comfyui_workflows, name)
      )
        throw new Error("工作流 ID 无效或重复");
      Object.defineProperty(config.comfyui_workflows, name, {
        value,
        enumerable: true,
        configurable: true,
        writable: true,
      });
    }
    const result = await request("/api/config", {
      config,
      revision: state.revision,
    });
    state = { ...state, ...result };
    render();
    announce("配置已保存。连接与模型设置需重启 Agent。");
  });
};
$("reload").onclick = () => {
  if (dirty && !confirm("放弃未保存的修改并重新加载？")) return;
  action(async () => {
    state = await request("/api/state");
    render();
    announce("已重新加载");
  });
};
$("connect").onclick = () =>
  action(async () => {
    if (dirty) throw new Error("请先保存配置，再测试连接");
    const result = await request("/api/connect", {});
    announce(result.message);
  });
$("register-form").onsubmit = (e) => {
  e.preventDefault();
  action(async () => {
    if (dirty) throw new Error("请先保存配置，再注册节点");
    if (!confirm(`将节点注册到 ${state.config.server}？`)) return;
    const token = $("token").value;
    $("token").value = "";
    const result = await request("/api/register", { token });
    announce(result.message);
  });
};
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
let metricPending = false;
async function sample() {
  if (metricPending || document.hidden) return;
  metricPending = true;
  try {
    const m = await request("/api/metrics");
    for (const [key, value] of [
      ["cpu", m.cpu_percent],
      ["memory", m.memory_percent],
    ]) {
      const percent = Math.round(Math.max(0, Math.min(100, value)));
      $(key + "-ring").style.setProperty("--value", percent + "%");
      $(key + "-ring").setAttribute("aria-valuenow", percent);
      $(key + "-value").textContent = percent + "%";
    }
    $("cpu-detail").textContent = m.cpu_count + " 个逻辑核心";
    $("memory-detail").textContent =
      `可用 ${(m.memory_available_mb / 1024).toFixed(1)} / ${(m.memory_total_mb / 1024).toFixed(1)} GB`;
    $("gpu-detail").replaceChildren();
    for (const gpu of m.gpu) {
      const text = document.createElement("p"),
        bar = document.createElement("progress");
      text.textContent = `${gpu.name} · ${gpu.used_mb} / ${gpu.total_mb} MB`;
      bar.max = Math.max(1, gpu.total_mb);
      bar.value = gpu.used_mb;
      bar.setAttribute("aria-label", gpu.name + "显存使用量");
      $("gpu-detail").append(text, bar);
    }
    if (!m.gpu.length) $("gpu-detail").textContent = "未检测到 NVIDIA GPU";
    $("sample-time").textContent =
      "采样于 " + new Date(m.sampled_at * 1000).toLocaleTimeString("zh-CN");
  } catch {
    $("sample-time").textContent = "采样失败；当前显示为上次数据";
  } finally {
    metricPending = false;
  }
}
const names = {
  primary: "主色",
  surface: "底色",
  accent: "强调色",
  soft: "柔和色",
  warm: "暖色",
  background: "背景",
  text: "文字基色",
};
const defaults = getComputedStyle(document.documentElement);
const palette = Object.fromEntries(
  Object.entries(names).map(([key, label]) => [
    key,
    [label, defaults.getPropertyValue("--theme-" + key).trim()],
  ]),
);
let theme = {};
try {
  const saved = JSON.parse(localStorage.getItem("pcc-theme") || "{}");
  for (const key of Object.keys(palette))
    if (/^#[0-9a-f]{6}$/i.test(saved[key])) theme[key] = saved[key];
} catch {
  /* Optional browser storage. */
}
function applyTheme() {
  for (const [key, [label, fallback]] of Object.entries(palette)) {
    document.documentElement.style.setProperty(
      "--theme-" + key,
      theme[key] || fallback,
    );
    const control = $("theme-" + key);
    if (control) control.value = theme[key] || fallback;
  }
  try {
    localStorage.setItem("pcc-theme", JSON.stringify(theme));
  } catch {}
}
for (const [key, [label, fallback]] of Object.entries(palette)) {
  const wrapper = input(label, theme[key] || fallback, key, "color");
  const control = wrapper.querySelector("input");
  control.id = "theme-" + key;
  control.oninput = () => {
    theme[key] = control.value;
    applyTheme();
  };
  $("theme-colors").append(wrapper);
}
$("reset-theme").onclick = () => {
  theme = {};
  applyTheme();
};
applyTheme();
(async () => {
  try {
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    if (token) {
      history.replaceState(null, "", location.pathname);
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("请使用终端生成的完整配置页链接");
      guiKey = (await response.json()).key;
      try {
        sessionStorage.setItem("pcc-gui-session", guiKey);
      } catch {}
    }
    state = await request("/api/state");
    render();
    await sample();
    setInterval(sample, 3000);
  } catch (e) {
    announce(e.message, true);
    for (const id of ["save", "connect", "register"]) $(id).disabled = true;
  }
})();
