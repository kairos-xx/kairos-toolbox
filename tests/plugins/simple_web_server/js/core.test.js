"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
	ServerPolicy
} = require("../../../../plugins/simple_web_server/src/core.js");
test("validates ports", () => {
	assert.equal(ServerPolicy.normalizePort("8000"), 8000);
	for (const value of [0, 65536, "x", 1.5]) assert.throws(() => ServerPolicy.normalizePort(value), RangeError);
});
test("builds url and command", () => {
	assert.equal(ServerPolicy.url(9000), "http://127.0.0.1:9000/");
	const c = ServerPolicy.command("python3", 9000, ".");
	assert.equal(c.command, "python3");
	assert.deepEqual(c.args.slice(0, 5), ["-m", "http.server", "9000", "--bind", "127.0.0.1"]);
	assert.ok(c.args[6]);
});
test("selects root", () => {
	assert.equal(ServerPolicy.root("/workspace", "/fallback"), "/workspace");
	assert.equal(ServerPolicy.root(undefined, "/fallback"), "/fallback");
});
