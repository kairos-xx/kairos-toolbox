import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { FileService } = require("../extension/lib/file-service.js");
/**
 * Fixture.
 * @param {(context: {root: string, project: {id: string, root: string}}) => Promise<*>} callback Callback.
 */
const fixture = async callback => {
  const root = await mkdtemp(join(tmpdir(), "devin-remote-"));
  try {
    await callback({ root, project: { id: "project", root } });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
};
test("file service reads, writes and lists owned text files", async () => fixture(async ({ root, project }) => {
  await mkdir(join(root, "src"));
  await writeFile(join(root, "src", "app.js"), "const value = 1;\n", "utf8");
  const service = new FileService(1024 * 1024, ["node_modules"]);
  assert.equal((await service.list(project, "src"))[0]?.name, "app.js");
  const file = await service.read(project, "src/app.js");
  await service.write(project, "src/app.js", "const value = 2;\n", file.mtimeMs);
  assert.equal(await readFile(join(root, "src", "app.js"), "utf8"), "const value = 2;\n");
}));
test("file service rejects traversal", async () => fixture(async ({ project }) => {
  const service = new FileService(1024 * 1024, []);
  await assert.rejects(() => service.read(project, "../outside.txt"), /project root/u);
}));
test("file service rejects mutation through symlinks", async () => fixture(async ({ root, project }) => {
  await mkdir(join(root, "real"));
  await writeFile(join(root, "real", "data.txt"), "safe\n", "utf8");
  await symlink(join(root, "real"), join(root, "linked"), "dir");
  const service = new FileService(1024 * 1024, []);
  await assert.rejects(
    () => service.write(project, "linked/new.txt", "blocked\n"),
    /symlinked directories/u
  );
  await assert.rejects(
    () => FileService.remove(project, "linked"),
    /Removing symlinks/u
  );
}));
test("file service searches and profiles the workspace", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-files-search-"));
  try {
    await writeFile(join(root, "alpha.js"), "const needle = 1;\n", "utf8");
    await writeFile(join(root, "beta.txt"), "nothing here\n", "utf8");
    const service = new FileService(1024 * 1024, []);
    const results = await service.search({ root }, "needle", 10);
    const stats = await service.stats({ root });
    assert.equal(results[0]?.path, "alpha.js");
    assert.equal(results[0]?.line, 1);
    assert.equal(stats.files, 2);
    assert.equal(stats.languages.find(item => item.language === "javascript")?.files, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
test("file service transfers binary files without weakening path safety", async () => {
  await fixture(async ({ root, project }) => {
    const service = new FileService(1024 * 1024, []);
    const encoded = Buffer.from([0, 1, 2, 250, 255]).toString("base64");
    const written = await service.writeBase64(project, "asset.bin", encoded);
    const raw = await service.readRaw(project, "asset.bin");
    assert.equal(written.size, 5);
    assert.deepEqual([...raw.content], [0, 1, 2, 250, 255]);
    assert.equal(raw.contentType, "application/octet-stream");
    await assert.rejects(
      () => service.writeBase64(project, "asset.bin", encoded),
      /already exists/u
    );
    assert.equal((await readFile(join(root, "asset.bin"))).byteLength, 5);
  });
});
test("file inspection summarizes code and symbols", async () => fixture(async ({ root, project }) => {
  await writeFile(
    join(root, "module.js"),
    "export class Demo {}\nfunction helper() {}\n// TODO: improve\n",
    "utf8"
  );
  const service = new FileService(1024 * 1024, []);
  const info = await service.inspect(project, "module.js");
  const symbols = /** @type {{name: string}[]} */ (Reflect.get(info, "symbols") ?? []);
  assert.equal(Reflect.get(info, "language"), "javascript");
  assert.equal(Reflect.get(info, "todo"), 1);
  assert.equal(symbols[0]?.name, "Demo");
  assert.equal(symbols[1]?.name, "helper");
}));
