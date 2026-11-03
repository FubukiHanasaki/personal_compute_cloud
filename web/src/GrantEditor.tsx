// Author: FubukiHanasaki
import { useState } from "react";
import { api, type User, type Template, type Node } from "./api";
export function GrantEditor({
  user,
  templates,
  nodes,
  save,
  cancel,
}: {
  user: User;
  templates: Template[];
  nodes: Node[];
  save: (f: () => Promise<unknown>, success?: string) => Promise<void>;
  cancel: () => void;
}) {
  const [grants, setGrants] = useState<Record<string, string[]>>({
    ...user.grants,
  });
  const [busy, setBusy] = useState(false);
  const groups = [
    {
      key: "templates",
      label: "模型与计算模板",
      items: templates.map((t) => ({
        id: t.id,
        name: t.name + (t.public ? " · 已公开" : ""),
      })),
    },
    { key: "nodes", label: "节点管理", items: nodes },
    { key: "view_nodes", label: "节点查看", items: nodes },
  ];
  return (
    <form
      className="grant-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await save(async () => {
            await api(`/api/v1/admin/users/${user.id}`, "PUT", {
              status: user.status,
              grants,
            });
            cancel();
          }, "授权已保存");
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="section-title">
        <h3>{user.username} 的资源授权</h3>
        <button type="button" onClick={cancel}>
          取消
        </button>
      </div>
      {groups.map((group) => (
        <fieldset key={group.key}>
          <legend>{group.label}</legend>
          <div className="choice-grid">
            {group.items.map((item) => (
              <label className="choice" key={item.id}>
                <input
                  type="checkbox"
                  checked={(grants[group.key] || []).includes(item.id)}
                  onChange={(e) =>
                    setGrants({
                      ...grants,
                      [group.key]: e.target.checked
                        ? [...(grants[group.key] || []), item.id]
                        : (grants[group.key] || []).filter(
                            (id) => id !== item.id,
                          ),
                    })
                  }
                />
                <span>
                  {item.name}
                  <small>{item.id}</small>
                </span>
              </label>
            ))}
            {!group.items.length && <p className="muted">暂无可选资源</p>}
          </div>
          {(grants[group.key] || [])
            .filter((id) => !group.items.some((item) => item.id === id))
            .map((id) => (
              <label className="check-label" key={id}>
                <input
                  type="checkbox"
                  checked
                  onChange={() =>
                    setGrants({
                      ...grants,
                      [group.key]: grants[group.key].filter(
                        (value) => value !== id,
                      ),
                    })
                  }
                />
                {id} · 未列出的已有授权
              </label>
            ))}
        </fieldset>
      ))}
      <p className="field-hint">
        公开模板不受这些勾选限制；节点管理仅对 Admin
        生效。已有任务授权保持不变。
      </p>
      <button className="primary" disabled={busy}>
        {busy ? "保存中…" : "保存授权"}
      </button>
    </form>
  );
}
