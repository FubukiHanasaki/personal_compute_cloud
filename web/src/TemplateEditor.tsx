// Author: FubukiHanasaki
import { useState } from "react";
import { api, type Template } from "./api";
type Act = (f: () => Promise<unknown>, success?: string) => Promise<void>;
export function TemplateEditor({
  templates,
  act,
}: {
  templates: Template[];
  act: Act;
}) {
  const [adapter, setAdapter] = useState("ollama");
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [cpu, setCpu] = useState(2);
  const [memory, setMemory] = useState(2048);
  const [vram, setVram] = useState(0);
  const [timeout, setTimeout] = useState(120);
  const [idle, setIdle] = useState(300);
  const [isPublic, setPublic] = useState(false);
  const [busy, setBusy] = useState(false);
  const kind =
    adapter === "compute.stats"
      ? "compute.stats"
      : adapter.includes("image") || adapter === "comfyui"
        ? "ai.image"
        : "ai.chat";
  return (
    <section className="panel pad">
      <h2>登记模型与计算模板</h2>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await act(async () => {
              await api("/api/v1/admin/templates", "POST", {
                id,
                name,
                kind,
                worker:
                  adapter === "ollama" || adapter === "comfyui"
                    ? `${adapter}:${model}`
                    : adapter,
                ...(kind !== "compute.stats" ? { model } : {}),
                resources: { cpu, memory_mb: memory, vram_mb: vram },
                public: isPublic,
                retry_safe: kind === "compute.stats",
                max_timeout_seconds: timeout,
                idle_seconds: idle,
              });
              setId("");
              setName("");
            }, "模板已登记");
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="form-grid">
          <label>
            适配器
            <select
              value={adapter}
              onChange={(e) => setAdapter(e.target.value)}
            >
              <option value="ollama">Ollama · 对话</option>
              <option value="comfyui">ComfyUI · 图片</option>
              <option value="compute.stats">数字统计</option>
              <option value="synthetic.chat">协议测试 · 对话</option>
              <option value="synthetic.image">协议测试 · 图片</option>
            </select>
          </label>
          <label>
            模板 ID
            <input
              required
              maxLength={80}
              pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]*"
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="例如 rabbit-image"
            />
          </label>
          <label>
            显示名称
            <input
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          {kind !== "compute.stats" && (
            <label>
              模型 / 本地工作流 ID
              <input
                required
                maxLength={100}
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={
                  adapter === "ollama"
                    ? "例如 qwen3:8b"
                    : "与 Agent 配置保持一致"
                }
              />
            </label>
          )}
          <label>
            CPU 核数
            <input
              type="number"
              required
              min={1}
              max={65536}
              step={0.1}
              value={cpu}
              onChange={(e) => setCpu(Number(e.target.value))}
            />
          </label>
          <label>
            内存 MB
            <input
              type="number"
              required
              min={128}
              max={1099511627776}
              value={memory}
              onChange={(e) => setMemory(Number(e.target.value))}
            />
          </label>
          <label>
            显存 MB
            <input
              type="number"
              required
              min={0}
              max={1099511627776}
              value={vram}
              onChange={(e) => setVram(Number(e.target.value))}
            />
          </label>
          <label>
            任务超时 秒
            <input
              type="number"
              required
              min={1}
              max={3600}
              value={timeout}
              onChange={(e) => setTimeout(Number(e.target.value))}
            />
          </label>
          {kind !== "compute.stats" && (
            <label>
              空闲卸载 秒
              <input
                type="number"
                required
                min={1}
                max={86400}
                value={idle}
                onChange={(e) => setIdle(Number(e.target.value))}
              />
            </label>
          )}
        </div>
        <label className="check-label">
          <input
            type="checkbox"
            checked={isPublic}
            onChange={(e) => setPublic(e.target.checked)}
          />
          向所有用户开放
        </label>
        <p className="field-hint">
          关闭时仅 Owner 和获授权用户可用。节点需另行安装模型并启用对应适配器。
        </p>
        <button className="primary" disabled={busy}>
          {busy ? "登记中…" : "登记模板"}
        </button>
      </form>
      <div className="template-list">
        {templates.map((t) => (
          <div key={t.id}>
            <strong>{t.name}</strong>
            <code>{t.id}</code>
            <span>{t.worker}</span>
            <span>{t.public ? "公开" : "按授权开放"}</span>
            <button
              onClick={() => {
                if (
                  confirm(
                    t.public
                      ? `将 ${t.name} 改为按授权开放？已有用户授权保留。`
                      : `将 ${t.name} 向所有用户开放？`,
                  )
                )
                  void act(
                    () =>
                      api(`/api/v1/admin/templates/${t.id}/visibility`, "PUT", {
                        public: !t.public,
                      }),
                    "开放范围已更新",
                  );
              }}
            >
              {t.public ? "限制访问" : "向所有用户开放"}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
