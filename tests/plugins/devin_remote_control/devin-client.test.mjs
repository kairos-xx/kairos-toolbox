import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { DevinClient } = require("../../../plugins/devin_remote_control/src/extension/lib/devin-client.js");
test("new Devin sessions inherit a deterministic project tag", async () => {
  const client = new DevinClient("org-test", async () => "token");
  /** @type {any} */
  let captured = null;
  client.request = async (method, path, body) => {
    captured = { method, path, body };
    return { id: "devin-test" };
  };
  await client.createSession(
    { prompt: "Fix it", tags: ["manual"] },
    { id: "abcdef123456", gitRemote: "git@example/repo.git" }
  );
  assert.equal(captured.method, "POST");
  assert.equal(captured.path, "/sessions");
  assert.deepEqual(captured.body.tags, ["manual", "vscode-project:abcdef123456"]);
});
test("Devin session detail APIs map to current v3 organization routes", async () => {
  const client = new DevinClient("org-test", async () => "token");
  /** @type {any[]} */
  const calls = [];
  client.request = async (method, path, body = null) => {
    calls.push({ method, path, body });
    return method === "GET" ? { items: [] } : { status: "ok" };
  };
  await client.attachments("devin-test");
  await client.insights("devin-test");
  await client.generateInsights("devin-test");
  await client.tags("devin-test");
  await client.replaceTags("devin-test", ["review"]);
  assert.deepEqual(
    calls.map(call => `${call.method} ${call.path}`),
    [
      "GET /sessions/devin-test/attachments",
      "GET /sessions/devin-test/insights",
      "POST /sessions/devin-test/insights/generate",
      "GET /sessions/devin-test/tags",
      "PUT /sessions/devin-test/tags"
    ]
  );
  assert.deepEqual(calls.at(-1)?.body, { tags: ["review"] });
});
test("Devin client authenticates requests and paginates sessions and messages", async () => {
  const original = globalThis.fetch;
  /** @type {{url: string, method: string, authorization: string}[]} */
  const calls = [];
  globalThis.fetch = /** @type {typeof fetch} */ (async (url, init) => {
    const target = new URL(String(url));
    calls.push({
      url: String(url),
      method: String(init?.method ?? "GET"),
      authorization: String(
        String(Reflect.get(init?.headers ?? {}, "Authorization") ?? "")
      )
    });
    if (target.pathname.endsWith("/sessions") && init?.method === "GET") {
      if (!target.searchParams.get("after"))
        return new Response(JSON.stringify({
          items: [{ id: "devin-a" }],
          has_next_page: true,
          end_cursor: "cursor-1"
        }), { status: 200 });
      return new Response(JSON.stringify({ items: [{ id: "devin-b" }] }), { status: 200 });
    }
    if (target.pathname.endsWith("/messages") && init?.method === "GET") {
      if (!target.searchParams.get("after"))
        return new Response(JSON.stringify({
          items: [{ role: "user" }],
          has_next_page: true,
          end_cursor: "cursor-m"
        }), { status: 200 });
      return new Response(JSON.stringify({ items: [{ role: "agent" }] }), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  try {
    const client = new DevinClient("org-test", async () => "token-1");
    const sessions = await client.listSessions();
    assert.deepEqual(sessions, [{ id: "devin-a" }, { id: "devin-b" }]);
    assert.equal(calls[0]?.authorization, "Bearer token-1");
    const messages = await client.messages("devin-a");
    assert.deepEqual(messages, [{ role: "user" }, { role: "agent" }]);
    await client.listSessions(false);
    const archivedCall = calls.find(call => call.url.includes("is_archived=false"));
    assert.ok(archivedCall);
    await client.sendMessage("devin-a", "ping");
    await client.terminate("devin-a", true);
    await client.archive("devin-a");
    const terminate = calls.find(call => call.method === "DELETE");
    assert.ok(terminate?.url.includes("archive=true"));
  } finally {
    Reflect.set(globalThis, "fetch", original);
  }
});
test("Devin client surfaces upstream errors and configuration failures", async () => {
  const original = globalThis.fetch;
  /** @type {number} */
  let mode = 0;
  globalThis.fetch = /** @type {typeof fetch} */ (async () => {
    if (mode === 0)
      return new Response(JSON.stringify({ detail: [{ msg: "field required" }] }), { status: 422 });
    if (mode === 1) return new Response("not-json{", { status: 200 });
    return new Response(JSON.stringify({ message: "boom" }), { status: 500 });
  });
  try {
    const client = new DevinClient("org-test", async () => "token-1");
    await assert.rejects(() => client.listSessions(), /field required/u);
    mode = 1;
    await assert.rejects(() => client.listSessions(), /invalid JSON/u);
    mode = 2;
    await assert.rejects(() => client.listSessions(), /boom/u);
    const unconfigured = new DevinClient("", async () => undefined);
    await assert.rejects(() => unconfigured.listSessions(), /not configured/u);
  } finally {
    Reflect.set(globalThis, "fetch", original);
  }
});
