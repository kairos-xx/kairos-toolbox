import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { TaskService } = require("../../../plugins/devin_remote_control/src/extension/lib/task-service.js");
test("task service discovers package scripts without executing them", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-tasks-"));
  try {
    await writeFile(join(root, "package.json"), JSON.stringify({ scripts: { test: "node --test", build: "vite build" } }), "utf8");
    await writeFile(join(root, "pnpm-lock.yaml"), "lockfileVersion: '9.0'\n", "utf8");
    const tasks = await TaskService.discover({ root });
    assert.equal(tasks.length, 2);
    assert.equal(tasks[0]?.command, "pnpm run 'test'");
    assert.equal(tasks[1]?.source, "package.json");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
test("task service discovers python and make tasks alongside package managers", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-tasks-multi-"));
  try {
    await writeFile(join(root, "pyproject.toml"), "[project]\nname = 'demo'\n", "utf8");
    await writeFile(
      join(root, "Makefile"),
      "build:\n\techo ok\nclean:\n\techo clean\n.PHONY: build\n",
      "utf8"
    );
    await writeFile(
      join(root, "package.json"),
      JSON.stringify({ scripts: { dev: "vite" } }),
      "utf8"
    );
    await writeFile(join(root, "yarn.lock"), "# yarn\n", "utf8");
    const tasks = await TaskService.discover({ root });
    const sources = new Set(tasks.map(task => task.source));
    assert.ok(sources.has("package.json"));
    assert.ok(sources.has("Python"));
    assert.ok(sources.has("Makefile"));
    assert.equal(tasks[0]?.command, "yarn run 'dev'");
    assert.ok(tasks.some(task => task.id === "python:pytest"));
    assert.ok(tasks.some(task => task.id === "make:build"));
    assert.ok(tasks.every(task => task.id !== "make:.PHONY"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
test("task service detects bun projects and tolerates empty workspaces", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-tasks-bun-"));
  const empty = await mkdtemp(join(tmpdir(), "devin-tasks-empty-"));
  try {
    await writeFile(join(root, "bun.lockb"), "binary", "utf8");
    await writeFile(
      join(root, "package.json"),
      JSON.stringify({ scripts: { start: "bun run index.ts" } }),
      "utf8"
    );
    const tasks = await TaskService.discover({ root });
    assert.equal(tasks[0]?.command, "bun run 'start'");
    assert.deepEqual(await TaskService.discover({ root: empty }), []);
  } finally {
    await Promise.all([
      rm(root, { recursive: true, force: true }),
      rm(empty, { recursive: true, force: true })
    ]);
  }
});
test("task service rethrows manifest errors other than missing files", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-tasks-eacces-"));
  try {
    const manifest = join(root, "package.json");
    await writeFile(manifest, "{", "utf8");
    await assert.rejects(() => TaskService.discover({ root }));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
