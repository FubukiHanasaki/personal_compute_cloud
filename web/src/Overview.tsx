// Author: FubukiHanasaki
import type { Job, Model, Node, User } from "./api";
import { Icon } from "./Icons";
import { NodeResources, Progress } from "./Visuals";
export function Overview({
  user,
  jobs,
  models,
  nodes,
  go,
}: {
  user: User;
  jobs: Job[];
  models: Model[];
  nodes: Node[];
  go: (v: "chat" | "images" | "jobs" | "nodes" | "catalog") => void;
}) {
  const active = jobs.filter((j) =>
    ["running", "claimed", "queued", "cancel_requested"].includes(j.status),
  );
  return (
    <div className="overview-layout">
      <section className="welcome-card">
        <div>
          <span className="eyebrow">YOUR PERSONAL CLOUD</span>
          <h2>你好，{user.username}。</h2>
          <p>今天，想用算力创造什么？</p>
          <button className="primary" onClick={() => go("chat")}>
            开始对话 <Icon name="arrow" size={17} />
          </button>
        </div>
        <div className="orbit-art" aria-hidden="true">
          <span />
          <span />
          <span />
          <Icon name="models" size={64} />
        </div>
      </section>
      <div className="overview-stats">
        {[
          [
            "可用模型",
            models.filter((m) => m.online).length,
            `${models.length} 个已授权`,
            "models",
          ],
          ["本页进行中", active.length, "任务实时同步", "clock"],
          [
            "本页已完成",
            jobs.filter((j) => j.status === "completed").length,
            "结果保留在任务中",
            "check",
          ],
        ].map(([label, value, detail, icon]) => (
          <article className="stat-card" key={label}>
            <Icon name={String(icon)} />
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{detail}</small>
          </article>
        ))}
      </div>
      <div className="section-title">
        <h2>创作空间</h2>
      </div>
      <div className="launch-grid">
        {[
          ["chat", "chat", "模型对话", "文字、图片与视频采样帧"],
          ["images", "image", "图像创作", "从画面描述到图像编辑"],
          ["jobs", "jobs", "计算任务", "提交、追踪与查看结果"],
        ].map(([view, icon, title, description]) => (
          <button
            className={`launch-card ${view}`}
            key={view}
            onClick={() => go(view as "chat")}
          >
            <span className="launch-icon">
              <Icon name={icon} size={26} />
            </span>
            <strong>{title}</strong>
            <small>{description}</small>
            <Icon name="arrow" size={18} />
          </button>
        ))}
      </div>
      {nodes.length > 0 && (
        <>
          <div className="section-title">
            <h2>我的算力</h2>
            <button className="text-button" onClick={() => go("nodes")}>
              管理节点 <Icon name="arrow" size={16} />
            </button>
          </div>
          <div className="overview-nodes">
            {nodes.slice(0, 2).map((n) => (
              <article className="panel node-preview" key={n.id}>
                <div className="node-preview-title">
                  <Icon name="nodes" />
                  <strong>{n.name}</strong>
                  <span className={`badge ${n.status}`}>
                    {n.status === "online" ? "在线" : "离线"}
                  </span>
                </div>
                <NodeResources node={n} />
              </article>
            ))}
          </div>
        </>
      )}
      <div className="section-title">
        <h2>正在进行</h2>
        <button className="text-button" onClick={() => go("jobs")}>
          全部任务 <Icon name="arrow" size={16} />
        </button>
      </div>
      <section className="panel activity-list">
        {active.length ? (
          active.slice(0, 5).map((j) => (
            <div key={j.job_id}>
              <span className="activity-icon">
                <Icon
                  name={
                    j.task_type === "ai.image"
                      ? "image"
                      : j.task_type === "ai.chat"
                        ? "chat"
                        : "cpu"
                  }
                />
              </span>
              <div>
                <strong>{j.template_id}</strong>
                <small>
                  {j.status === "queued"
                    ? "等待可用节点"
                    : j.status === "cancel_requested"
                      ? "正在取消"
                      : "执行中"}
                </small>
              </div>
              <Progress value={j.progress} />
            </div>
          ))
        ) : (
          <div className="quiet-state">
            <Icon name="check" />
            <p>当前没有进行中的任务</p>
          </div>
        )}
      </section>
    </div>
  );
}
