// Author: FubukiHanasaki
import { useEffect, useState } from "react";
export function MediaImage({
  src,
  alt = "附件",
}: {
  src: string;
  alt?: string;
}) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    try {
      const [header, data] = src.split(",");
      const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const value = URL.createObjectURL(
        new Blob([bytes], { type: header.slice(5).split(";")[0] }),
      );
      setUrl(value);
      return () => URL.revokeObjectURL(value);
    } catch {
      setUrl("");
    }
  }, [src]);
  return url ? <img src={url} alt={alt} /> : null;
}
async function bounded<T>(operation: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new Error("附件解码超时，请更换文件")),
          15000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer!);
  }
}
function frame(source: CanvasImageSource, width: number, height: number) {
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, 1280 / Math.max(width, height));
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext("2d")!.drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}
export async function readMedia(
  file: File,
): Promise<{ url: string; name: string }[]> {
  if (file.size > 32 * 1024 * 1024)
    throw new Error("附件最大 32 MB，图片会缩放后发送");
  const src = URL.createObjectURL(file);
  try {
    if (["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      const img = new Image();
      img.src = src;
      await bounded(img.decode());
      if (img.naturalWidth * img.naturalHeight > 40_000_000)
        throw new Error("图片分辨率过大");
      return [
        {
          url: frame(img, img.naturalWidth, img.naturalHeight),
          name: file.name,
        },
      ];
    }
    if (file.type.startsWith("video/")) {
      const video = document.createElement("video");
      video.muted = true;
      video.preload = "metadata";
      video.src = src;
      await bounded(
        new Promise<void>((resolve, reject) => {
          video.onloadedmetadata = () => resolve();
          video.onerror = () => reject(new Error("浏览器无法解码此视频"));
        }),
      );
      if (
        !Number.isFinite(video.duration) ||
        video.duration <= 0.1 ||
        video.duration > 600 ||
        video.videoWidth * video.videoHeight > 16_000_000
      )
        throw new Error("视频最多 10 分钟，分辨率不能超过 1600 万像素");
      const result = [];
      try {
        for (let i = 0; i < 4; i++) {
          const time = Math.min(
            video.duration - 0.05,
            Math.max(0.001, (video.duration * (i + 0.5)) / 4),
          );
          await bounded(
            new Promise<void>((resolve, reject) => {
              video.onseeked = () => resolve();
              video.onerror = () => reject(new Error("视频采样失败"));
              video.currentTime = Math.max(0, time);
            }),
          );
          result.push({
            url: frame(video, video.videoWidth, video.videoHeight),
            name: `${file.name} · ${time.toFixed(1)}s`,
          });
        }
      } finally {
        video.removeAttribute("src");
        video.load();
      }
      return result;
    }
    throw new Error("支持 PNG、JPEG、WebP 图片和浏览器可解码的视频");
  } finally {
    URL.revokeObjectURL(src);
  }
}
