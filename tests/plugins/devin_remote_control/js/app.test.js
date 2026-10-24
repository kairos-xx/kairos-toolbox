"use strict";
const assert = require("node:assert/strict");
/** Fake element used by DOM-oriented presentation tests. */
class FakeElement {
	/** Initialize a fake element.
	 * @param {string} text Initial text content.
	 */
	constructor(text = "") {
		this.className = "";
		this.textContent = text;
		/** @type {unknown[]} */
		this.childNodes = [];
		/** @type {unknown | null} */
		this.firstElementChild = null;
		this.tabIndex = 0;
		this.classList = {
			add: () => {},
			toggle: () => {}
		};
	}
	/** Append child nodes.
	 * @param {...unknown} nodes Child nodes.
	 */
	append(...nodes) {
		this.childNodes.push(...nodes);
		if (this.firstElementChild === null && nodes.length > 0) this.firstElementChild = nodes[0] ?? null;
	}
	/** Replace child nodes.
	 * @param {...unknown} nodes Replacement child nodes.
	 */
	replaceChildren(...nodes) {
		this.childNodes = [...nodes];
		this.firstElementChild = nodes[0] ?? null;
	}
	/** Accept an attribute assignment.
	 * @param {string} name Attribute name.
	 * @param {string} value Attribute value.
	 */
	setAttribute(name, value) {
		Reflect.set(this, name, value);
	}
}
const test = require("node:test");
test("web app static presentation helpers", () => {
	globalThis.document = /** @type {Document} */ ( /** @type {unknown} */ ({
		createElement: ( /** @type {string} */ tag) => ({
			tag,
			className: "",
			textContent: "",
		}),
		createElementNS: () => ({
			classList: {
				add: () => {}
			},
			setAttribute: () => {},
			append: () => {},
		}),
	}));
	const {
		DevinRemoteApp
	} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
	const element = DevinRemoteApp.element("div", "card", "hello");
	assert.equal(element.className, "card");
	assert.equal(element.textContent, "hello");
	assert.equal(DevinRemoteApp.statusClass("running"), "running");
	assert.equal(DevinRemoteApp.statusClass("paused"), "waiting");
	assert.equal(DevinRemoteApp.statusClass("error"), "failed");
	assert.equal(DevinRemoteApp.text(null), "");
});
test("web app static data helpers cover alternate representations", () => {
	const {
		DevinRemoteApp
	} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
	assert.equal(DevinRemoteApp.errorText(new Error("boom")), "boom");
	assert.equal(DevinRemoteApp.errorText("boom"), "boom");
	assert.equal(DevinRemoteApp.sessionId({
		devin_id: "devin"
	}), "devin");
	assert.equal(DevinRemoteApp.sessionId({
		id: "id"
	}), "id");
	assert.equal(DevinRemoteApp.sessionId({
		session_id: "session"
	}), "session");
	assert.equal(DevinRemoteApp.status({
		status: "RUNNING"
	}), "running");
	assert.equal(DevinRemoteApp.status({
		state: "PAUSED"
	}), "paused");
	assert.equal(DevinRemoteApp.status({
		is_archived: true
	}), "archived");
	assert.equal(DevinRemoteApp.status({}), "unknown");
	assert.deepEqual(DevinRemoteApp.tags({
		tags: [1, "two"]
	}), ["1", "two"]);
	assert.deepEqual(DevinRemoteApp.tags({
		metadata: {
			tags: ["meta"]
		}
	}), ["meta"]);
	assert.deepEqual(DevinRemoteApp.tags({}), []);
	assert.equal(DevinRemoteApp.title({
		title: "title"
	}), "title");
	assert.equal(DevinRemoteApp.title({
		name: "name"
	}), "name");
	assert.equal(DevinRemoteApp.title({
		prompt: "prompt"
	}), "prompt");
	assert.equal(DevinRemoteApp.title({
		id: "fallback"
	}), "fallback");
});
test("web app static message helpers cover content forms", () => {
	const {
		DevinRemoteApp
	} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
	assert.equal(DevinRemoteApp.messageText({
		message: "message"
	}), "message");
	assert.equal(DevinRemoteApp.messageText({
		content: ["one", {
			text: "two"
		}, {}]
	}), "one\ntwo\n", );
	assert.equal(DevinRemoteApp.messageText({
		content: {
			text: "nested"
		}
	}), "nested");
	assert.equal(DevinRemoteApp.messageText({
		text: "text"
	}), "text");
	assert.equal(DevinRemoteApp.messageIsAgent({
		is_devin: true
	}), true);
	assert.equal(DevinRemoteApp.messageIsAgent({
		source: "devin"
	}), true);
	assert.equal(DevinRemoteApp.messageIsAgent({
		role: "assistant"
	}), true);
	assert.equal(DevinRemoteApp.messageIsAgent({
		author: "devin"
	}), true);
	assert.equal(DevinRemoteApp.messageIsAgent({
		role: "user"
	}), false);
});
test("web app relative time covers all display ranges", () => {
	const {
		DevinRemoteApp
	} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
	const now = Date.now();
	assert.equal(DevinRemoteApp.relativeTime("invalid"), "");
	assert.match(DevinRemoteApp.relativeTime(now - 10_000), /^10s ago$/u);
	assert.match(DevinRemoteApp.relativeTime(now - 120_000), /^2m ago$/u);
	assert.match(DevinRemoteApp.relativeTime(now - 7_200_000), /^2h ago$/u);
	assert.match(DevinRemoteApp.relativeTime(now - 172_800_000), /^2d ago$/u);
});
test("web app rich presentation helpers cover markdown and formatting", () => {
	/** Create a fake element.
	 * @param {string} text Initial text.
	 * @returns {HTMLElement} Fake HTML element.
	 */
	const fakeElement = (text = "") => /** @type {HTMLElement} */ ( /** @type {unknown} */ (new FakeElement(text)));
	globalThis.document = /** @type {Document} */ ( /** @type {unknown} */ ({
		createElement: () => fakeElement(),
		createElementNS: () => fakeElement(),
		createTextNode: ( /** @type {string} */ text) => /** @type {Text} */ ( /** @type {unknown} */ ({
			textContent: text
		})),
		createDocumentFragment: () => fakeElement(),
		querySelector: () => fakeElement(),
	}));
	const {
		DevinRemoteApp
	} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
	assert.ok(DevinRemoteApp.markdownNodes("# Head\n- one\n- two\n```\nconst x = 1;\n```\nplain").length > 0);
	assert.ok(DevinRemoteApp.highlight('const x = "value"; // note').length > 1);
	assert.equal(DevinRemoteApp.insightText("plain"), "plain");
	assert.equal(DevinRemoteApp.insightText({
		title: "title"
	}), "title");
	assert.equal(DevinRemoteApp.insightText({
		description: "description"
	}), "description");
	assert.equal(DevinRemoteApp.insightText({
		value: 1
	}), '{"value":1}');
	assert.equal(DevinRemoteApp.duration(10), "10s uptime");
	assert.equal(DevinRemoteApp.duration(120), "2m uptime");
	assert.equal(DevinRemoteApp.duration(7200), "2h uptime");
	assert.equal(DevinRemoteApp.duration(172800), "2d uptime");
	assert.equal(DevinRemoteApp.formatBytes(10), "10 B");
	assert.equal(DevinRemoteApp.formatBytes(2048), "2.0 KB");
	assert.equal(DevinRemoteApp.formatBytes(2097152), "2.0 MB");
	assert.equal(DevinRemoteApp.bytesToBase64(new Uint8Array([65, 66, 67])), "QUJD");
	assert.ok(DevinRemoteApp.messageNode({
		role: "assistant",
		content: "hello",
		attachments: [{
			name: "x",
			url: "https://example.test/x"
		}]
	}));
	assert.ok(DevinRemoteApp.messageNode({
		role: "user",
		content: "hello",
		attachments: [{
			name: "x",
			url: "file:///x"
		}, {
			name: "local"
		}]
	}));
});
test("web app cards and DOM helpers cover presentation branches", () => {
	/** @type {Array<HTMLElement>} */
	const badges = [];
	/** Create a fake card element.
	 * @param {string} text Initial text.
	 * @returns {HTMLElement} Fake HTML element.
	 */
	const node = (text = "") => /** @type {HTMLElement} */ ( /** @type {unknown} */ (new FakeElement(text)));
	const badge = node();
	badges.push(badge);
	globalThis.document = /** @type {Document} */ ( /** @type {unknown} */ ({
		createElement: () => node(),
		createElementNS: () => node(),
		createDocumentFragment: () => node(),
		querySelector: ( /** @type {string} */ selector) => selector.includes("agents") ? badge : null,
	}));
	const {
		DevinRemoteApp
	} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
	assert.ok(DevinRemoteApp.agentCard({
		id: "agent",
		title: "Agent",
		status: "running",
		current_message: "Working",
		acu_usage: 1.25,
		updated_at: Date.now(),
		pull_request: {
			url: "https://example.test/pr"
		},
	}));
	assert.ok(DevinRemoteApp.agentCard({
		id: "idle",
		created_at: Date.now()
	}));
	assert.ok(DevinRemoteApp.reviewMetric("12", "files"));
	assert.ok(DevinRemoteApp.changeCard({
		path: "src/a.js",
		status: "M "
	}));
	assert.ok(DevinRemoteApp.changeCard({
		path: "src/b.js",
		status: " M"
	}));
	assert.ok(DevinRemoteApp.changeCard({
		path: "src/c.js",
		status: "??"
	}));
	assert.ok(DevinRemoteApp.empty("i-agent", "Empty", "Nothing here"));
	DevinRemoteApp.badge("agents", 3);
	assert.equal(badge.textContent, "3");
	DevinRemoteApp.badge("agents", 0);
	assert.equal(badge.textContent, "");
	const target = node();
	DevinRemoteApp.replace(target, [node("a"), node("b")]);
	assert.equal(target.childNodes.length, 1);
});
