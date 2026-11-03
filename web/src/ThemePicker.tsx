// Author: FubukiHanasaki
import { useState } from "react";
const names = {
  primary: "主色",
  surface: "底色",
  accent: "强调色",
  soft: "柔和色",
  warm: "暖色",
  background: "背景",
  text: "文字基色",
};
const defaults = getComputedStyle(document.documentElement);
const colors = Object.fromEntries(
  Object.entries(names).map(([key, label]) => [
    key,
    [label, defaults.getPropertyValue(`--theme-${key}`).trim()],
  ]),
);
function read(): Record<string, string> {
  try {
    const value = JSON.parse(localStorage.getItem("pcc-theme") || "{}");
    return Object.fromEntries(
      Object.entries(value).filter(
        ([key, value]) =>
          Object.hasOwn(colors, key) &&
          typeof value === "string" &&
          /^#[0-9a-f]{6}$/i.test(value),
      ),
    ) as Record<string, string>;
  } catch {
    return {};
  }
}
export function applyTheme(values = read()) {
  for (const key of Object.keys(colors)) {
    if (values[key])
      document.documentElement.style.setProperty(`--theme-${key}`, values[key]);
    else document.documentElement.style.removeProperty(`--theme-${key}`);
  }
}
applyTheme();
export function ThemePicker() {
  const [values, setValues] = useState(read);
  const update = (next: Record<string, string>) => {
    setValues(next);
    applyTheme(next);
    try {
      localStorage.setItem("pcc-theme", JSON.stringify(next));
    } catch {
      /* Private browser storage may be unavailable. */
    }
  };
  return (
    <details className="theme-picker">
      <summary>主题</summary>
      <div className="theme-popover">
        {Object.entries(colors).map(([key, [name, fallback]]) => (
          <label key={key}>
            {name}
            <input
              type="color"
              value={values[key] || fallback}
              onChange={(e) => update({ ...values, [key]: e.target.value })}
            />
          </label>
        ))}
        <button onClick={() => update({})}>恢复默认</button>
      </div>
    </details>
  );
}
