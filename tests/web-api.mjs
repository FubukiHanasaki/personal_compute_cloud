// Browser transport contract regression; no browser or external network. Fubuki.
import assert from "node:assert/strict";
import { test } from "node:test";
import { listAll, streamChat, api, setCsrf } from "../tmp/web-api/api.js";

test("selectors traverse resource pages instead of truncating at the first page", async () => {
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(url);
    const offset = Number(
      new URL(url, "http://test").searchParams.get("offset"),
    );
    return Response.json({
      items: Array.from(
        { length: offset === 0 ? 100 : 3 },
        (_, i) => offset + i,
      ),
    });
  };
  assert.equal((await listAll("/api/v1/keys")).length, 103);
  assert.deepEqual(requests, [
    "/api/v1/keys?limit=100&offset=0",
    "/api/v1/keys?limit=100&offset=100",
  ]);
});

function sse(text) {
  // Deliberately split in the middle of UTF-8 characters and frame boundaries.
  const bytes = new TextEncoder().encode(text);
  return new Response(
    new ReadableStream({
      start(controller) {
        for (let i = 0; i < bytes.length; i += 3)
          controller.enqueue(bytes.slice(i, i + 3));
        controller.close();
      },
    }),
  );
}
test("SSE reassembles fragmented Chinese text and requires a completion marker", async () => {
  globalThis.fetch = async () =>
    sse(
      'data: {"choices":[{"delta":{"content":"中文回复"}}]}\n\ndata: [DONE]\n\n',
    );
  let content = "";
  await streamChat("test", [{ role: "user", content: "你好" }], (text) => {
    content += text;
  });
  assert.equal(content, "中文回复");
  globalThis.fetch = async () =>
    sse('data: {"choices":[{"delta":{"content":"不完整"}}]}\n\n');
  await assert.rejects(
    streamChat("test", [], () => {}),
    /响应意外中断/,
  );
  globalThis.fetch = async () =>
    sse('data: {"error":{"message":"推理失败"}}\n\ndata: [DONE]\n\n');
  await assert.rejects(
    streamChat("test", [], () => {}),
    /推理失败/,
  );
});

test("session writes attach CSRF and surface server errors without success", async () => {
  setCsrf("test-csrf");
  globalThis.fetch = async (_url, init) => {
    assert.equal(init.credentials, "same-origin");
    assert.equal(init.headers["X-CSRF-Token"], "test-csrf");
    return Response.json({ error: { message: "权限不足" } }, { status: 403 });
  };
  await assert.rejects(api("/api/v1/jobs", "POST", {}), /权限不足/);
});
