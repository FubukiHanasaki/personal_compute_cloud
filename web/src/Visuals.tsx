// Author: FubukiHanasaki
import type { CSSProperties } from "react";
import type { Node } from "./api";
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
  return (
    <>
      <div className="gauges">
        <Gauge label="CPU 使用率" value={c.cpu_load} />
        <Gauge label="内存使用率" value={c.memory_load} />
      </div>
      <div className="resource-bars">
        {(["cpu", "memory_mb", "vram_mb"] as const).map((key) => {
          const physical = c.physical?.[key] ?? 0;
          const allocated = c.allocatable?.[key] ?? 0;
          const available = c.available?.[key] ?? 0;
          return (
            <div key={key}>
              <div className="resource-row">
                <span>
                  {key === "cpu"
                    ? "CPU"
                    : key === "memory_mb"
                      ? "内存"
                      : "显存"}
                </span>
                <strong>
                  {allocated.toLocaleString()} / {physical.toLocaleString()}{" "}
                  {key === "cpu" ? "核" : "MB"}
                </strong>
              </div>
              <progress
                aria-label={`${node.name}的${key === "cpu" ? "CPU" : key === "memory_mb" ? "内存" : "显存"}分配额度`}
                value={allocated}
                max={Math.max(physical, allocated, 1)}
              />
              <small className="muted">
                可用 {available.toLocaleString()} {key === "cpu" ? "核" : "MB"}
              </small>
            </div>
          );
        })}
      </div>
    </>
  );
}
