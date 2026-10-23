import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { FileService } = require("../extension/lib/file-service.js");
const { GatewayServer } = require("../extension/lib/gateway-server.js");
const { SecurityService } = require("../extension/lib/security.js");
test("gateway pairs a project and serves authenticated project APIs", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-gateway-project-"));
  const assets = await mkdtemp(join(tmpdir(), "devin-gateway-assets-"));
  const project = {
    id: "abcdef123456",
    name: "sample",
    root,
    gitRemote: ""
  };
  await writeFile(join(root, "hello.txt"), "hello\n", "utf8");
  await writeFile(join(assets, "index.html"), "<!doctype html><title>ok</title>", "utf8");
  const security = new SecurityService(12);
  const gateway = new GatewayServer({
    registry: /** @type {any} */ ({
      get: /** @type {(id: string) => any} */ (id => {
        assert.equal(id, project.id);
        return project;
      }),
      publicList: () => [{ id: project.id, name: project.name, gitRemote: "" }]
    }),
    security,
    files: new FileService(1024 * 1024, []),
    devin: /** @type {any} */ ({
      configured: () => false,
      listSessions: async () => [],
      createSession: async () => ({}),
      messages: async () => [],
      sendMessage: async () => ({}),
      archive: async () => ({}),
      terminate: async () => ({})
    }),
    providers: /** @type {any} */ ({
      providers: async () => [],
      chat: async () => ({ text: "" })
    }),
    terminals: /** @type {any} */ ({ closeAll: () => {}, create: () => { throw new Error("unused"); } }),
    assetsRoot: assets
  });
  try {
    const port = await gateway.start(0);
    const base = `http://127.0.0.1:${port}/p/${project.id}/`;
    const pairing = security.createPairing(project.id);
    const paired = await fetch(`${base}api/pair`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: pairing.code })
    });
    assert.equal(paired.status, 200);
    const cookie = paired.headers.get("set-cookie")?.split(";", 1)[0] ?? "";
    const { csrf } = await paired.json();
    assert.ok(cookie.includes("devin_remote_session="));
    assert.ok(csrf);
    const bootstrap = await fetch(`${base}api/bootstrap`, {
      headers: { Cookie: cookie }
    });
    assert.equal(bootstrap.status, 200);
    assert.equal((await bootstrap.json()).project.name, "sample");
    const saved = await fetch(`${base}api/file`, {
      method: "PUT",
      headers: {
        Cookie: cookie,
        "Content-Type": "application/json",
        "X-Devin-Remote-CSRF": csrf
      },
      body: JSON.stringify({ path: "hello.txt", content: "updated\n" })
    });
    assert.equal(saved.status, 200);
    const uploaded = await fetch(`${base}api/upload`, {
      method: "POST",
      headers: {
        Cookie: cookie,
        "Content-Type": "application/json",
        "X-Devin-Remote-CSRF": csrf
      },
      body: JSON.stringify({
        path: "asset.bin",
        base64: Buffer.from([0, 1, 2, 255]).toString("base64")
      })
    });
    assert.equal(uploaded.status, 201);
    const downloaded = await fetch(`${base}api/file/raw?path=asset.bin`, {
      headers: { Cookie: cookie }
    });
    assert.equal(downloaded.status, 200);
    assert.deepEqual([...new Uint8Array(await downloaded.arrayBuffer())], [0, 1, 2, 255]);
  } finally {
    await gateway.stop();
    await Promise.all([
      rm(root, { recursive: true, force: true }),
      rm(assets, { recursive: true, force: true })
    ]);
  }
});
