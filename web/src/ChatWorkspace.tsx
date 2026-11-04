// Author: FubukiHanasaki
import { useCallback, useEffect, useRef, useState } from "react";
import { api, listAll, type Model, type Parameters } from "./api";
import { Icon } from "./Icons";
import { MediaImage, readMedia } from "./Media";
import { Progress } from "./Visuals";
type Content =
  | string
  | (
      | { type: "text"; text: string }
      | { type: "image_url"; image_url: { url: string } }
    )[];
type Summary = { id: string; title: string; model: string; updated_at: number };
type Turn = {
  id: string;
  content: Content;
  job_id: string;
  status: string;
  answer: string;
  error: string | null;
  progress: number;
};
type Conversation = Summary & { turns: Turn[] };
const terminal = ["completed", "failed", "cancelled"];
function MessageContent({ content }: { content: Content }) {
  return typeof content === "string" ? (
    <p>{content}</p>
  ) : (
    <>
      {content.map((p, i) =>
        p.type === "text" ? (
          <p key={i}>{p.text}</p>
        ) : (
          <MediaImage key={i} src={p.image_url.url} />
        ),
      )}
    </>
  );
}
export function ChatWorkspace({ models }: { models: Model[] }) {
  const available = models.filter((m) => m.task_type === "ai.chat");
  const [list, setList] = useState<Summary[]>([]),
    [id, setId] = useState(""),
    [conversation, setConversation] = useState<Conversation | null>(null);
  const [model, setModel] = useState(""),
    [prompt, setPrompt] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [loadingMedia, setLoadingMedia] = useState(false);
  const [attachments, setAttachments] = useState<
      { url: string; name: string }[]
    >([]),
    [overrides, setOverrides] = useState<Partial<Parameters>>({});
  const request = useRef<{ id: string; body: string } | null>(null),
    selected = useRef(id),
    scroll = useRef<HTMLDivElement>(null),
    file = useRef<HTMLInputElement>(null);
  selected.current = id;
  const chosen =
    available.find((m) => m.id === (conversation?.model || model)) ||
    (!id ? available[0] : undefined);
  const defaults = chosen?.parameters || {};
  const running = conversation?.turns.some((t) => !terminal.includes(t.status));
  const refreshList = useCallback(async () => {
    setList(await listAll<Summary>("/api/v1/conversations"));
  }, []);
  useEffect(() => {
    let live = true;
    listAll<Summary>("/api/v1/conversations")
      .then((rows) => {
        if (live) {
          setList(rows);
          if (rows.length) setId(rows[0].id);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    setConversation(null);
    setError("");
    if (!id) return;
    let live = true;
    const load = () =>
      api<Conversation>(`/api/v1/conversations/${id}`)
        .then((data) => {
          if (live) setConversation(data);
        })
        .catch((e) => {
          if (live) setError(e.message);
        });
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [id]);
  useEffect(() => {
    scroll.current?.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: "smooth",
    });
  }, [conversation?.turns.length, conversation?.turns.at(-1)?.status]);
  async function send() {
    if (
      busy ||
      running ||
      loadingMedia ||
      (!prompt.trim() && !attachments.length) ||
      !chosen
    )
      return;
    setBusy(true);
    setError("");
    try {
      let identity = id;
      if (!identity) {
        const created = await api<Summary>("/api/v1/conversations", "POST", {
          model: chosen.id,
        });
        identity = created.id;
        selected.current = identity;
        setId(identity);
      }
      const content: Content = attachments.length
        ? [
            {
              type: "text",
              text:
                prompt +
                (attachments.length > 0
                  ? "\n附件按顺序排列；视频帧的时间信息：" +
                    attachments.map((a) => a.name).join("，")
                  : ""),
            },
            ...attachments.map((a) => ({
              type: "image_url" as const,
              image_url: { url: a.url },
            })),
          ]
        : prompt;
      const body = JSON.stringify({ content, parameters: overrides });
      if (!request.current || request.current.body !== body)
        request.current = { id: crypto.randomUUID(), body };
      await api(`/api/v1/conversations/${identity}/messages`, "POST", {
        content,
        parameters: overrides,
        request_id: request.current.id,
      });
      request.current = null;
      setPrompt("");
      setAttachments([]);
      if (selected.current === identity)
        setConversation(
          await api<Conversation>(`/api/v1/conversations/${identity}`),
        );
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  const choose = (value: string) => {
    setId(value);
    setPrompt("");
    setAttachments([]);
    setOverrides({});
    setError("");
  };
  return (
    <div className="conversation-workspace">
      <aside className="conversation-sidebar">
        <button
          className="primary wide"
          disabled={busy}
          onClick={() => {
            choose("");
            setConversation(null);
          }}
        >
          <Icon name="plus" size={18} />
          新建对话
        </button>
        <span className="eyebrow">最近对话</span>
        <div className="conversation-list">
          {list.map((c) => (
            <button
              className={id === c.id ? "selected" : ""}
              disabled={busy}
              key={c.id}
              onClick={() => choose(c.id)}
            >
              <Icon name="chat" size={17} />
              <span>
                {c.title}
                <small>{c.model}</small>
              </span>
            </button>
          ))}
          {!list.length && <p className="small muted">开始对话后自动保存</p>}
        </div>
      </aside>
      <section className="conversation-main">
        <div className="conversation-toolbar">
          <label>
            <span className="sr-only">Chat 模型</span>
            <select
              aria-label="Chat 模型"
              disabled={!!id || busy}
              value={conversation?.model || chosen?.id || ""}
              onChange={(e) => {
                setModel(e.target.value);
                setOverrides({});
              }}
            >
              {conversation && !chosen && (
                <option value={conversation.model}>
                  {conversation.model} · 未授权
                </option>
              )}
              {!available.length && !conversation && (
                <option value="">暂无授权模型</option>
              )}
              {available.map((m) => (
                <option value={m.id} key={m.id}>
                  {m.name || m.id}
                  {m.online ? "" : " · 节点离线"}
                </option>
              ))}
            </select>
          </label>
          <span className="model-capability">
            {defaults.vision ? "视觉 · 文字" : "文字"}
          </span>
          <div className="toolbar-spacer" />
          {conversation && (
            <>
              <button
                className="icon-button"
                title="重命名对话"
                aria-label="重命名对话"
                disabled={busy}
                onClick={async () => {
                  const title = window.prompt("对话名称", conversation.title);
                  if (title?.trim())
                    try {
                      await api(`/api/v1/conversations/${id}`, "PATCH", {
                        title: title.trim(),
                      });
                      await refreshList();
                      setConversation({ ...conversation, title: title.trim() });
                    } catch (e) {
                      setError(String(e));
                    }
                }}
              >
                重命名
              </button>
              <button
                className="icon-button danger-text"
                aria-label="删除对话"
                disabled={busy || running}
                onClick={async () => {
                  if (!confirm("删除此对话？关联任务仍按原保留策略保存。"))
                    return;
                  try {
                    await api(`/api/v1/conversations/${id}`, "DELETE");
                    choose("");
                    setConversation(null);
                    await refreshList();
                  } catch (e) {
                    setError(String(e));
                  }
                }}
              >
                删除
              </button>
            </>
          )}
        </div>
        <div ref={scroll} className="conversation-scroll" aria-live="polite">
          {!conversation?.turns.length ? (
            <div className="chat-welcome">
              <span className="welcome-symbol">
                <Icon name="chat" size={32} />
              </span>
              <h2>让想法，开始生长。</h2>
              <p>
                {defaults.vision
                  ? "发送文字、图片，或上传视频提取采样帧。"
                  : "选择一个模型，开始你的第一句话。"}
              </p>
              <div className="prompt-suggestions">
                {["帮我梳理一个想法", "解释这段代码", "一起构思一个故事"].map(
                  (text) => (
                    <button key={text} onClick={() => setPrompt(text)}>
                      {text}
                      <Icon name="arrow" size={15} />
                    </button>
                  ),
                )}
              </div>
            </div>
          ) : (
            conversation.turns.map((turn) => (
              <div className="chat-turn" key={turn.id}>
                <div className="message-row user">
                  <span className="message-avatar">你</span>
                  <div className="message-bubble">
                    <MessageContent content={turn.content} />
                  </div>
                </div>
                <div className="message-row assistant">
                  <span className="message-avatar">
                    <Icon name="models" size={18} />
                  </span>
                  <div className="message-bubble">
                    {turn.answer ? (
                      <p>{turn.answer}</p>
                    ) : terminal.includes(turn.status) ? (
                      <p className="danger-text">
                        {turn.error || "此次任务没有返回回复"}
                      </p>
                    ) : (
                      <div className="inference-pending">
                        <span className="thinking-dots">● ● ●</span>
                        <span>
                          {turn.status === "queued"
                            ? "等待可用算力"
                            : "正在推理"}
                        </span>
                        <Progress value={turn.progress} />
                        <button
                          className="text-button"
                          onClick={() =>
                            void api(
                              `/api/v1/jobs/${turn.job_id}/cancel`,
                              "POST",
                            ).catch((e) => setError(e.message))
                          }
                        >
                          停止
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
        {error && (
          <p role="alert" className="notice error-notice">
            {error}
          </p>
        )}
        <div className="composer-area">
          <details className="chat-parameters">
            <summary>
              推理设置{" "}
              <span>
                {defaults.context_length || 8192} 上下文 ·{" "}
                {defaults.kv_cache_type || "f16"} KV
              </span>
            </summary>
            <div className="form-grid">
              <label>
                温度
                <input
                  type="number"
                  min={0}
                  max={2}
                  step={0.05}
                  value={overrides.temperature ?? defaults.temperature ?? 0.7}
                  onChange={(e) =>
                    setOverrides({
                      ...overrides,
                      temperature: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                Top P
                <input
                  type="number"
                  min={0.01}
                  max={1}
                  step={0.01}
                  value={overrides.top_p ?? defaults.top_p ?? 0.8}
                  onChange={(e) =>
                    setOverrides({
                      ...overrides,
                      top_p: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label>
                输出 Token 上限
                <input
                  type="number"
                  min={1}
                  max={defaults.max_tokens || 2048}
                  value={overrides.max_tokens ?? defaults.max_tokens ?? 2048}
                  onChange={(e) =>
                    setOverrides({
                      ...overrides,
                      max_tokens: Number(e.target.value),
                    })
                  }
                />
              </label>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={overrides.thinking ?? defaults.thinking ?? false}
                  onChange={(e) =>
                    setOverrides({ ...overrides, thinking: e.target.checked })
                  }
                />
                思考模式
              </label>
            </div>
            <button className="text-button" onClick={() => setOverrides({})}>
              恢复管理员默认值
            </button>
          </details>
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <div className="attachment-strip">
              {attachments.map((a, i) => (
                <div key={i}>
                  <MediaImage src={a.url} alt={a.name} />
                  <button
                    type="button"
                    aria-label={`移除 ${a.name}`}
                    onClick={() =>
                      setAttachments(attachments.filter((_, n) => n !== i))
                    }
                  >
                    ×
                  </button>
                  <small>{a.name}</small>
                </div>
              ))}
            </div>
            <textarea
              aria-label="消息"
              placeholder="输入消息…"
              maxLength={16000}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing &&
                  e.keyCode !== 229
                ) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <div className="composer-actions">
              <input
                ref={file}
                className="sr-only"
                type="file"
                accept="image/png,image/jpeg,image/webp,video/*"
                aria-label="选择图片或视频"
                onChange={async (e) => {
                  const input = e.currentTarget;
                  const f = input.files?.[0];
                  if (!f) return;
                  setLoadingMedia(true);
                  setError("");
                  try {
                    const media = await readMedia(f);
                    if (attachments.length + media.length > 8)
                      throw new Error("最多 8 张图片或视频采样帧");
                    setAttachments([...attachments, ...media]);
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setLoadingMedia(false);
                    input.value = "";
                  }
                }}
              />
              <button
                type="button"
                className="text-button"
                disabled={!defaults.vision || loadingMedia || busy || running}
                onClick={() => file.current?.click()}
              >
                <Icon name="upload" size={18} />
                {loadingMedia ? "正在处理…" : "添加附件"}
              </button>
              <span className="composer-hint">
                Enter 发送 · Shift + Enter 换行
              </span>
              <button
                className="primary send-button"
                aria-label="发送消息"
                disabled={
                  busy ||
                  running ||
                  loadingMedia ||
                  !chosen ||
                  (!prompt.trim() && !attachments.length)
                }
              >
                <Icon name="arrow" size={19} />
              </button>
            </div>
          </form>
          <small className="chat-retention">
            对话自动保存到你的账户 · 视觉上下文最多 8 张图片或采样帧
          </small>
        </div>
      </section>
    </div>
  );
}
