import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { createRequire } from "node:module";
const execute = promisify(execFile);
const require = createRequire(import.meta.url);
const { GitService } = require("../../../plugins/devin_remote_control/src/extension/lib/git-service.js");
test("git service exposes history, branches and commit operations", async () => {
  const root = await mkdtemp(join(tmpdir(), "devin-git-"));
  try {
    await execute("git", ["init", "-q"], { cwd: root });
    await execute("git", ["config", "user.email", "test@example.com"], { cwd: root });
    await execute("git", ["config", "user.name", "Test User"], { cwd: root });
    await writeFile(join(root, "a.txt"), "one\n", "utf8");
    await execute("git", ["add", "a.txt"], { cwd: root });
    await execute("git", ["commit", "-qm", "initial"], { cwd: root });
    const git = new GitService({ root });
    assert.equal((await git.log(5))[0]?.subject, "initial");
    const baseBranch = (await git.status()).branch ?? "";
    const initialHash = (await git.log(5))[0]?.hash ?? "";
    const created = await git.createBranch("feature/mobile", true);
    assert.equal(created.status.branch, "feature/mobile");
    await writeFile(join(root, "a.txt"), "two\n", "utf8");
    await git.stageAll();
    const committed = await git.commit("update file");
    assert.equal(committed.log[0]?.subject, "update file");
    assert.equal((await git.status()).files.length, 0);
    const detail = await git.commitDetail(committed.log[0]?.hash ?? "");
    assert.match(detail?.message ?? "", /update file/u);
    assert.equal((await git.blame("a.txt"))[0]?.author, "Test User");
    assert.equal((await git.fileHistory("a.txt", 10)).length, 2);
    assert.equal((await git.fileAtRevision("a.txt", initialHash)).content, "one\n");
    const comparison = await git.compare(baseBranch, "feature/mobile");
    assert.match(comparison.diff, /two/u);
    await writeFile(join(root, "a.txt"), "three\n", "utf8");
    const stashed = await git.stash("mobile stash");
    assert.equal(stashed.stashes.length, 1);
    assert.equal((await git.status()).files.length, 0);
    const restored = await git.applyStash(stashed.stashes[0]?.ref ?? "", true);
    assert.equal(restored.stashes.length, 0);
    assert.equal((await git.status()).files.length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
