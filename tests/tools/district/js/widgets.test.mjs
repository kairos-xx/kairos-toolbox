/** Tests for the widget renderer that turns command results into DOM. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
import {
	Widgets
} from "../../../../tools/district/web/js/widgets.js";
const {
	document
} = buildSandbox();
const now = Date.parse("2026-01-01T12:00:00Z");
/**
 * Render one result into a fresh container.
 * @param {object} result Command result.
 * @returns {import("./sandbox-dom.mjs").FakeElement} The container.
 */
const show = result => {
	const container = document.createElement("div");
	Widgets.render(/** @type {HTMLElement} */ (/** @type {unknown} */ (container)), /** @type {import("../../../../tools/district/web/js/widgets.js").CommandResult} */ (result), now);
	return container;
};
/**
 * Build a result around some widgets.
 * @param {object[]} widgets Widgets.
 * @param {boolean} [ok] Whether the command succeeded.
 * @returns {object} The result.
 */
const around = (widgets, ok = true) => ({
	command: "demo",
	title: "Demo",
	ok,
	widgets
});
test("a result shows its title and whether it worked", () => {
	const good = show(around([]));
	assert.equal(good.querySelector(".w-result-title")?.textContent, "Demo");
	assert.equal(good.querySelector(".w-result-state")?.textContent, "Done");
	assert.equal(good.querySelector(".w-result-state")?.getAttribute("data-tone"), "good");
	const bad = show(around([], false));
	assert.equal(bad.querySelector(".w-result-state")?.textContent, "Failed");
	assert.equal(bad.querySelector(".w-result-state")?.getAttribute("data-tone"), "bad");
});
test("metrics show the value first and fill a gauge only when given a share", () => {
	const view = show(around([{
		type: "metrics",
		items: [{
			label: "Memory",
			value: "42%",
			detail: "6 of 16 GiB",
			tone: "warn",
			percent: 42
		}, {
			label: "Districts",
			value: "3",
			detail: "",
			tone: "neutral",
			percent: null
		}]
	}]));
	const metrics = view.querySelectorAll(".w-metric");
	assert.equal(metrics.length, 2);
	assert.equal(metrics[0]?.getAttribute("data-tone"), "warn");
	assert.equal(metrics[0]?.querySelector(".w-metric-value")?.textContent, "42%");
	assert.equal(metrics[0]?.querySelector(".w-metric-detail")?.textContent, "6 of 16 GiB");
	assert.equal(metrics[0]?.querySelector(".w-gauge")?.style.properties["--runtime-gauge-fill"], "42%");
	assert.equal(metrics[1]?.querySelector(".w-gauge"), null);
	assert.equal(metrics[1]?.querySelector(".w-metric-detail"), null);
});
test("facts link a value only when it has a destination", () => {
	const view = show(around([{
		type: "facts",
		title: "District",
		items: [{
			label: "District",
			value: "ab12cd",
			tone: "neutral",
			href: "#/session/ab12cd"
		}, {
			label: "Directory",
			value: "/work",
			tone: "neutral",
			href: ""
		}]
	}]));
	assert.equal(view.querySelector(".w-title")?.textContent, "District");
	const links = view.querySelectorAll("a");
	assert.equal(links.length, 1);
	assert.equal(links[0]?.href, "#/session/ab12cd");
	assert.equal(view.querySelectorAll(".w-fact").length, 2);
});
test("table cells present each kind of value the way it should be read", () => {
	const view = show(around([{
		type: "table",
		title: "Commands",
		columns: [{
			key: "id",
			label: "District",
			kind: "session"
		}, {
			key: "agent",
			label: "Agent",
			kind: "agent"
		}, {
			key: "when",
			label: "When",
			kind: "time"
		}, {
			key: "took",
			label: "Took",
			kind: "duration"
		}, {
			key: "size",
			label: "Size",
			kind: "bytes"
		}, {
			key: "exit",
			label: "Result",
			kind: "exit"
		}, {
			key: "cmd",
			label: "Command",
			kind: "mono"
		}],
		rows: [{
			id: "ab12cd",
			agent: "devin",
			when: "2026-01-01T10:00:00Z",
			took: 1.5,
			size: 1536,
			exit: 3,
			cmd: "make test"
		}, {
			id: "ef34ab",
			agent: "",
			when: "",
			took: null,
			size: 0,
			exit: 0,
			cmd: ""
		}],
		empty: "Nothing."
	}]));
	const rows = view.querySelector("tbody")?.querySelectorAll("tr") ?? [];
	assert.equal(rows.length, 2);
	const first = rows[0]?.querySelectorAll("td");
	assert.ok(first !== undefined);
	assert.equal(first[0]?.querySelector("a")?.href, "#/session/ab12cd");
	assert.equal(first[1]?.querySelector("a")?.href, "#/agent/devin");
	assert.equal(first[2]?.querySelector(".w-cell-value")?.textContent, "2h ago");
	assert.equal(first[3]?.querySelector(".w-cell-value")?.textContent, "1.5s");
	assert.equal(first[4]?.querySelector(".w-cell-value")?.textContent, "1.5 KiB");
	assert.equal(first[5]?.querySelector(".w-cell-value")?.textContent, "exit 3");
	assert.equal(first[5]?.querySelector(".w-cell-value")?.getAttribute("data-ok"), "false");
	assert.equal(first[6]?.querySelector("code")?.textContent, "make test");
	const second = rows[1]?.querySelectorAll("td");
	assert.ok(second !== undefined);
	assert.equal(second[1]?.querySelector(".w-cell-value")?.textContent, "—");
	assert.equal(second[2]?.querySelector(".w-cell-value")?.textContent, "—");
	assert.equal(second[5]?.querySelector(".w-cell-value")?.textContent, "ok");
	assert.equal(second[5]?.querySelector(".w-cell-value")?.getAttribute("data-ok"), "true");
});
test("every table cell repeats its column label for the narrow layout", () => {
	const view = show(around([{
		type: "table",
		title: "T",
		columns: [{
			key: "a",
			label: "Alpha",
			kind: "text"
		}],
		rows: [{
			a: "x"
		}],
		empty: ""
	}]));
	assert.equal(view.querySelector("td")?.querySelector(".w-cell-label")?.textContent, "Alpha");
	assert.equal(view.querySelectorAll("th")[0]?.textContent, "Alpha");
});
test("an empty table says why instead of showing bare headings", () => {
	const view = show(around([{
		type: "table",
		title: "T",
		columns: [{
			key: "a",
			label: "Alpha",
			kind: "text"
		}],
		rows: [],
		empty: "No district matches."
	}]));
	assert.equal(view.querySelector(".w-empty")?.textContent, "No district matches.");
	assert.equal(view.querySelector("table"), null);
});
test("checks say passed or failed in words as well as with a mark", () => {
	const view = show(around([{
		type: "checks",
		title: "Checks",
		items: [{
			name: "python_runtime",
			ok: true,
			detail: "3.13"
		}, {
			name: "database",
			ok: false,
			detail: "locked"
		}]
	}]));
	const items = view.querySelectorAll(".w-check");
	assert.equal(items[0]?.getAttribute("data-ok"), "true");
	assert.equal(items[0]?.querySelector(".w-check-word")?.textContent, "passed");
	assert.equal(items[1]?.getAttribute("data-ok"), "false");
	assert.equal(items[1]?.querySelector(".w-check-word")?.textContent, "failed");
	assert.equal(items[1]?.querySelector(".w-check-detail")?.textContent, "locked");
});
test("console output keeps its text exactly", () => {
	const view = show(around([{
		type: "console",
		title: "ls",
		text: "a\n  b\n"
	}]));
	assert.equal(view.querySelector("pre")?.textContent, "a\n  b\n");
	assert.equal(view.querySelector(".w-title")?.textContent, "ls");
});
test("a notice is announced and carries its emphasis", () => {
	const view = show(around([{
		type: "notice",
		tone: "good",
		text: "District ab12cd stopped."
	}]));
	const notice = view.querySelector(".w-notice");
	assert.equal(notice?.textContent, "District ab12cd stopped.");
	assert.equal(notice?.getAttribute("data-tone"), "good");
	assert.equal(notice?.getAttribute("role"), "status");
});
test("a widget type the page does not know is skipped without failing", () => {
	const view = show(around([{
		type: "hologram"
	}, {
		type: "notice",
		tone: "neutral",
		text: "still here"
	}]));
	assert.equal(view.querySelectorAll(".w-notice").length, 1);
});
