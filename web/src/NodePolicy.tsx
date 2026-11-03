// Author: Fubuki
import { useEffect, useState } from "react";
import type { Node } from "./api";

export function NodePolicy({
  node,
  save,
}: {
  node: Node;
  save: (policy: Record<string, unknown>) => Promise<void>;
}) {
  const [allowed, setAllowed] = useState("");
  const [cpu, setCpu] = useState(85);
  const [memory, setMemory] = useState(90);
  const [concurrency, setConcurrency] = useState(1);
  const signature = JSON.stringify(node.policy);
  useEffect(() => {
    setAllowed(((node.policy.allowed_users || []) as string[]).join(", "));
    setCpu(Number(node.policy.max_cpu_load || 85));
    setMemory(Number(node.policy.max_memory_load || 90));
    setConcurrency(Number(node.policy.max_concurrency || 1));
  }, [signature]);
  return (
    <details className="policy-editor">
      <summary>预留名单与负载保护</summary>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          await save({
            ...node.policy,
            allowed_users: allowed
              .split(",")
              .map((v) => v.trim())
              .filter(Boolean),
            max_cpu_load: cpu,
            max_memory_load: memory,
            max_concurrency: concurrency,
          });
        }}
      >
        <label>
          预留用户 ID（逗号分隔）
          <input
            value={allowed}
            onChange={(e) => setAllowed(e.target.value)}
            placeholder="Reserved 模式允许的用户 ID"
          />
        </label>
        <div className="form-row">
          <label>
            CPU 暂停阈值 %
            <input
              type="number"
              required
              min={1}
              max={100}
              value={cpu}
              onChange={(e) => setCpu(Number(e.target.value))}
            />
          </label>
          <label>
            RAM 暂停阈值 %
            <input
              type="number"
              required
              min={1}
              max={100}
              value={memory}
              onChange={(e) => setMemory(Number(e.target.value))}
            />
          </label>
        </div>
        <label>
          云端并发上限
          <input
            type="number"
            required
            min={1}
            max={32}
            value={concurrency}
            onChange={(e) => setConcurrency(Number(e.target.value))}
          />
        </label>
        <p className="small muted">本地策略同时生效，实际并发取两侧上限。</p>
        <button disabled={node.disabled}>保存保护策略</button>
      </form>
    </details>
  );
}
