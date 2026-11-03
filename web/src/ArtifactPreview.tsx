// Author: FubukiHanasaki
import { useEffect, useState } from "react";
import type { Artifact } from "./api";
export function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (
      !artifact.name.toLowerCase().endsWith(".png") ||
      artifact.size > 20 * 1024 * 1024 ||
      !artifact.download_url.startsWith("/api/v1/artifacts/")
    )
      return;
    const controller = new AbortController();
    let objectUrl = "";
    let active = true;
    fetch(artifact.download_url, {
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) return;
        const bytes = await response.arrayBuffer();
        if (
          bytes.byteLength > 20 * 1024 * 1024 ||
          ![137, 80, 78, 71, 13, 10, 26, 10].every(
            (value, index) => new Uint8Array(bytes)[index] === value,
          )
        )
          return;
        if (!active) return;
        objectUrl = URL.createObjectURL(
          new Blob([bytes], { type: "image/png" }),
        );
        setUrl(objectUrl);
      })
      .catch(() => {});
    return () => {
      active = false;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setUrl("");
    };
  }, [artifact.id, artifact.download_url, artifact.size, artifact.name]);
  return url ? (
    <img
      className="artifact-preview"
      src={url}
      alt={`生成结果：${artifact.name}`}
    />
  ) : null;
}
