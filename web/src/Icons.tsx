// Author: FubukiHanasaki
import type { CSSProperties } from "react";
const paths: Record<string, string> = {
  home: "M3 10 12 3l9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z M9 21v-8h6v8",
  chat: "M21 11a8 8 0 0 1-8 8H7l-5 3 2-6a8 8 0 1 1 17-5Z M8 10h8 M8 14h5",
  image: "M3 3h18v18H3Z M3 17l6-6 4 4 3-3 5 5 M15 7h.01",
  nodes: "M3 4h18v6H3Z M3 14h18v6H3Z M7 7h.01 M7 17h.01 M17 7h1 M17 17h1",
  jobs: "M5 4h14v17H5Z M9 4V2h6v2 M9 9h6 M9 13h6 M9 17h3",
  models: "m12 2 10 5-10 5L2 7Z M2 12l10 5 10-5 M2 17l10 5 10-5",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  cpu: "M6 6h12v12H6Z M9 9h6v6H9Z M9 2v4 M15 2v4 M9 18v4 M15 18v4 M2 9h4 M2 15h4 M18 9h4 M18 15h4",
  memory:
    "M3 7h18v10H3Z M6 10v4 M10 10v4 M14 10v4 M18 10v4 M6 17v3 M10 17v3 M14 17v3 M18 17v3",
  gpu: "M2 6h19v12H2Z M7 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4 M15 10h3 M15 14h3 M5 18v3 M19 18v3",
  plus: "M12 5v14 M5 12h14",
  upload: "M12 16V3 M7 8l5-5 5 5 M4 15v6h16v-6",
  check: "m5 12 4 4L19 6",
  close: "M6 6l12 12 M6 18 18 6",
  clock: "M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  refresh: "M20 7V3l-3 3a8 8 0 1 0 3 11 M20 3h-5",
  lock: "M5 10h14v11H5Z M8 10V6a4 4 0 0 1 8 0v4",
};
export function Icon({
  name,
  size = 20,
  style,
}: {
  name: string;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      <path d={paths[name] || paths.models} />
    </svg>
  );
}
