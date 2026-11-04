// Author: FubukiHanasaki
import { useEffect, useRef, useState } from "react";
import { api, type Artifact, type Job, type Model } from "./api";
import { Icon } from "./Icons";
import { ArtifactPreview } from "./ArtifactPreview";
import { MediaImage, readMedia } from "./Media";
import { Progress } from "./Visuals";
function GalleryItem({
  job,
  select,
}: {
  job: Job;
  select: (id: string) => void;
}) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(job.status === "completed");
  useEffect(() => {
    let live = true;
    if (job.status === "completed") {
      setLoading(true);
      api<{ items: Artifact[] }>(`/api/v1/jobs/${job.job_id}/artifacts`)
        .then((d) => {
          if (live) setArtifacts(d.items);
        })
        .catch((e) => {
          if (live) setError(e.message);
        })
        .finally(() => {
          if (live) setLoading(false);
        });
    }
    return () => {
      live = false;
    };
  }, [job.job_id, job.status]);
  return (
    <article className="gallery-card">
      <div className="gallery-image">
        {artifacts.length ? (
          <ArtifactPreview artifact={artifacts[0]} />
        ) : (
          <div className="gallery-placeholder">
            <Icon name="image" size={36} />
            <span>
              {job.status === "completed"
                ? loading
                  ? "正在读取图像…"
                  : error
                    ? "图像加载失败"
                    : "工件已过期或无可用图片"
                : job.status === "queued"
                  ? "等待算力"
                  : job.status === "running"
                    ? "正在绘制"
                    : job.status === "failed"
                      ? "生成失败"
                      : job.status}
            </span>
            {!["completed", "failed", "cancelled"].includes(job.status) && (
              <Progress value={job.progress} />
            )}
          </div>
        )}
      </div>
      <div className="gallery-caption">
        <div>
          <strong>{job.template_id}</strong>
          <small>
            {new Date(job.created_at * 1000).toLocaleString("zh-CN")}
          </small>
        </div>
        <button onClick={() => select(job.job_id)}>
          查看任务 <Icon name="arrow" size={14} />
        </button>
      </div>
      {error && (
        <p role="alert" className="small danger-text">
          {error}
        </p>
      )}
    </article>
  );
}
export function ImageStudio({
  models,
  jobs,
  submit,
  select,
  offset,
  setOffset,
}: {
  models: Model[];
  jobs: Job[];
  submit: (body: unknown) => Promise<void>;
  select: (id: string) => void;
  offset: number;
  setOffset: (value: number) => void;
}) {
  const available = models.filter((m) => m.task_type === "ai.image");
  const [model, setModel] = useState(""),
    [prompt, setPrompt] = useState(""),
    [size, setSize] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [images, setImages] = useState<{ url: string; name: string }[]>([]);
  const file = useRef<HTMLInputElement>(null);
  const selected = available.find((m) => m.id === model) || available[0];
  const sizes = [
    "256x256",
    "512x512",
    "768x768",
    "1024x1024",
    "1536x1024",
    "1024x1536",
  ].filter((s) => !selected?.synthetic || ["256x256", "512x512"].includes(s));
  const preferredSize = String(
    size || selected?.image_parameters?.default_size || "",
  );
  const effectiveSize =
    preferredSize && sizes.includes(preferredSize)
      ? preferredSize
      : selected?.synthetic
        ? "512x512"
        : "1024x1024";
  return (
    <div className="image-studio">
      <section className="image-controls panel">
        <div className="studio-form-title">
          <span className="model-emblem image">
            <Icon name="image" size={24} />
          </span>
          <h2>描绘你的想象</h2>
        </div>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await submit({
                model: selected?.id,
                prompt,
                size: effectiveSize,
                ...(images.length ? { images: images.map((i) => i.url) } : {}),
              });
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            模型
            <select
              aria-label="图像模型"
              required
              value={selected?.id || ""}
              onChange={(e) => {
                setModel(e.target.value);
                setSize("");
                setImages([]);
              }}
            >
              {!available.length && <option value="">暂无授权图像模型</option>}
              {available.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name || m.id}
                  {m.synthetic ? " · 协议测试" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            画面描述
            <textarea
              required
              rows={7}
              maxLength={2000}
              placeholder="描述主体、动作、光线和构图…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </label>
          <label>
            画幅
            <select
              aria-label="画幅"
              value={effectiveSize}
              onChange={(e) => setSize(e.target.value)}
            >
              {sizes.map((s) => (
                <option key={s} value={s}>
                  {s.replace("x", " × ")}
                </option>
              ))}
            </select>
          </label>
          <div className="reference-heading">
            <span>参考图片</span>
            <button
              type="button"
              className="text-button"
              disabled={busy || images.length >= 4 || selected?.synthetic}
              onClick={() => file.current?.click()}
            >
              <Icon name="plus" size={16} />
              添加
            </button>
          </div>
          <input
            ref={file}
            type="file"
            className="sr-only"
            accept="image/png,image/jpeg,image/webp"
            aria-label="参考图片"
            onChange={async (e) => {
              const input = e.currentTarget,
                f = input.files?.[0];
              if (!f) return;
              setBusy(true);
              try {
                if (images.length >= 4) throw new Error("最多 4 张参考图片");
                const media = await readMedia(f);
                setImages([...images, ...media]);
              } catch (e) {
                setError(String(e));
              } finally {
                setBusy(false);
                input.value = "";
              }
            }}
          />
          <div className="attachment-strip">
            {images.map((i, index) => (
              <div key={index}>
                <MediaImage src={i.url} />
                <button
                  type="button"
                  aria-label={`移除参考图片 ${index + 1}`}
                  onClick={() =>
                    setImages(images.filter((_, n) => n !== index))
                  }
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          <small className="field-hint">
            参考图片需要节点批准的编辑工作流；采样步数、CFG
            与种子使用管理员默认值。
          </small>
          {error && (
            <p role="alert" className="notice error-notice">
              {error}
            </p>
          )}
          <button className="primary wide" disabled={busy || !available.length}>
            {busy ? "处理中…" : "生成图像"}
            <Icon name="arrow" size={18} />
          </button>
        </form>
      </section>
      <section className="gallery-workspace">
        <div className="section-title">
          <h2>作品与进度</h2>
          <span className="muted small">{jobs.length} 项</span>
        </div>
        {jobs.length ? (
          <div className="gallery-grid">
            {jobs.map((j) => (
              <GalleryItem key={j.job_id} job={j} select={select} />
            ))}
          </div>
        ) : (
          <div className="gallery-empty">
            <div className="empty-canvas">
              <Icon name="image" size={56} />
            </div>
            <h2>这里，等待你的第一幅作品。</h2>
            <p>写下画面描述，让想象有迹可循。</p>
          </div>
        )}
        <div className="pagination">
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            上一页
          </button>
          <span>
            {jobs.length ? offset + 1 : 0}–{offset + jobs.length}
          </span>
          <button
            disabled={jobs.length < 30}
            onClick={() => setOffset(offset + 30)}
          >
            下一页
          </button>
        </div>
      </section>
    </div>
  );
}
