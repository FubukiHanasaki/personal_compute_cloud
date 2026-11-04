// Author: FubukiHanasaki
import type { CSSProperties } from "react";
import type { Node } from "./api";
import { Icon } from "./Icons";
const clamp = (value: number) =>
  Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
export function Progress({
  value,
  label = "任务进度",
}: {
  value: number;
  label?: string;
}) {
  const percent = Math.round(clamp(value * 100));
  return (
    <div className="progress-value">
      <progress aria-label={label} max={100} value={percent} />
      <span>{percent}%</span>
    </div>
  );
}
export function Gauge({
  label,
  value,
  detail,
}: {
  label: string;
  value: number;
  detail?: string;
}) {
  const percent = Math.round(clamp(value));
  return (
    <div className="gauge-card">
      <div
        className={`gauge ${percent >= 85 ? "high" : ""}`}
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        style={{ "--value": `${percent}%` } as CSSProperties}
      >
        <strong>
          {percent}
          <small>%</small>
        </strong>
      </div>
      <div>
        <h3>{label}</h3>
        {detail && <p className="muted small">{detail}</p>}
      </div>
    </div>
  );
}
export function NodeResources({ node }: { node: Node }) {
  const c = node.capabilities;
  const resources = [
    { key: "cpu", name: "处理器", icon: "cpu", load: c.cpu_load },
    { key: "memory_mb", name: "内存", icon: "memory", load: c.memory_load },
    {
      key: "vram_mb",
      name: "显存",
      icon: "gpu",
      load: c.physical?.vram_mb
        ? 100 * (1 - (c.available?.vram_mb || 0) / c.physical.vram_mb)
        : 0,
    },
  ];
  const unit = (key: string, value: number) =>
    key === "cpu"
      ? `${value.toLocaleString()} 核`
      : `${(value / 1024).toFixed(1)} GB`;
  return (
    <div className="resource-cards">
      {resources.map((r) => {
        const total = c.physical?.[r.key] || 0,
          allocated = c.allocatable?.[r.key] || 0,
          available = c.available?.[r.key] || 0;
        return (
          <section
            className={`resource-card ${r.key}`}
            key={r.key}
            aria-label={`${node.name}${r.name}资源`}
          >
            <div className="resource-card-head">
              <Icon name={r.icon} />
              <span>{r.name}</span>
              <span className="resource-load">
                {total ? `${Math.round(clamp(r.load))}% 使用` : "未检测到"}
              </span>
            </div>
            <strong className="resource-capacity">
              {unit(r.key, allocated)}
              <small>分配额度</small>
            </strong>
            <div className="resource-segments" aria-hidden="true">
              {Array.from({ length: 16 }, (_, i) => (
                <i
                  className={
                    i < Math.round((clamp(r.load) / 100) * 16) ? "filled" : ""
                  }
                  key={i}
                />
              ))}
            </div>
            <dl>
              <div>
                <dt>设备总量</dt>
                <dd>{unit(r.key, total)}</dd>
              </div>
              <div>
                <dt>设备可用</dt>
                <dd>{unit(r.key, available)}</dd>
              </div>
            </dl>
          </section>
        );
      })}
    </div>
  );
}
