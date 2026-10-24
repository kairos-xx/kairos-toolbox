/** Tests for the command palette: find a command, fill in its form, see the result. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	ApiError
} from "../../../../tools/district/web/js/api.js";
import {
	Palette
} from "../../../../tools/district/web/js/palette.js";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
const {
	document
} = buildSandbox();
/**
 * Describe a command.
 * @param {object} extra Fields to override.
 * @returns {object} The specification.
 */
const spec = extra => ({
	name: "sessions",
	title: "Districts",
	summary: "Every live district.",
	group: "observe",
	danger: "safe",
	params: [],
	...extra
});
/**
 * Describe a text command parameter.
 * @param {string} name Parameter name.
 * @param {string} label Human-readable label.
 * @param {object} [extra] Fields to override.
 * @returns {object} The parameter specification.
 */
const text = (name, label, extra = {}) => ({
	name,
	label,
	kind: "text",
	required: false,
	default: "",
	choices: [],
	placeholder: "",
	minimum: 0,
	maximum: 0,
	...extra
});
const catalog = [spec({}), spec({
	name: "exec",
	title: "Run a command",
	summary: "Run a command in a district.",
	group: "act",
	danger: "change",
	params: [text("session", "District", {
		kind: "session",
		required: true
	}), text("command", "Command", {
		required: true
	})]
}), spec({
	name: "kill",
	title: "Stop a district",
	summary: "End a district and everything running in it.",
	group: "act",
	danger: "destructive",
	params: [text("session", "District", {
		kind: "session",
		required: true
	})]
}), spec({
	name: "health",
	title: "Health check",
	summary: "Check this installation.",
	group: "system"
})];
const lakes = [{
	id: "ab12cd",
	agent: "devin",
	cwd: "/work",
	command: ""
}];
/**
 * Build a successful command result.
 * @param {string} [title] Result title.
 * @returns {object} The successful result.
 */
const done = (title = "Districts") => ({
	command: "sessions",
	title,
	ok: true,
	widgets: [{
		type: "notice",
		tone: "good",
		text: "all good"
	}]
});
/**
 * Build a palette over a fake API.
 * @param {object} [overrides] Replacement handlers.
 * @returns {{palette:Palette,root:import("./sandbox-dom.mjs").FakeElement,posts:{path:string,body:any}[]}} The palette and a log of posts.
 */
const make = (overrides = {}) => {
	/** @type {{path:string,body:any}[]} */
	const posts = [];
	const api = {
		get: async () => ({
			commands: catalog
		}),
		post: async (/** @type {string} */ path, /** @type {object} */ body) => {
			posts.push({
				path,
				body
			});
			return done();
		},
		...overrides
	};
	const root = document.createElement("section");
	const palette = new Palette(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), /** @type {any} */ (api), () => 0);
	return {
		palette,
		root,
		posts
	};
};
/**
 * Let promises settle.
 * @returns {Promise<void>} Resolves after the microtask queue drains.
 */
const settle = () => new Promise(resolve => {
	setTimeout(resolve, 0);
});
/**
 * Find the tile for a command.
 * @param {import("./sandbox-dom.mjs").FakeElement} root The palette.
 * @param {string} title The command's title.
 * @returns {import("./sandbox-dom.mjs").FakeElement|undefined} Its button.
 */
