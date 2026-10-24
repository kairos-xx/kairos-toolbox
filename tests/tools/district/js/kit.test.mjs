/** Tests for the shared formatters and DOM builders. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
import {
	Kit
} from "../../../../tools/district/web/js/kit.js";
buildSandbox();
test("bytes use binary units and keep one decimal only while small", () => {
	assert.equal(Kit.bytes(0), "0 B");
	assert.equal(Kit.bytes(1023), "1023 B");
	assert.equal(Kit.bytes(1536), "1.5 KiB");
	assert.equal(Kit.bytes(10 * 1024 * 1024), "10 MiB");
	assert.equal(Kit.bytes(5.5 * 1024 ** 3), "5.5 GiB");
	assert.equal(Kit.bytes(2 * 1024 ** 4), "2.0 TiB");
});
test("spans of time show the two largest units", () => {
	assert.equal(Kit.span(0), "0s");
	assert.equal(Kit.span(59), "59s");
	assert.equal(Kit.span(61), "1m");
	assert.equal(Kit.span(3600 + 5 * 60), "1h 5m");
	assert.equal(Kit.span(2 * 86400 + 3 * 3600), "2d 3h");
	assert.equal(Kit.span(2 * 3600), "2h");
	assert.equal(Kit.span(2 * 86400), "2d");
});
test("a past moment reads as how long ago, and a future one as how long from now", () => {
	const now = Date.parse("2026-01-01T12:00:00Z");
	assert.equal(Kit.ago("2026-01-01T11:59:30Z", now), "30s ago");
	assert.equal(Kit.ago("2026-01-01T10:00:00Z", now), "2h ago");
	assert.equal(Kit.ago("2026-01-01T12:10:00Z", now), "in 10m");
	assert.equal(Kit.ago("", now), "—");
	assert.equal(Kit.ago("not a time", now), "—");
});
test("a duration in seconds is short and never empty", () => {
	assert.equal(Kit.duration(null), "—");
	assert.equal(Kit.duration(0.25), "250ms");
	assert.equal(Kit.duration(1.5), "1.5s");
});
test("a percentage keeps a decimal only below ten", () => {
	assert.equal(Kit.percent(3.14159), "3.1%");
	assert.equal(Kit.percent(42.6), "43%");
	assert.equal(Kit.percent(100), "100%");
});
test("an element is built with its class and text", () => {
	const node = Kit.el("span", "badge good", "ok");
	assert.equal(node.tagName, "SPAN");
	assert.equal(node.className, "badge good");
	assert.equal(node.textContent, "ok");
});
test("a link carries its destination and class", () => {
	const node = Kit.link("devin", "#/agent/devin", "chip");
	assert.equal(node.href, "#/agent/devin");
	assert.equal(node.className, "chip");
	assert.equal(node.textContent, "devin");
});
test("copying uses the clipboard when the page may", async () => {
	/** @type {string[]} */
	const written = [];
	const copied = await Kit.copy("district://x", /** @type {Navigator} */ ({
		clipboard: {
			writeText: async text => {
				written.push(text);
			}
		}
	}));
	assert.equal(copied, true);
	assert.deepEqual(written, ["district://x"]);
});
test("copying falls back when the clipboard refuses, and says so when nothing works", async () => {
	const refusing = /** @type {Navigator} */ (/** @type {unknown} */ ({
		clipboard: {
			writeText: async () => {
				throw new DOMException("denied", "NotAllowedError");
			}
		}
	}));
	assert.equal(await Kit.copy("x", refusing), false);
	assert.equal(await Kit.copy("x", /** @type {Navigator} */ ({})), false);
});
test("the legacy path selects a hidden field, copies and cleans up", async () => {
	/** @type {string[]} */
	const steps = [];
	const original = document.execCommand;
	Object.assign(document, {
		execCommand: /** @param {string} command */
	command => {
			steps.push(command);
			return true;
		}
	});
	const copied = await Kit.copy("x", /** @type {Navigator} */ ({}));
	Object.assign(document, {
		execCommand: original
	});
	assert.equal(copied, true);
	assert.deepEqual(steps, ["copy"]);
	assert.equal(document.body.children.length, 0);
});
