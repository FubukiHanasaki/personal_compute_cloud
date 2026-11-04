// Author: FubukiHanasaki
import { useLayoutEffect, useRef, useState } from "react";
import { api, type Node, type User } from "./api";
import { Icon } from "./Icons";
import { NodeResources } from "./Visuals";
import { NodePolicy } from "./NodePolicy";

type Act = (f: () => Promise<unknown>, success?: string) => Promise<void>;
type Origin = { x: number; y: number; width: number; height: number };
const modes: Record<string, string> = {
  shared: "共享",
  private: "私人",
  reserved: "预留",
  maintenance: "维护",
  offline: "离线",
};
const statuses: Record<string, string> = {
  online: "在线",
  stale: "心跳过期",
  offline: "离线",
  disabled: "已禁用",
};
function Status({ node }: { node: Node }) {
  return (
    <span className={`badge ${node.status}`}>
      {statuses[node.status] || node.status}
    </span>
  );
}
function Rings({ node }: { node: Node }) {
  const c = node.capabilities;
  const metrics = [
    {
      key: "cpu",
      label: "CPU",
      load: c.cpu_load,
      amount: `${c.allocatable?.cpu || 0} 核`,
    },
    {
      key: "memory_mb",
      label: "内存",
      load: c.memory_load,
      amount: `${((c.allocatable?.memory_mb || 0) / 1024).toFixed(1)} GB`,
    },
    {
      key: "vram_mb",
      label: "显存",
      load: c.physical?.vram_mb
        ? 100 * (1 - (c.available?.vram_mb || 0) / c.physical.vram_mb)
        : 0,
      amount: `${((c.allocatable?.vram_mb || 0) / 1024).toFixed(1)} GB`,
    },
  ];
  return (
    <div className="node-rings">
      {metrics.map((m) => {
        const present = (c.physical?.[m.key] || 0) > 0;
        const percent = Math.round(
          Math.min(100, Math.max(0, Number.isFinite(m.load) ? m.load : 0)),
        );
        return (
          <div className={`node-ring-metric ${m.key}`} key={m.key}>
            <div
              className={`node-ring ${percent >= 85 ? "high" : ""} ${!present ? "unavailable" : ""}`}
              role={present ? "meter" : "img"}
              aria-label={`${node.name}${m.label}${present ? "使用率" : "未检测到"}`}
              {...(present
                ? {
                    "aria-valuemin": 0,
                    "aria-valuemax": 100,
                    "aria-valuenow": percent,
                  }
                : {})}
            >
              <svg viewBox="0 0 80 80" aria-hidden="true">
                <circle className="ring-track" cx="40" cy="40" r="33" />
                <circle
                  className="ring-fill"
                  cx="40"
                  cy="40"
                  r="33"
                  pathLength="100"
                  strokeDasharray="100"
                  strokeDashoffset={present ? 100 - percent : 100}
                />
              </svg>
              <strong>
                {present ? percent : "—"}
                {present && <small>%</small>}
              </strong>
            </div>
            <span>{m.label}</span>
            <small>{present ? m.amount : "未检测到"}</small>
          </div>
        );
      })}
    </div>
  );
}
function NodeDetail({
  node,
  users,
  act,
  origin,
  close,
}: {
  node: Node;
  users: User[];
  act: Act;
  origin: Origin;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    closing = useRef(false);
  const [isClosing, setClosing] = useState(false);
  const working = useRef(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    text: string;
    error: boolean;
  } | null>(null);
  async function perform(
    task: () => Promise<unknown>,
    success = "节点设置已保存",
  ) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setFeedback(null);
    try {
      await act(async () => {
        try {
          await task();
          setFeedback({ text: success, error: false });
        } catch (error) {
          setFeedback({
            text: error instanceof Error ? error.message : String(error),
            error: true,
          });
          throw error;
        }
      }, success);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  useLayoutEffect(() => {
    const element = dialog.current!;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element.style.animation = "none";
    element.showModal();
    const target = element.getBoundingClientRect();
    element.style.setProperty(
      "--node-x",
      `${origin.x + origin.width / 2 - target.x - target.width / 2}px`,
    );
    element.style.setProperty(
      "--node-y",
      `${origin.y + origin.height / 2 - target.y - target.height / 2}px`,
    );
    element.style.setProperty(
      "--node-scale-x",
      String(Math.min(1, origin.width / target.width)),
    );
    element.style.setProperty(
      "--node-scale-y",
      String(Math.min(1, origin.height / target.height)),
    );
    element.style.removeProperty("animation");
    return () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
      element.close();
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [origin]);
  function finish() {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    dialog.current?.close();
    close();
  }
  function dismiss() {
    if (closing.current) return;
    closing.current = true;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      finish();
      return;
    }
    setClosing(true);
    closeTimer.current = setTimeout(finish, 240);
  }
  return (
    <dialog
      ref={dialog}
      className={`node-detail-dialog ${isClosing ? "is-closing" : ""}`}
      aria-labelledby="node-detail-title"
      onCancel={(e) => {
        e.preventDefault();
        dismiss();
      }}
      onClick={(e) => {
        if (e.target === dialog.current) {
          const rect = dialog.current.getBoundingClientRect();
          if (
            e.clientX < rect.left ||
            e.clientX > rect.right ||
            e.clientY < rect.top ||
            e.clientY > rect.bottom
          )
            dismiss();
        }
      }}
      onAnimationEnd={(e) => {
        if (
          e.target === dialog.current &&
          e.animationName === "node-dialog-out"
        )
          finish();
      }}
    >
      <div className="node-detail-surface">
        <header className="node-detail-heading">
          <span className="node-icon">
            <Icon name="nodes" size={24} />
          </span>
          <div>
            <h2 id="node-detail-title">{node.name}</h2>
            <span className="small muted">
              {node.os} · {node.id.slice(0, 8)}
            </span>
          </div>
          <Status node={node} />
          <button
            autoFocus
            className="node-detail-close"
            aria-label="收起节点详情"
            onClick={dismiss}
          >
            <Icon name="close" size={19} />
          </button>
        </header>
        <div className="node-detail-content">
          <NodeResources node={node} />
          <div className="node-detail-meta">
            <span>
              本地模式{" "}
              <strong>
                {modes[String(node.local_policy.mode)] || "未报告"}
              </strong>
            </span>
            <span>
              最近心跳{" "}
              {new Date(node.last_seen * 1000).toLocaleString("zh-CN", {
                hour12: false,
              })}
            </span>
          </div>
          <div className="node-detail-workers">
            <h3>已启用能力</h3>
            <div>
              {node.capabilities.workers.length ? (
                node.capabilities.workers.map((w) => <code key={w}>{w}</code>)
              ) : (
                <span className="muted small">暂无启用的 Worker</span>
              )}
            </div>
          </div>
          {feedback && (
            <p
              className={`notice ${feedback.error ? "error-notice" : ""}`}
              role={feedback.error ? "alert" : "status"}
            >
              {feedback.text}
            </p>
          )}
          <div className="node-actions">
            <label>
              云端模式
              <select
                aria-label={`${node.name}共享策略`}
                value={String(node.policy.mode)}
                disabled={node.disabled || busy}
                onChange={(e) =>
                  void perform(() =>
                    api(`/api/v1/nodes/${node.id}/policy`, "PUT", {
                      ...node.policy,
                      mode: e.target.value,
                    }),
                  )
                }
              >
                {Object.entries(modes).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={busy}
              className="danger"
              onClick={() => {
                if (confirm("请求中止该节点上的所有 PCC 任务？"))
                  void perform(
                    () => api(`/api/v1/nodes/${node.id}/abort`, "POST"),
                    "已请求中止受管任务",
                  );
              }}
            >
              中止受管任务
            </button>
            <button
              disabled={busy}
              className="text-button danger-text"
              onClick={() => {
                if (confirm("吊销后需重新注册，确认继续？"))
                  void perform(
                    () => api(`/api/v1/nodes/${node.id}/revoke`, "POST"),
                    "节点凭证已吊销",
                  );
              }}
            >
              吊销节点
            </button>
          </div>
          <NodePolicy
            node={{ ...node, disabled: node.disabled || busy }}
            users={users}
            save={(policy) =>
              perform(() =>
                api(`/api/v1/nodes/${node.id}/policy`, "PUT", policy),
              )
            }
          />
        </div>
      </div>
    </dialog>
  );
}
export function NodeGallery({
  nodes,
  users,
  act,
}: {
  nodes: Node[];
  users: User[];
  act: Act;
}) {
  const [selection, setSelection] = useState<{
    id: string;
    origin: Origin;
  } | null>(null);
  const [search, setSearch] = useState(""),
    [status, setStatus] = useState("all");
  const visible = nodes.filter(
    (node) =>
      (status === "all" || node.status === status) &&
      `${node.name} ${node.os} ${(node.capabilities.workers || []).join(" ")}`
        .toLocaleLowerCase()
        .includes(search.trim().toLocaleLowerCase()),
  );
  const selected = nodes.find((n) => n.id === selection?.id);
  return (
    <>
      <div className="node-filters">
        <input
          type="search"
          aria-label="搜索节点"
          placeholder="搜索节点、系统或模型"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          aria-label="节点状态筛选"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="all">全部状态</option>
          {Object.entries(statuses).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="small muted" role="status">
          {visible.length} / {nodes.length} 个节点
        </span>
      </div>
      <div className="node-gallery">
        {visible.map((node) => (
          <button
            className="node-tile"
            key={node.id}
            aria-label={`查看 ${node.name} 详情`}
            aria-haspopup="dialog"
            aria-expanded={selected?.id === node.id}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              setSelection({
                id: node.id,
                origin: {
                  x: rect.x,
                  y: rect.y,
                  width: rect.width,
                  height: rect.height,
                },
              });
            }}
          >
            <span className="node-tile-heading">
              <span className="node-tile-icon">
                <Icon name="nodes" size={18} />
              </span>
              <strong>{node.name}</strong>
              <Status node={node} />
            </span>
            <Rings node={node} />
            <span className="node-tile-footer">
              <span>
                {node.os} · {modes[String(node.local_policy.mode)] || "未报告"}
              </span>
              <span>
                查看详情 <Icon name="arrow" size={14} />
              </span>
            </span>
          </button>
        ))}
      </div>
      {!visible.length && (
        <p className="node-filter-empty">
          {nodes.length ? "没有匹配的节点" : "暂无计算节点"}
        </p>
      )}
      {selected && selection && (
        <NodeDetail
          node={selected}
          users={users}
          act={act}
          origin={selection.origin}
          close={() => setSelection(null)}
        />
      )}
    </>
  );
}
