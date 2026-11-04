// Author: FubukiHanasaki
import { useState, useEffect, useRef } from "react";
import { api, type Parameters, type Template } from "./api";
import { Icon } from "./Icons";
type Act = (f: () => Promise<unknown>, success?: string) => Promise<void>;
const defaults: Parameters = {
  context_length: 8192,
  max_tokens: 2048,
  temperature: 0.7,
  top_p: 0.8,
  top_k: 20,
  repetition_penalty: 1,
  seed: 0,
  thinking: false,
  kv_cache_type: "f16",
  kv_capacity_mb: 0,
  prefix_reuse: true,
  flash_attention: true,
  vision: false,
};
const presets = [
  {
    id: "qwen-image-21",
    name: "Qwen Image 2.1",
    adapter: "comfyui",
    model: "qwen-image-2.1",
    vision: false,
  },
  {
    id: "qwen35-27b",
    name: "Qwen3.5 · 27B",
    adapter: "ollama",
    model: "qwen3.5:27b",
    vision: true,
  },
  {
    id: "qwen36-27b",
    name: "Qwen3.6 · 27B",
    adapter: "ollama",
    model: "qwen3.6:27b",
    vision: true,
  },
  {
    id: "qwen38-27b",
    name: "Qwen3.8 · 27B",
    adapter: "ollama",
    model: "qwen3.8:27b",
    vision: true,
  },
  {
    id: "ninfer-qwen36",
    name: "NInfer · Qwen3.6 27B",
    adapter: "ninfer",
    model: "qwen3.6-27b",
    vision: true,
  },
  {
    id: "ninfer-qwen38",
    name: "NInfer · Qwen3.8 27B",
    adapter: "ninfer",
    model: "qwen3.8-27b",
    vision: true,
  },
  {
    id: "qwen3-vl-8b",
    name: "Qwen3-VL-8B-Instruct",
    adapter: "ollama",
    model: "qwen3-vl:8b-instruct",
    vision: true,
  },
];
export function TemplateEditor({
  templates,
  act,
  owner = true,
}: {
  templates: Template[];
  act: Act;
  owner?: boolean;
}) {
  const editor = useRef<HTMLElement>(null);
  const [editing, setEditing] = useState<Template | null>(null),
    [open, setOpen] = useState(false),
    [search, setSearch] = useState("");
  const [id, setId] = useState(""),
    [name, setName] = useState(""),
    [adapter, setAdapter] = useState("ollama"),
    [model, setModel] = useState("");
  const [params, setParams] = useState<Parameters>(defaults),
    [resources, setResources] = useState({
      cpu: 2,
      memory_mb: 16384,
      vram_mb: 0,
    }),
    [timeout, setTimeout] = useState(600),
    [idle, setIdle] = useState(300),
    [isPublic, setPublic] = useState(false),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) {
      editor.current?.focus();
      window.scrollTo({ top: 0 });
    }
  }, [open]);
  const [image, setImage] = useState({
    steps: 25,
    cfg: 1,
    seed: 0,
    default_size: "1024x1024",
  });
  const isImage = adapter === "comfyui" || adapter === "synthetic.image",
    isCompute = adapter === "compute.stats";
  const kind = isCompute ? "compute.stats" : isImage ? "ai.image" : "ai.chat";
  function edit(t: Template) {
    const c = t.config;
    setEditing(t);
    setOpen(true);
    setId(t.id);
    setName(t.name);
    setAdapter(t.worker.includes(":") ? t.worker.split(":")[0] : t.worker);
    setModel(String(c.model || ""));
    setParams({ ...defaults, ...(c.parameters as Partial<Parameters>) });
    setImage({
      steps: 25,
      cfg: 1,
      seed: 0,
      default_size: "1024x1024",
      ...(c.image_parameters as Partial<{
        steps: number;
        cfg: number;
        seed: number;
        default_size: string;
      }>),
    });
    setResources(c.resources as typeof resources);
    setTimeout(Number(c.max_timeout_seconds));
    setIdle(Number(c.idle_seconds || 300));
    setPublic(t.public);
  }
  function preset(value: string) {
    const p = presets.find((p) => p.id === value);
    if (!p) return;
    setId(p.id);
    setName(p.name);
    setAdapter(p.adapter);
    setModel(p.model);
    setParams({
      ...defaults,
      vision: p.vision,
      kv_cache_type: p.adapter === "ninfer" ? "fp8" : "f16",
    });
    setResources({ cpu: 4, memory_mb: 32768, vram_mb: 24576 });
    setImage({ steps: 25, cfg: 1, seed: 0, default_size: "1024x1024" });
  }
  function numeric(
    key: keyof Parameters,
    label: string,
    min: number,
    max: number,
    step = 1,
  ) {
    return (
      <label key={key}>
        {label}
        <input
          type="number"
          required
          min={min}
          max={max}
          step={step}
          value={Number(params[key])}
          onChange={(e) =>
            setParams({ ...params, [key]: Number(e.target.value) })
          }
        />
      </label>
    );
  }
  return (
    <div className="model-studio">
      {!open && (
        <>
          <div className="studio-top">
            <div className="search-box">
              <Icon name="models" />
              <input
                aria-label="搜索模型"
                placeholder="搜索模型或适配器…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            {owner && (
              <button
                className="primary"
                onClick={() => {
                  setEditing(null);
                  setOpen(true);
                  setId("");
                  setName("");
                  setModel("");
                  setAdapter("ollama");
                  setParams(defaults);
                  setPublic(false);
                }}
              >
                <Icon name="plus" size={18} />
                登记模型
              </button>
            )}
          </div>
          <div className="model-catalog">
            {templates
              .filter((t) =>
                (t.name + t.worker)
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((t) => {
                const p = t.config.parameters as
                  Partial<Parameters> | undefined;
                return (
                  <article className="model-card" key={t.id}>
                    <div className="model-card-title">
                      <span
                        className={`model-emblem ${t.kind === "ai.image" ? "image" : ""}`}
                      >
                        <Icon
                          name={
                            t.kind === "ai.image"
                              ? "image"
                              : t.kind === "ai.chat"
                                ? "chat"
                                : "cpu"
                          }
                          size={26}
                        />
                      </span>
                      <div>
                        <h2>{t.name}</h2>
                        <span className="muted small">
                          {t.worker.split(":")[0]} ·{" "}
                          {t.public ? "公开" : "按授权开放"}
                        </span>
                      </div>
                    </div>
                    <code>{String(t.config.model || t.id)}</code>
                    <div className="model-chips">
                      {t.kind === "ai.chat" ? (
                        <>
                          <span>
                            {(p?.context_length || 8192).toLocaleString()}{" "}
                            上下文
                          </span>
                          <span>{p?.kv_cache_type || "f16"} KV</span>
                          <span>{p?.vision ? "视觉 · 文字" : "文字"}</span>
                        </>
                      ) : (
                        <span>
                          {t.kind === "ai.image"
                            ? "图像生成 / 编辑"
                            : "受控计算"}
                        </span>
                      )}
                    </div>
                    <div className="model-card-foot">
                      <span>
                        {Number(
                          (t.config.resources as { vram_mb: number })
                            ?.vram_mb || 0,
                        ) / 1024}{" "}
                        GB 显存预留
                      </span>
                      <button onClick={() => edit(t)}>
                        维护参数 <Icon name="settings" size={15} />
                      </button>
                    </div>
                  </article>
                );
              })}
          </div>
        </>
      )}
      {!templates.length && !open && (
        <div className="empty">
          <Icon name="models" size={32} />
          <h2>登记你的第一个模型</h2>
          <p>从 Qwen 预设开始，或连接已安装的本地模型。</p>
        </div>
      )}
      {open && (
        <section
          ref={editor}
          tabIndex={-1}
          aria-labelledby="model-editor-title"
          className="panel model-editor"
        >
          <div className="panel-heading">
            <div>
              <span className="eyebrow">MODEL SETTINGS</span>
              <h2 id="model-editor-title">
                {editing ? `维护 ${editing.name}` : "登记模型与计算模板"}
              </h2>
            </div>
            <button aria-label="关闭模型编辑" onClick={() => setOpen(false)}>
              <Icon name="close" size={18} />
            </button>
          </div>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                await act(async () => {
                  const body = {
                    name,
                    parameters: kind === "ai.chat" ? params : {},
                    image_parameters: image,
                    resources,
                    max_timeout_seconds: timeout,
                    idle_seconds: idle,
                  };
                  if (editing) {
                    await api(`/api/v1/admin/templates/${id}`, "PUT", body);
                    if (owner && isPublic !== editing.public)
                      await api(
                        `/api/v1/admin/templates/${id}/visibility`,
                        "PUT",
                        { public: isPublic },
                      );
                  } else {
                    await api("/api/v1/admin/templates", "POST", {
                      ...body,
                      id,
                      kind,
                      worker: ["ollama", "ninfer", "comfyui"].includes(adapter)
                        ? `${adapter}:${model}`
                        : adapter,
                      ...(!isCompute ? { model } : {}),
                      public: isPublic,
                      retry_safe: isCompute,
                    });
                  }
                  setOpen(false);
                }, "模型设置已保存");
              } finally {
                setBusy(false);
              }
            }}
          >
            {!editing && (
              <label className="preset-select">
                快速配置
                <select
                  aria-label="模型预设"
                  defaultValue=""
                  onChange={(e) => preset(e.target.value)}
                >
                  <option value="">自定义模型</option>
                  {presets.map((p) => (
                    <option value={p.id} key={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <fieldset>
              <legend>基本信息</legend>
              <div className="form-grid">
                <label>
                  显示名称
                  <input
                    required
                    maxLength={120}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  模板 ID
                  <input
                    required
                    disabled={!!editing}
                    pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]*"
                    maxLength={80}
                    value={id}
                    onChange={(e) => setId(e.target.value)}
                  />
                </label>
                <label>
                  适配器
                  <select
                    disabled={!!editing}
                    value={adapter}
                    onChange={(e) => {
                      const a = e.target.value;
                      setAdapter(a);
                      setParams({
                        ...params,
                        kv_cache_type: a === "ninfer" ? "fp8" : "f16",
                      });
                    }}
                  >
                    {[
                      "ollama",
                      "ninfer",
                      "comfyui",
                      "compute.stats",
                      "synthetic.chat",
                      "synthetic.image",
                    ].map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </label>
                {!isCompute && (
                  <label>
                    模型 / 本地工作流 ID
                    <input
                      required
                      disabled={!!editing}
                      maxLength={100}
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                    />
                  </label>
                )}
              </div>
            </fieldset>
            {kind === "ai.chat" && (
              <>
                <fieldset>
                  <legend>上下文与缓存</legend>
                  <div className="form-grid">
                    {numeric(
                      "context_length",
                      "上下文长度（Token）",
                      512,
                      262144,
                    )}
                    {numeric(
                      "max_tokens",
                      "输出 Token 上限",
                      1,
                      Math.min(32768, params.context_length - 1),
                    )}
                    <label>
                      KV 缓存类型
                      <select
                        aria-label="KV 缓存类型"
                        value={params.kv_cache_type}
                        onChange={(e) =>
                          setParams({
                            ...params,
                            kv_cache_type: e.target.value,
                          })
                        }
                      >
                        {(adapter === "ninfer"
                          ? ["bf16", "fp8", "int8"]
                          : ["f16", "q8_0", "q4_0"]
                        ).map((v) => (
                          <option key={v}>{v}</option>
                        ))}
                      </select>
                    </label>
                    {adapter === "ninfer" ? (
                      numeric(
                        "kv_capacity_mb",
                        "KV / State 内存池 MB（0 为自动）",
                        0,
                        65536,
                      )
                    ) : (
                      <label className="check-label">
                        <input
                          type="checkbox"
                          checked={params.flash_attention}
                          onChange={(e) =>
                            setParams({
                              ...params,
                              flash_attention: e.target.checked,
                            })
                          }
                        />
                        Flash Attention
                      </label>
                    )}
                    {adapter === "ninfer" && (
                      <label className="check-label">
                        <input
                          type="checkbox"
                          checked={params.prefix_reuse}
                          onChange={(e) =>
                            setParams({
                              ...params,
                              prefix_reuse: e.target.checked,
                            })
                          }
                        />
                        复用前缀缓存
                      </label>
                    )}
                    <label className="check-label">
                      <input
                        type="checkbox"
                        checked={params.vision}
                        onChange={(e) =>
                          setParams({ ...params, vision: e.target.checked })
                        }
                      />
                      启用视觉输入（需模型支持）
                    </label>
                  </div>
                </fieldset>
                <fieldset>
                  <legend>用户默认推理参数</legend>
                  <div className="form-grid">
                    {numeric("temperature", "温度", 0, 2, 0.05)}
                    {numeric("top_p", "Top P", 0.01, 1, 0.01)}
                    {numeric(
                      "top_k",
                      "Top K",
                      1,
                      adapter === "ninfer" ? 20 : 200,
                    )}
                    {adapter !== "ninfer" &&
                      numeric("repetition_penalty", "重复惩罚", 1, 2, 0.05)}
                    {numeric("seed", "随机种子", 0, 2147483647)}
                    <label className="check-label">
                      <input
                        type="checkbox"
                        checked={params.thinking}
                        onChange={(e) =>
                          setParams({ ...params, thinking: e.target.checked })
                        }
                      />
                      默认开启思考
                    </label>
                  </div>
                </fieldset>
              </>
            )}
            {isImage && (
              <fieldset>
                <legend>图像默认参数</legend>
                <div className="form-grid">
                  <label>
                    采样步数
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={image.steps}
                      onChange={(e) =>
                        setImage({ ...image, steps: Number(e.target.value) })
                      }
                    />
                  </label>
                  <label>
                    CFG
                    <input
                      type="number"
                      min={0}
                      max={30}
                      step={0.1}
                      value={image.cfg}
                      onChange={(e) =>
                        setImage({ ...image, cfg: Number(e.target.value) })
                      }
                    />
                  </label>
                  <label>
                    随机种子
                    <input
                      type="number"
                      min={0}
                      max={9007199254740991}
                      value={image.seed}
                      onChange={(e) =>
                        setImage({ ...image, seed: Number(e.target.value) })
                      }
                    />
                  </label>
                  <label>
                    默认尺寸
                    <select
                      value={image.default_size}
                      onChange={(e) =>
                        setImage({ ...image, default_size: e.target.value })
                      }
                    >
                      {[
                        "256x256",
                        "512x512",
                        "768x768",
                        "1024x1024",
                        "1536x1024",
                        "1024x1536",
                      ].map((s) => (
                        <option key={s}>{s}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </fieldset>
            )}
            <fieldset>
              <legend>运行额度</legend>
              <div className="form-grid">
                {(["cpu", "memory_mb", "vram_mb"] as const).map((key) => (
                  <label key={key}>
                    {
                      {
                        cpu: "CPU 核数",
                        memory_mb: "内存 MB",
                        vram_mb: "显存 MB",
                      }[key]
                    }
                    <input
                      type="number"
                      required
                      min={key === "cpu" ? 1 : key === "memory_mb" ? 128 : 0}
                      max={key === "cpu" ? 256 : 1048576}
                      step={key === "cpu" ? 0.1 : 1}
                      value={resources[key]}
                      onChange={(e) =>
                        setResources({
                          ...resources,
                          [key]: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                ))}
                <label>
                  任务超时 秒
                  <input
                    type="number"
                    min={1}
                    max={3600}
                    required
                    value={timeout}
                    onChange={(e) => setTimeout(Number(e.target.value))}
                  />
                </label>
                <label>
                  空闲卸载 秒
                  <input
                    type="number"
                    min={1}
                    max={86400}
                    required
                    value={idle}
                    onChange={(e) => setIdle(Number(e.target.value))}
                  />
                </label>
              </div>
            </fieldset>
            <div className="editor-actions">
              <label className="check-label">
                <input
                  type="checkbox"
                  disabled={!owner}
                  checked={isPublic}
                  onChange={(e) => setPublic(e.target.checked)}
                />
                向所有用户开放
              </label>
              <button type="button" onClick={() => setOpen(false)}>
                取消
              </button>
              <button className="primary" disabled={busy}>
                {busy ? "保存中…" : "保存模型设置"}
              </button>
            </div>
            <p className="field-hint">
              节点需预先安装对应模型。修改参数前须结束该模型的排队和运行任务；本地执行路径与模型文件仍由节点管理员批准。
            </p>
          </form>
        </section>
      )}
    </div>
  );
}
