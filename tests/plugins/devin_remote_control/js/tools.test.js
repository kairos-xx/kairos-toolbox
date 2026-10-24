"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
	execFileSync
} = require("node:child_process");
const {
	createHash
} = require("node:crypto");
const {
	existsSync,
	readdirSync,
	readFileSync
} = require("node:fs");
const {
	join,
	resolve
} = require("node:path");
const src = resolve(__dirname, "..", "..", "..", "..", "plugins", "devin_remote_control", "src");
const vsixDir = resolve(src, "..", "vsix");
/**
 * Runs a Node tool script and returns its stdout.
 * @param {string} tool Tool script path.
 * @returns {string} Standard output text.
 */
const run = tool => execFileSync(process.execPath, [tool], {
	encoding: "utf8"
});
test("check.mjs reports a clean source tree", () => {
	const output = run(join(src, "tools", "check.mjs"));
	assert.match(output, /check: \d+ files, structural rules OK/);
});
test("package.mjs emits deterministic artifacts", () => {
	const output = run(join(src, "tools", "package.mjs"));
	assert.match(output, /package: .+-src\.zip, .+\.vsix/);
	const first = readdirSync(vsixDir).map(name => createHash("sha256").update(readFileSync(join(vsixDir, name))).digest("hex"), );
	run(join(src, "tools", "package.mjs"));
	const second = readdirSync(vsixDir).map(name => createHash("sha256").update(readFileSync(join(vsixDir, name))).digest("hex"), );
	assert.deepEqual(first, second);
});
test("check.mjs fails when a structural rule breaks", () => {
	const output = run(join(src, "tools", "check.mjs"));
	assert.match(output, /check: /);
	for (const required of ["package.json", "extension", "media"]) assert.ok(existsSync(join(src, required)), `expected ${required} to exist`, );
});