const tile = (root, title) => root.querySelectorAll(".cmd").find(button => button.querySelector(".cmd-title")?.textContent === title);
test("every command is offered under a heading that says what it is for", async () => {
	const {
		palette,
		root
	} = make();
	await palette.load();
	assert.deepEqual(root.querySelectorAll(".cmd-group-title").map(node => node.textContent), ["Look around", "Work with lakes", "This installation"]);
	assert.equal(root.querySelectorAll(".cmd").length, 4);
	assert.equal(tile(root, "Stop a district")?.querySelector(".cmd-flag")?.textContent, "asks first");
	assert.equal(tile(root, "Districts")?.querySelector(".cmd-flag"), null);
});
test("typing narrows the commands by any word of their name or summary", async () => {
	const {
		palette,
		root
	} = make();
	await palette.load();
	const search = root.querySelector(".cmd-search");
	assert.ok(search !== null);
	search.value = "everything running";
	search.dispatchEvent(new Event("input"));
	assert.deepEqual(root.querySelectorAll(".cmd-title").map(node => node.textContent), ["Stop a district"]);
	search.value = "zzz";
	search.dispatchEvent(new Event("input"));
	assert.equal(root.querySelector(".cmd-none")?.textContent, "No command matches “zzz”.");
});
test("a safe command with nothing to fill in runs the moment it is chosen", async () => {
	const {
		palette,
		root,
		posts
	} = make();
	await palette.load();
	tile(root, "Health check")?.dispatchEvent(new Event("click"));
	await settle();
	assert.deepEqual(posts, [{
		path: "/api/commands/health",
		body: {
			values: {},
			confirmed: false
		}
	}]);
	assert.equal(root.querySelector(".w-result-title")?.textContent, "Districts");
	assert.equal(root.querySelector(".w-notice")?.textContent, "all good");
});
test("a command with inputs asks for them in a sheet and then shows the result", async () => {
	const {
		palette,
		root,
		posts
	} = make();
	await palette.load();
	palette.show("", lakes);
	tile(root, "Run a command")?.dispatchEvent(new Event("click"));
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	assert.equal(sheet?.getAttribute("open"), "");
	const [pick] = sheet.querySelectorAll("select");
	assert.ok(pick !== undefined);
	pick.value = "ab12cd";
	sheet.querySelectorAll("input")[0]?.setAttribute("value", "ls -la");
	sheet.querySelectorAll("button").find(button => button.textContent === "Run")?.dispatchEvent(new Event("click"));
	await settle();
	assert.deepEqual(posts, [{
		path: "/api/commands/exec",
		body: {
			values: {
				session: "ab12cd",
				command: "ls -la"
			},
			confirmed: false
		}
	}]);
	assert.equal(sheet?.getAttribute("open"), null);
	assert.equal(root.querySelectorAll(".run-card").length, 1);
});
test("a missing required value is explained in the sheet and nothing is sent", async () => {
	const {
		palette,
		root,
		posts
	} = make();
	await palette.load();
	palette.show("", lakes);
	tile(root, "Run a command")?.dispatchEvent(new Event("click"));
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	sheet.querySelectorAll("button").find(button => button.textContent === "Run")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(sheet?.querySelector(".sheet-error")?.textContent, "District is required.");
	assert.deepEqual(posts, []);
	assert.equal(sheet?.getAttribute("open"), "");
});
test("stopping a district names the consequence and sends the confirmation only when asked", async () => {
	const {
		palette,
		root,
		posts
	} = make();
	await palette.load();
	palette.show("", lakes);
	tile(root, "Stop a district")?.dispatchEvent(new Event("click"));
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	assert.equal(sheet?.querySelector(".sheet-summary")?.textContent, "End a district and everything running in it. This cannot be undone.");
	const buttons = sheet.querySelectorAll("button");
	assert.notEqual(buttons[1], undefined);
	assert.deepEqual(buttons.map(button => button.textContent), ["Cancel", "Stop a district"]);
	const destructive = buttons[1];
	assert.ok(destructive !== undefined);
	assert.equal(destructive.getAttribute("data-tone"), "danger");
	const select = sheet.querySelector("select");
	assert.ok(select !== null);
	select.value = "ab12cd";
	destructive.dispatchEvent(new Event("click"));
	await settle();
	const posted = posts[0];
	assert.ok(posted !== undefined);
	assert.equal(posted.body.confirmed, true);
});
test("a failure from the server stays in the sheet in the server's words", async () => {
	const {
		palette,
		root
	} = make({
		post: async () => {
			throw new ApiError(404, "There is no live district 'ab12cd'.");
		}
	});
	await palette.load();
	palette.show("", lakes);
	tile(root, "Run a command")?.dispatchEvent(new Event("click"));
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	const select = sheet.querySelector("select");
	assert.ok(select !== null);
	select.value = "ab12cd";
	sheet.querySelectorAll("input")[0]?.setAttribute("value", "ls");
	sheet.querySelectorAll("button").find(button => button.textContent === "Run")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(sheet?.querySelector(".sheet-error")?.textContent, "There is no live district 'ab12cd'.");
	assert.equal(root.querySelectorAll(".run-card").length, 0);
});
test("a command that runs on its own and fails leaves a card that says why", async () => {
	const {
		palette,
		root
	} = make({
		post: async () => {
			throw new ApiError(0, "Cannot reach the server.");
		}
	});
	await palette.load();
	tile(root, "Health check")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(root.querySelector(".run-card")?.querySelector(".w-notice")?.textContent, "Cannot reach the server.");
	assert.equal(root.querySelector(".run-card")?.querySelector(".w-notice")?.getAttribute("data-tone"), "bad");
});
test("the same result can be run again or dismissed", async () => {
	const {
		palette,
		root,
		posts
	} = make();
	await palette.load();
	tile(root, "Health check")?.dispatchEvent(new Event("click"));
	await settle();
	const again = root.querySelectorAll(".run-again")[0];
	assert.ok(again !== undefined);
	again.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(posts.length, 2);
	assert.equal(root.querySelectorAll(".run-card").length, 1);
	root.querySelectorAll(".run-dismiss")[0]?.dispatchEvent(new Event("click"));
	assert.equal(root.querySelectorAll(".run-card").length, 0);
});
test("only the latest few results are kept", async () => {
	const {
		palette,
		root
	} = make();
	await palette.load();
	/**
	 * Run the health check until the asked number of results are on screen.
	 * @param {number} count How many more results to add.
	 * @returns {Promise<void>} Settles when the results are drawn.
	 */
	const addResult = async count => {
		if (count === 0) return;
		tile(root, "Health check")?.dispatchEvent(new Event("click"));
		await settle();
		await addResult(count - 1);
	};
	await addResult(8);
	assert.equal(root.querySelectorAll(".run-card").length, Palette.keep);
});
test("a link to a command opens its form once, however often the screen refreshes", async () => {
	const {
		palette,
		root
	} = make();
	await palette.load();
	palette.show("exec", lakes);
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	assert.equal(sheet?.getAttribute("open"), "");
	sheet.querySelectorAll("button").find(button => button.textContent === "Cancel")?.dispatchEvent(new Event("click"));
	assert.equal(sheet?.getAttribute("open"), null);
	palette.show("exec", lakes);
	assert.equal(sheet?.getAttribute("open"), null);
});
test("if the commands cannot be loaded the screen says so and offers a retry", async () => {
	let attempts = 0;
	const {
		palette,
		root
	} = make({
		get: async () => {
			attempts += 1;
			if (attempts === 1) throw new ApiError(0, "Cannot reach the server.");
			return {
				commands: catalog
			};
		}
	});
	await palette.load();
	assert.equal(root.querySelector(".cmd-error")?.querySelector(".w-notice")?.textContent, "Cannot reach the server.");
	root.querySelector(".cmd-retry")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(root.querySelectorAll(".cmd").length, 4);
});
