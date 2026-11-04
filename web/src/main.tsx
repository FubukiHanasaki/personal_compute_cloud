import React, {
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  api,
  listAll,
  setCsrf,
  type User,
  type Job,
  type Model,
  type Template,
  type Node,
  type Service,
  type Artifact,
} from "./api";
import "./style.css";
import { NodeGallery } from "./NodeGallery";
import { Progress } from "./Visuals";
import { TemplateEditor } from "./TemplateEditor";
import { GrantEditor } from "./GrantEditor";
import { ThemePicker } from "./ThemePicker";
import { ArtifactPreview } from "./ArtifactPreview";
import { Icon } from "./Icons";
import { Overview } from "./Overview";
import { ChatWorkspace } from "./ChatWorkspace";
import { ImageStudio } from "./ImageStudio";

type View =
  | "overview"
  | "catalog"
  | "jobs"
  | "chat"
  | "images"
  | "nodes"
  | "services"
  | "access"
  | "audit";
const labels: Record<string, string> = {
  queued: "排队中",
  claimed: "已领取",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  reconciling: "待核对",
  online: "在线",
  stale: "心跳过期",
  offline: "离线",
  disabled: "已禁用",
  stopped: "已停止",
  starting: "启动中",
  ready: "就绪",
  idle: "空闲",
  stopping: "停止中",
  error: "错误",
  shared: "共享",
  private: "私人",
  reserved: "预留",
  maintenance: "维护",
};
const titles: Record<View, [string, string]> = {
  overview: ["工作空间", ""],
  catalog: ["模型管理", ""],
  jobs: ["计算任务", "提交计算，追踪每一次执行。"],
  chat: ["模型对话", "连接你的模型，让计算回到自己手中。"],
  images: ["图像创作", "异步生成，结果持久保存。"],
  nodes: ["计算节点", "按能力接入，按本地策略共享。"],
  services: ["服务运行", "按需启动，空闲释放资源。"],
  access: ["访问与授权", "凭证、用户和已批准的计算能力。"],
  audit: ["操作审计", "每一次重要变更都有迹可循。"],
};
const stamp = (value: number) =>
  new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
