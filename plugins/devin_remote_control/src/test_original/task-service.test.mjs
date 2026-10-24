import assert from "node:assert/strict";
import {
	mkdtemp,
	rm,
	writeFile
} from "node:fs/promises";
import {
	tmpdir
} from "node:os";
import {
	join
} from "node:path";
import test from "node:test";
import {
	createRequire
} from "node:module";
const require = createRequire(import.meta.url);
const {
	TaskService
} = require("../extension/lib/task-service.js");
test("task service discovers package scripts without executing them", async () => {
	const root = await mkdtemp(join(tmpdir(), "devin-tasks-"));
	try {
		await writeFile(join(root, "package.json"), JSON.stringify({
			scripts: {
				test: "node --test",
				build: "vite build"
			}
		}), "utf8");
		await writeFile(join(root, "pnpm-lock.yaml"), "lockfileVersion: '9.0'\n", "utf8");
		const tasks = await TaskService.discover({
			root
		});
		assert.equal(tasks.length, 2);
		assert.equal(tasks[0]?.command, "pnpm run 'test'");
		assert.equal(tasks[1]?.source, "package.json");
	} finally {
		await rm(root, {
			recursive: true,
			force: true
		});
	}
});
