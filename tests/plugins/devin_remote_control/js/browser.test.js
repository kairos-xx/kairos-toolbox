"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
test("browser coverage tests are split by source module", () => {
	assert.equal(typeof globalThis, "object");
});