const pretty = (data: unknown) => JSON.stringify(data, null, 2);
function Badge({ value }: { value: string }) {
  return <span className={`badge ${value}`}>{labels[value] || value}</span>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon">◇</span>
      {children}
    </div>
  );
}
function Logo() {
  return (
    <div className="logo">
      <span className="logo-mark">◈</span>
      <div>
        PCC<small>个人计算云</small>
      </div>
    </div>
  );
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api<{ user: User; csrf_token: string }>(
        "/api/v1/auth/login",
        "POST",
        { username, password },
      );
      setCsrf(data.csrf_token);
      onLogin(data.user);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-shell">
      <section className="login-story">
        <Logo />
        <div className="story-body">
          <h1>
            你的设备。
            <br />
            一个工作空间。
          </h1>
          <div className="cloud-art" aria-hidden="true">
            <i />
            <i />
            <i />
            <b>◈</b>
          </div>
        </div>
        <small>Personal Compute Cloud · FubukiHanasaki</small>
      </section>
      <main className="login-main">
        <form onSubmit={submit}>
          <h2>登录工作空间</h2>
          <label>
            用户名
            <input
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            密码
            <input
              type="password"
              required
              maxLength={256}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && (
            <p role="alert" className="notice error-notice">
              {error}
            </p>
          )}
          <button className="primary wide" disabled={busy}>
            {busy ? "正在登录…" : "进入控制台 →"}
          </button>
        </form>
      </main>
    </div>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("overview");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [policyUsers, setPolicyUsers] = useState<User[]>([]);
  const [sampledAt, setSampledAt] = useState<Date | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [jobOffset, setJobOffset] = useState(0);
  const [imageJobs, setImageJobs] = useState<Job[]>([]);
  const [imageOffset, setImageOffset] = useState(0);
  const currentUser = useRef(user);
  currentUser.current = user;
  const closeDetails = useCallback(() => setSelected(null), []);
  const admin = user?.role === "owner" || user?.role === "admin";
  useEffect(() => {
    api<{ user: User; csrf_token: string }>("/api/v1/auth/me")
      .then((d) => {
        setCsrf(d.csrf_token);
        setUser(d.user);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  const refresh = useCallback(async () => {
    if (!user) return;
    try {
      const [j, m, t, images] = await Promise.all([
        api<{ items: Job[] }>(`/api/v1/jobs?limit=30&offset=${jobOffset}`),
        listAll<Model>("/api/v1/models", "data"),
        listAll<Template>("/api/v1/templates"),
        api<{ items: Job[] }>(
          `/api/v1/jobs?task_type=ai.image&limit=30&offset=${imageOffset}`,
        ),
      ]);
      if (currentUser.current !== user) return;
      setJobs(j.items);
      setModels(m);
      setTemplates(t);
      setImageJobs(images.items);
      if (admin) {
        const [n, s, u] = await Promise.all([
          listAll<Node>("/api/v1/nodes"),
          listAll<Service>("/api/v1/admin/services"),
          user.role === "owner"
            ? listAll<User>("/api/v1/admin/users")
            : Promise.resolve([]),
        ]);
        if (currentUser.current !== user) return;
        setNodes(n);
        setServices(s);
        setPolicyUsers(u);
      }
      if (currentUser.current === user) setSampledAt(new Date());
    } catch (e) {
      if (currentUser.current === user) setError(message(e));
    }
  }, [user, admin, jobOffset, imageOffset]);
  useEffect(() => {
    if (!user) return;
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => clearInterval(timer);
  }, [refresh, user]);
  async function act(task: () => Promise<unknown>, success = "操作已保存") {
    setError("");
    setNotice("");
    try {
      await task();
      setNotice(success);
      await refresh();
    } catch (e) {
      setError(message(e));
    }
  }
  if (loading)
    return (
      <div className="boot">
        <Logo />
        <p>正在连接工作空间…</p>
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={(next) => {
          setJobs([]);
          setImageJobs([]);
          setNodes([]);
          setPolicyUsers([]);
          setSampledAt(null);
          setServices([]);
          setTemplates([]);
          setModels([]);
          setSelected(null);
          setJobOffset(0);
          setImageOffset(0);
          setError("");
          setNotice("");
          setView("overview");
          setUser(next);
        }}
      />
    );
  const nav: [View, string, string][] = [
    ["overview", "总览", "home"],
    ["chat", "模型对话", "chat"],
    ["images", "图像创作", "image"],
    ["jobs", "计算任务", "jobs"],
    ...(admin
      ? ([
          ["nodes", "计算节点", "nodes"],
          ["catalog", "模型管理", "models"],
          ["services", "服务运行", "settings"],
        ] as [View, string, string][])
      : []),
    ["access", "访问与授权", "lock"],
    ...(user.role === "owner"
      ? ([["audit", "操作审计", "jobs"]] as [View, string, string][])
      : []),
  ];
  return (
    <div className="app">
      <aside className="sidebar">
        <Logo />
        <div className="workspace-label">工作空间</div>
        <nav aria-label="主导航">
          {nav.map(([key, label, icon]) => (
            <button
              key={key}
              aria-current={view === key ? "page" : undefined}
              className={view === key ? "active" : ""}
              onClick={() => {
                setView(key);
                setError("");
                setNotice("");
              }}
            >
              <Icon name={String(icon)} />
              {label}
              {key === "jobs" && (
                <small>
                  {
                    jobs.filter((j) =>
                      ["queued", "running", "claimed"].includes(j.status),
                    ).length
                  }
                </small>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="account-avatar">
            {user.username[0]?.toUpperCase()}
          </div>
          <div>
            <strong title={user.username}>{user.username}</strong>
            <small>{user.role.toUpperCase()}</small>
          </div>
          <button
            title="退出登录"
            onClick={() =>
              void act(async () => {
                await api("/api/v1/auth/logout", "POST");
                setUser(null);
                setCsrf("");
              })
            }
          >
            ↗
          </button>
        </div>
      </aside>
      <div className="main-wrap">
        <header className="topbar">
          <span>
            个人计算云 <i>/</i> {titles[view][0]}
          </span>
          <span className="workspace-state">
            <b />{" "}
            {sampledAt
              ? `更新于 ${sampledAt.toLocaleTimeString("zh-CN", { hour12: false })}`
              : "正在刷新"}{" "}
            <button
              className="mobile-logout"
              onClick={() =>
                void act(async () => {
                  await api("/api/v1/auth/logout", "POST");
                  setUser(null);
                  setCsrf("");
                })
              }
            >
              退出
            </button>
          </span>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <h1>{titles[view][0]}</h1>
            </div>
            <button onClick={() => void refresh()} className="secondary">
              ↻ 刷新
            </button>
          </div>
          {error && (
            <div role="alert" className="notice error-notice">
              {error}
              <button aria-label="关闭错误" onClick={() => setError("")}>
                ×
              </button>
            </div>
          )}
          {notice && (
            <div role="status" className="notice">
              {notice}
              <button aria-label="关闭提示" onClick={() => setNotice("")}>
                ×
              </button>
            </div>
          )}
          {view === "overview" && (
            <Overview
              user={user}
              jobs={jobs}
              models={models}
              nodes={nodes}
              go={setView}
            />
          )}
          {view === "catalog" && admin && (
            <TemplateEditor
              templates={templates}
              act={act}
              owner={user.role === "owner"}
            />
          )}
          {view === "jobs" && (
            <>
              <div className="metrics">
                {[
                  ["本页任务", jobs.length, "◫"],
                  [
                    "正在进行",
                    jobs.filter((j) =>
                      ["queued", "claimed", "running"].includes(j.status),
                    ).length,
                    "↗",
                  ],
                  [
                    "已完成",
                    jobs.filter((j) => j.status === "completed").length,
                    "✓",
                  ],
                  [
                    "需要关注",
                    jobs.filter((j) =>
                      ["failed", "reconciling"].includes(j.status),
                    ).length,
                    "!",
                  ],
                ].map(([label, value, icon]) => (
                  <div className="metric" key={label}>
                    <div>
                      <small>{label}</small>
                      <strong>{value}</strong>
                    </div>
                    <Icon name={String(icon)} />
                  </div>
                ))}
              </div>
              <ComputeForm
                templates={templates}
                onSubmit={async (data) => {
                  await act(
                    () => api("/api/v1/jobs", "POST", data),
                    "任务已加入队列",
                  );
                }}
              />
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>任务历史</h2>
                  </div>
                  <span>{jobs.length} 项</span>
                </div>
                <JobTable jobs={jobs} select={setSelected} />
                <div className="pagination">
                  <button
                    disabled={!jobOffset}
                    onClick={() => setJobOffset(Math.max(0, jobOffset - 30))}
                  >
                    上一页
                  </button>
                  <span>
                    {jobs.length ? jobOffset + 1 : 0}–{jobOffset + jobs.length}
                  </span>
                  <button
                    disabled={jobs.length < 30}
                    onClick={() => setJobOffset(jobOffset + 30)}
                  >
                    下一页
                  </button>
                </div>
              </section>
            </>
          )}
          {view === "chat" && <ChatWorkspace models={models} />}
          {view === "images" && (
            <ImageStudio
              models={models}
              jobs={imageJobs}
              offset={imageOffset}
              setOffset={setImageOffset}
              submit={async (data) => {
                await act(
                  () => api("/api/v1/images/generations", "POST", data),
                  "图片任务已排队",
                );
              }}
              select={setSelected}
            />
          )}
          {view === "nodes" && (
            <>
              <div className="node-summary">
                <div>
                  <span className="eyebrow">COMPUTE NETWORK</span>
                  <h2>
                    {nodes.filter((n) => n.status === "online").length}
                    <small> / {nodes.length} 个节点在线</small>
                  </h2>
                </div>
                <div>
                  <strong>
                    {nodes.reduce(
                      (sum, n) => sum + (n.capabilities.allocatable?.cpu || 0),
                      0,
                    )}{" "}
                    核
                  </strong>
                  <span>总分配 CPU</span>
                </div>
                <div>
                  <strong>
                    {(
                      nodes.reduce(
                        (sum, n) =>
                          sum + (n.capabilities.allocatable?.memory_mb || 0),
                        0,
                      ) / 1024
                    ).toFixed(1)}{" "}
                    GB
                  </strong>
                  <span>总分配内存</span>
                </div>
              </div>
              <Enrollment act={act} />
              <section className="panel node-catalog-panel">
                <div className="panel-heading">
                  <h2>节点与资源</h2>
                  <span>{nodes.length} 个已授权节点</span>
                </div>
                {!nodes.length ? (
                  <Empty>
                    暂无计算节点。签发一次性令牌，在节点上完成注册。
                  </Empty>
                ) : (
                  <NodeGallery nodes={nodes} users={policyUsers} act={act} />
                )}
              </section>
            </>
          )}
          {view === "services" && (
            <section className="panel">
              <div className="panel-heading">
                <h2>受管模型服务</h2>
                <span>活动请求期间保留模型</span>
              </div>
              {!services.length ? (
                <Empty>
                  暂无服务实例。批准 AI
                  模板后，启用对应适配器的节点将在心跳时关联服务。
                </Empty>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>服务 / 节点</th>
                        <th>状态</th>
                        <th>活动请求</th>
                        <th>期望状态</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {services.map((s) => (
                        <tr key={s.id}>
                          <td>
                            <strong>{s.definition_id}</strong>
                            <small>
                              {nodes.find((n) => n.id === s.node_id)?.name ||
                                s.node_id.slice(0, 8)}
                            </small>
                            {s.error && (
                              <small className="danger-text">{s.error}</small>
                            )}
                          </td>
                          <td>
                            <Badge value={s.state} />
                          </td>
                          <td className="mono">{s.active_requests}</td>
                          <td>
                            {{
                              ready: "保持加载",
                              stopped: "保持停止",
                              auto: "按需",
                            }[s.desired_state] || s.desired_state}
                          </td>
                          <td>
                            <button
                              onClick={() =>
                                void act(
                                  () =>
                                    api(
                                      `/api/v1/admin/services/${s.id}`,
                                      "PUT",
                                      { desired_state: "ready" },
                                    ),
                                  "已请求启动",
                                )
                              }
                            >
                              启动
                            </button>{" "}
                            <button
                              onClick={() =>
                                void act(
                                  () =>
                                    api(
                                      `/api/v1/admin/services/${s.id}`,
                                      "PUT",
                                      { desired_state: "stopped" },
                                    ),
                                  "已请求排空并停止",
                                )
                              }
                            >
                              停止
                            </button>{" "}
                            <button
                              onClick={() =>
                                void act(() =>
                                  api(`/api/v1/admin/services/${s.id}`, "PUT", {
                                    desired_state: "auto",
                                  }),
                                )
                              }
                            >
                              自动
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
          {view === "access" && (
            <Access user={user} templates={templates} nodes={nodes} act={act} />
          )}
          {view === "audit" && <Audit />}
        </main>
        <footer>
          <span>Personal Compute Cloud · FubukiHanasaki</span>
          <ThemePicker />
        </footer>
      </div>
      {selected && (
        <JobDetails id={selected} close={closeDetails} changed={refresh} />
      )}
    </div>
  );
}

function JobTable({
  jobs,
  select,
}: {
  jobs: Job[];
  select: (id: string) => void;
}) {
  return !jobs.length ? (
    <Empty>还没有任务。选择已批准的模板，提交第一个计算请求。</Empty>
  ) : (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务</th>
            <th>类型</th>
            <th>状态</th>
            <th>进度</th>
            <th>创建时间</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.job_id}>
              <td>
                <strong>{j.template_id}</strong>
                <small className="mono">{j.job_id.slice(0, 12)}</small>
              </td>
              <td>
                {{
                  "compute.stats": "数字统计",
                  "ai.chat": "对话",
                  "ai.image": "图片",
                }[j.task_type] || j.task_type}
              </td>
              <td>
                <Badge value={j.status} />
              </td>
              <td>
                <Progress value={j.progress} />
              </td>
              <td className="nowrap muted">{stamp(j.created_at)}</td>
              <td>
                <button
                  className="text-button"
                  onClick={() => select(j.job_id)}
                >
                  查看 →
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function ComputeForm({
  templates,
  onSubmit,
}: {
  templates: Template[];
  onSubmit: (body: unknown) => Promise<void>;
}) {
  const compute = templates.filter((t) => t.kind === "compute.stats");
  const [id, setId] = useState("");
  const [values, setValues] = useState("1, 2, 3, 4, 5");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="panel quick-submit"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          if (!values.trim()) throw new Error("请输入至少一个数字");
          const numbers = values
            .trim()
            .split(/[\s,，]+/)
            .map(Number);
          if (!numbers.length || numbers.some((v) => !Number.isFinite(v)))
            throw new Error("请输入有限数字，以逗号或空格分隔");
          await onSubmit({
            task_type: "compute.stats",
            template_id: id || compute[0]?.id,
            input: { values: numbers },
          });
        } catch (e) {
          setError(message(e));
        } finally {
          setBusy(false);
        }
      }}
    >
      <div>
        <h2>发起计算</h2>
      </div>
      <label>
        任务模板
        <select
          value={id || compute[0]?.id || ""}
          onChange={(e) => setId(e.target.value)}
        >
          <option value="" disabled>
            暂无可用模板
          </option>
          {compute.map((t) => (
            <option value={t.id} key={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label className="grow">
        输入数字
        <input
          value={values}
          onChange={(e) => setValues(e.target.value)}
          required
          placeholder="例如：1, 2, 3"
        />
      </label>
      <button className="primary" disabled={busy || !compute.length}>
        {busy ? "提交中…" : "+ 提交任务"}
      </button>
      {error && <p className="danger-text">{error}</p>}
    </form>
  );
}
function JobDetails({
  id,
  close,
  changed,
}: {
  id: string;
  close: () => void;
  changed: () => Promise<void>;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<
    { message: string; timestamp: number }[]
  >([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const dialog = useRef<HTMLElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const [j, e, a] = await Promise.all([
          api<Job>(`/api/v1/jobs/${id}`),
          api<{ items: typeof events }>(`/api/v1/jobs/${id}/events`),
          api<{ items: Artifact[] }>(`/api/v1/jobs/${id}/artifacts`),
        ]);
        if (live) {
          setJob(j);
          setEvents(e.items);
          setArtifacts(a.items);
        }
      } catch (e) {
        if (live) setError(message(e));
      }
    }
    void load();
    const interval = setInterval(() => void load(), 2000);
    return () => {
      live = false;
      clearInterval(interval);
    };
  }, [id]);
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const listener = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {
        const controls = Array.from(
          dialog.current?.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]',
          ) || [],
        ).filter((element) => element.getClientRects().length);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            !dialog.current?.contains(document.activeElement))
        ) {
          e.preventDefault();
          last?.focus();
        } else if (
          !e.shiftKey &&
          (document.activeElement === last ||
            !dialog.current?.contains(document.activeElement))
        ) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", listener);
    return () => {
      document.removeEventListener("keydown", listener);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [close]);
  return (
    <div className="modal-shade" onClick={close}>
      <section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="job-title"
        className="modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-heading">
          <div>
            <h2 id="job-title">任务详情</h2>
            <small className="mono">{id}</small>
          </div>
          <button aria-label="关闭" onClick={close}>
            ×
          </button>
        </div>
        {error && (
          <p role="alert" className="notice error-notice">
            {error}
          </p>
        )}
        {job && (
          <>
            <div className="detail-status">
              <Badge value={job.status} />
              <span>{job.template_id}</span>
              {!["completed", "failed", "cancelled"].includes(job.status) && (
                <button
                  className="danger"
                  onClick={() =>
                    void api(`/api/v1/jobs/${id}/cancel`, "POST")
                      .then(() => changed())
                      .catch((e) => setError(message(e)))
                  }
                >
                  请求取消
                </button>
              )}
            </div>
            {job.queue_reason && <p className="notice">{job.queue_reason}</p>}
            {job.error && <p className="notice error-notice">{job.error}</p>}
            <Progress value={job.progress} />
            <h3>结果</h3>
            <pre>{job.result ? pretty(job.result) : "等待执行结果…"}</pre>
            <h3>工件</h3>
            {artifacts.length ? (
              artifacts.map((a) => (
                <div className="artifact" key={a.id}>
                  <span>↧</span>
                  <div>
                    <ArtifactPreview artifact={a} />
                    <a href={a.download_url}>{a.name}</a>
                    <small>
                      {(a.size / 1024).toFixed(1)} KB · 保留至{" "}
                      {stamp(a.expires_at)}
                    </small>
                    <code>SHA256 {a.sha256}</code>
                  </div>
                </div>
              ))
            ) : (
              <p className="muted">尚无工件</p>
            )}
            <h3>执行日志</h3>
            <div className="log">
              {events.length
                ? events.map((e, i) => (
                    <p key={i}>
                      <time>{stamp(e.timestamp)}</time> {e.message}
                    </p>
                  ))
                : "等待节点日志…"}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
function Enrollment({
  act,
}: {
  act: (f: () => Promise<unknown>, success?: string) => Promise<void>;
}) {
  const [raw, setRaw] = useState("");
  return (
    <section className="panel enroll">
      <div>
        <h2>接入一个新节点</h2>
        <p>
          安装 Agent，填写服务器地址，再使用一次性令牌绑定。令牌十分钟有效。
        </p>
      </div>
      <button
        className="primary"
        onClick={() =>
          void act(async () => {
            const d = await api<{ token: string }>(
              "/api/v1/admin/registration-tokens",
              "POST",
              {},
            );
            setRaw(d.token);
          }, "一次性令牌已签发")
        }
      >
        签发注册令牌 +
      </button>
      {raw && (
        <div className="secret-display">
          <strong>仅显示一次，请在节点终端中输入</strong>
          <code>{raw}</code>
          <button onClick={() => setRaw("")}>隐藏</button>
        </div>
      )}
    </section>
  );
}
function Access({
  user,
  templates,
  nodes,
  act,
}: {
  user: User;
  nodes: Node[];
  templates: Template[];
  act: (f: () => Promise<unknown>, success?: string) => Promise<void>;
}) {
  const [keys, setKeys] = useState<
    {
      id: string;
      scopes: string[];
      expires_at: number;
      revoked_at: number | null;
    }[]
  >([]);
  const [raw, setRaw] = useState("");
  const [hours, setHours] = useState(24);
  const [scopes, setScopes] = useState([
    "jobs:read",
    "jobs:write",
    "models:read",
  ]);
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [grantId, setGrantId] = useState("");
  const [tab, setTab] = useState("keys");
  const load = useCallback(async () => {
    setKeys(await listAll<(typeof keys)[number]>("/api/v1/keys"));
    if (user.role === "owner")
      setUsers(await listAll<User>("/api/v1/admin/users"));
  }, [user.role]);
  useEffect(() => {
    void act(load);
  }, [load]);
  return (
    <>
      <div className="tabs" role="group" aria-label="访问设置">
        {[
          ["keys", "API 密钥"],
          ...(user.role === "owner" ? [["users", "用户授权"]] : []),
          ["password", "账号安全"],
        ].map(([key, label]) => (
          <button
            key={key}
            aria-pressed={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "keys" && (
        <section className="panel pad">
          <h2>API Key</h2>
          <p>新密钥只显示一次。为不同应用选择最少作用域，默认一天有效。</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const d = await api<{ key: string }>("/api/v1/keys", "POST", {
                  scopes,
                  expires_seconds: hours * 3600,
                });
                setRaw(d.key);
                await load();
              }, "密钥已创建");
            }}
          >
            <div className="checkbox-row">
              {[
                "jobs:read",
                "jobs:write",
                "models:read",
                "nodes:read",
                ...(user.role === "owner" || user.role === "admin"
                  ? ["admin"]
                  : []),
              ].map((scope) => (
                <label key={scope}>
                  <input
                    type="checkbox"
                    checked={scopes.includes(scope)}
                    onChange={(e) =>
                      setScopes(
                        e.target.checked
                          ? [...scopes, scope]
                          : scopes.filter((s) => s !== scope),
                      )
                    }
                  />
                  {scope}
                </label>
              ))}
            </div>
            <label style={{ maxWidth: 180, marginBottom: 16 }}>
              有效时长（小时）
              <input
                type="number"
                min={1}
                max={2160}
                required
                value={hours}
                onChange={(e) => setHours(Number(e.target.value))}
              />
            </label>
            <button className="primary" disabled={!scopes.length}>
              创建密钥 +
            </button>
          </form>
          {raw && (
            <div className="secret-display">
              <code>{raw}</code>
              <button onClick={() => setRaw("")}>隐藏密钥</button>
            </div>
          )}
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>凭证</th>
                  <th>作用域</th>
                  <th>到期</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id}>
                    <td className="mono">{k.id.slice(0, 10)}</td>
                    <td>{k.scopes.join(", ")}</td>
                    <td>{stamp(k.expires_at)}</td>
                    <td>
                      {k.revoked_at ? (
                        "已撤销"
                      ) : (
                        <button
                          onClick={() =>
                            void act(async () => {
                              await api(`/api/v1/keys/${k.id}/revoke`, "POST");
                              await load();
                            }, "密钥已撤销")
                          }
                        >
                          撤销
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {user.role === "owner" && (
        <>
          {tab === "users" && (
            <section className="panel pad">
              <h2>用户管理</h2>
              <form
                className="form-row"
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    await api("/api/v1/admin/users", "POST", {
                      username,
                      password,
                      role,
                    });
                    setPassword("");
                    setUsername("");
                    await load();
                  }, "用户已创建");
                }}
              >
                <label>
                  用户名
                  <input
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </label>
                <label>
                  初始密码
                  <input
                    type="password"
                    required
                    minLength={16}
                    maxLength={256}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </label>
                <label>
                  角色
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                  >
                    <option value="user">普通用户</option>
                    <option value="admin">管理员</option>
                  </select>
                </label>
                <button className="primary">创建用户</button>
              </form>
              <div className="user-list">
                {users.map((u) => (
                  <div key={u.id}>
                    <strong>{u.username}</strong>
                    <span>
                      {
                        { owner: "所有者", admin: "管理员", user: "普通用户" }[
                          u.role
                        ]
                      }{" "}
                      · {u.status === "active" ? "启用" : "禁用"}
                    </span>
                    {u.role !== "owner" && (
                      <>
                        <button
                          onClick={() => {
                            setGrantId(u.id);
                          }}
                        >
                          编辑授权
                        </button>
                        <button
                          className="danger-text"
                          onClick={() =>
                            void act(async () => {
                              if (
                                !confirm(
                                  `${u.status === "active" ? "禁用" : "启用"}用户 ${u.username}？${u.status === "active" ? "现有会话和密钥将失效。" : ""}`,
                                )
                              )
                                return;
                              await api(`/api/v1/admin/users/${u.id}`, "PUT", {
                                status:
                                  u.status === "active" ? "disabled" : "active",
                                grants: u.grants,
                              });
                              await load();
                            })
                          }
                        >
                          {u.status === "active" ? "禁用" : "启用"}
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
              {grantId && users.find((u) => u.id === grantId) && (
                <GrantEditor
                  key={grantId}
                  user={users.find((u) => u.id === grantId)!}
                  templates={templates}
                  nodes={nodes}
                  save={async (f, success) => {
                    await act(async () => {
                      await f();
                      await load();
                    }, success);
                  }}
                  cancel={() => setGrantId("")}
                />
              )}
            </section>
          )}
        </>
      )}
      {tab === "password" && <PasswordChange act={act} />}
    </>
  );
}
function PasswordChange({
  act,
}: {
  act: (f: () => Promise<unknown>, success?: string) => Promise<void>;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  return (
    <section className="panel pad">
      <h2>修改密码</h2>
      <p>修改后所有网页登录会话失效，需要重新登录。</p>
      <form
        className="form-row"
        onSubmit={(e) => {
          e.preventDefault();
          void act(async () => {
            await api("/api/v1/auth/password", "POST", {
              current_password: current,
              new_password: next,
            });
            location.reload();
          });
        }}
      >
        <label>
          当前密码
          <input
            type="password"
            required
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          新密码
          <input
            type="password"
            required
            minLength={16}
            maxLength={256}
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <button>更新密码</button>
      </form>
    </section>
  );
}
function Audit() {
  const [rows, setRows] = useState<
    {
      id: string;
      actor: string;
      action: string;
      target: string;
      timestamp: number;
      outcome: string;
    }[]
  >([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ items: typeof rows }>(`/api/v1/admin/audit?limit=50&offset=${offset}`)
      .then((d) => setRows(d.items))
      .catch((e) => setError(message(e)));
  }, [offset]);
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>审计事件</h2>
        <button
          onClick={() => setOffset(Math.max(0, offset - 50))}
          disabled={!offset}
        >
          较新
        </button>
        <button
          disabled={rows.length < 50}
          onClick={() => setOffset(offset + 50)}
        >
          更早
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>操作</th>
              <th>操作者</th>
              <th>目标</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{stamp(r.timestamp)}</td>
                <td className="mono">{r.action}</td>
                <td className="mono">{r.actor.slice(0, 12)}</td>
                <td className="mono">{r.target.slice(0, 12)}</td>
                <td>{r.outcome}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
