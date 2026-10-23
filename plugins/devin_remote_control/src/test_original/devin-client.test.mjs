import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { DevinClient } = require("../extension/lib/devin-client.js");
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
