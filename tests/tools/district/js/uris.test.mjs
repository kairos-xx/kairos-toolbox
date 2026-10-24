/** Tests for the URI screen: create, copy, run, revoke and redeem. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	ApiError
} from "../../../../tools/district/web/js/api.js";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
import {
	UriScreen
} from "../../../../tools/district/web/js/uris.js";
const {
	document
} = buildSandbox();
const now = Date.parse("2026-01-01T12:00:00Z");
/** @typedef {{id:string,uri:string,command:string,title:string,values:Record<string,string>,destructive:boolean,createdAt:string,expiresAt:string,state:string}} Ticket */
/**
 * Describe a ticket.
 * @param {Partial<Ticket>} extra Fields to override.
 * @returns {Ticket} The ticket.
 */
const ticket = extra => ({
	id: "t1",
	uri: "district://request/v1/abcdefghijklmnopqrstuvwxyz0123456789.signaturesignature",
	command: "health",
	title: "Health check",
	values: {},
	destructive: false,
	createdAt: "2026-01-01T11:50:00Z",
	expiresAt: "2026-01-01T12:09:00Z",
	state: "active",
	...extra
});
/**
 * Describe a URI command parameter.
 * @param {string} name Parameter name.
 * @param {string} label Human-readable label.
 * @param {object} [extra] Fields to override.
 * @returns {object} The parameter specification.
 */
const param = (name, label, extra = {}) => ({
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
const catalog = [{
	name: "health",
	title: "Health check",
	summary: "Check this installation.",
	group: "system",
	danger: "safe",
	params: []
}, {
	name: "kill",
	title: "Stop a district",
	summary: "End a district.",
	group: "act",
	danger: "destructive",
	params: [param("session", "District", {
		kind: "session",
		required: true
	})]
}];
/**
 * Build the screen over a fake API.
 * @param {object[]} items Tickets the server holds.
 * @param {object} [overrides] Replacement handlers.
 * @returns {{screen:UriScreen,root:import("./sandbox-dom.mjs").FakeElement,posts:{path:string,body:any}[],copies:string[]}} The screen and its logs.
 */
const make = (items, overrides = {}) => {
	/** @type {{path:string,body:any}[]} */
	const posts = [];
	/** @type {string[]} */
	const copies = [];
	const api = {
		get: async (/** @type {string} */ path) => path === "/api/commands" ? {
			commands: catalog
		} : {
			items
		},
		post: async (/** @type {string} */ path, /** @type {object} */ body) => {
			posts.push({
				path,
				body
			});
			if (path.endsWith("/run") || path.endsWith("/redeem")) return {
				command: "health",
				title: "Health check",
				ok: true,
				widgets: [{
					type: "notice",
					tone: "good",
					text: "fine"
				}]
			};
			return ticket({});
		},
		...overrides
	};
	const root = document.createElement("section");
	const screen = new UriScreen(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), /** @type {any} */ (api), () => now, async text => {
		copies.push(text);
		return true;
	});
	return {
		screen,
		root,
		posts,
		copies
	};
};
/**
 * Let pending asynchronous work settle.
 * @returns {Promise<void>} Resolves on the next timer turn.
 */
const settle = () => new Promise(resolve => {
	setTimeout(resolve, 0);
});
/**
 * Find a button by its words inside a node.
 * @param {import("./sandbox-dom.mjs").FakeElement} scope Where to look.
 * @param {string} label The button's text.
 * @returns {import("./sandbox-dom.mjs").FakeElement|undefined} The button.
 */
const button = (scope, label) => scope.querySelectorAll("button").find(candidate => candidate.textContent === label);
test("each ticket shows what it does, whether it still works, and for how long", async () => {
	const {
		screen,
		root
	} = make([ticket({
		values: {
			session: "ab12cd"
		}
	})]);
	await screen.refresh(true);
	assert.equal(root.querySelector(".uri-name")?.textContent, "Health check");
	assert.equal(root.querySelector(".uri-state")?.textContent, "Active");
	assert.equal(root.querySelector(".uri-state")?.getAttribute("data-state"), "active");
	assert.equal(root.querySelector(".uri-values")?.textContent, "session ab12cd");
	assert.equal(root.querySelector(".uri-when")?.textContent, "expires in 9m");
});
test("the long URI is shortened to something that fits a phone", async () => {
	const {
		screen,
		root
	} = make([ticket({})]);
	await screen.refresh(true);
	const shown = root.querySelector(".uri-text")?.textContent ?? "";
	assert.ok(shown.length < 60, shown);
	assert.ok(shown.startsWith("district://"), shown);
	assert.ok(shown.includes("…"), shown);
});
test("an empty list invites the first URI instead of showing nothing", async () => {
	const {
		screen,
		root
	} = make([]);
	await screen.refresh(true);
	assert.equal(root.querySelector(".uri-none")?.textContent, "No URIs yet. Create one to share a one-tap action.");
});
test("only an active ticket can be run or revoked", async () => {
	const {
		screen,
		root
	} = make([ticket({
		id: "a"
	}), ticket({
		id: "b",
		state: "used"
	}), ticket({
		id: "c",
		state: "expired",
		expiresAt: "2026-01-01T11:57:00Z"
	}), ticket({
		id: "d",
		state: "revoked"
	})]);
	await screen.refresh(true);
	const cards = root.querySelectorAll(".uri-card");
	assert.deepEqual(cards.map(card => button(card, "Run") !== undefined), [true, false, false, false]);
	assert.deepEqual(cards.map(card => button(card, "Revoke") !== undefined), [true, false, false, false]);
	assert.equal(cards[2]?.querySelector(".uri-when")?.textContent, "expired 3m ago");
});
test("copying hands over the whole URI and confirms in words", async () => {
	const {
		screen,
		root,
		copies
	} = make([ticket({})]);
	await screen.refresh(true);
	const copy = button(root, "Copy");
	copy?.dispatchEvent(new Event("click"));
	await settle();
	assert.deepEqual(copies, [ticket({}).uri]);
	assert.equal(copy?.textContent, "Copied");
});
test("when the browser will not copy, the whole URI is shown to select by hand", async () => {
	const {
		root
	} = make([ticket({})]);
	const refusing = new UriScreen(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), /** @type {any} */ ({
		get: async () => ({
			items: [ticket({})]
		}),
		post: async () => ({})
	}), () => now, async () => false);
	await refusing.refresh(true);
	button(root, "Copy")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(root.querySelector(".uri-full")?.textContent, ticket({}).uri);
});
test("running a ticket shows its result as widgets and refreshes the list", async () => {
	const {
		screen,
		root,
		posts
	} = make([ticket({})]);
	await screen.refresh(true);
	button(root, "Run")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(posts[0]?.path, "/api/uris/t1/run");
	assert.equal(root.querySelector(".w-notice")?.textContent, "fine");
});
test("a ticket that cannot run explains why", async () => {
	const {
		screen,
		root
	} = make([ticket({})], {
		post: async () => {
			throw new ApiError(409, "This URI is used and cannot run.");
		}
	});
	await screen.refresh(true);
	button(root, "Run")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(root.querySelector(".uri-result")?.querySelector(".w-notice")?.textContent, "This URI is used and cannot run.");
});
test("revoking cancels the ticket on the server", async () => {
	const {
		screen,
		root,
		posts
	} = make([ticket({})]);
	await screen.refresh(true);
	button(root, "Revoke")?.dispatchEvent(new Event("click"));
	await settle();
	assert.equal(posts[0]?.path, "/api/uris/t1/revoke");
});
test("creating a URI asks for the command's input, its lifetime, and sends them", async () => {
	const {
		screen,
		root,
		posts
	} = make([]);
	await screen.refresh(true);
	button(root, "New URI")?.dispatchEvent(new Event("click"));
	await settle();
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	const command = sheet.querySelector(".u-command");
	assert.ok(command !== null);
	assert.deepEqual(command.querySelectorAll("option").map(option => option.textContent), ["Health check", "Stop a district"]);
	command.value = "health";
	command.dispatchEvent(new Event("change"));
	const ttl = sheet.querySelector(".u-ttl");
	assert.ok(ttl !== null);
	ttl.value = "3600";
	button(sheet, "Create URI")?.dispatchEvent(new Event("click"));
	await settle();
	assert.deepEqual(posts[0], {
		path: "/api/uris",
		body: {
			command: "health",
			values: {},
			ttlSeconds: 3600,
			confirmed: false
		}
	});
	assert.equal(sheet.getAttribute("open"), null);
});
test("a URI for a destructive command is only made after an explicit tick", async () => {
	const {
		screen,
		root,
		posts
	} = make([]);
	await screen.refresh(true);
	button(root, "New URI")?.dispatchEvent(new Event("click"));
	await settle();
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	const command = sheet.querySelector(".u-command");
	assert.ok(command !== null);
	command.value = "kill";
	command.dispatchEvent(new Event("change"));
	const confirm = sheet.querySelector(".u-confirm");
	assert.ok(confirm !== null);
	assert.equal(confirm.hidden, false);
	button(sheet, "Create URI")?.dispatchEvent(new Event("click"));
	await settle();
	assert.deepEqual(posts, []);
	assert.equal(sheet.querySelector(".sheet-error")?.textContent, "District is required.");
});
test("a pasted URI is redeemed and its result shown", async () => {
	const {
		screen,
		root,
		posts
	} = make([]);
	await screen.refresh(true);
	button(root, "Paste a URI")?.dispatchEvent(new Event("click"));
	const sheet = root.querySelector(".sheet");
	assert.ok(sheet !== null);
	const paste = sheet.querySelector(".u-paste");
	assert.ok(paste !== null);
	paste.value = "  district://request/v1/x.y  ";
	button(sheet, "Redeem")?.dispatchEvent(new Event("click"));
	await settle();
	assert.deepEqual(posts[0], {
		path: "/api/uris/redeem",
		body: {
			uri: "district://request/v1/x.y"
		}
	});
	assert.equal(root.querySelector(".uri-result")?.querySelector(".w-notice")?.textContent, "fine");
});
test("watching the screen refreshes at most every few seconds", async () => {
	let reads = 0;
	const {
		screen
	} = make([], {
		get: async () => {
			reads += 1;
			return {
				items: []
			};
		}
	});
	await screen.show();
	await screen.show();
	assert.equal(reads, 1);
	screen.clock = () => now + UriScreen.every + 1;
	await screen.show();
	assert.equal(reads, 2);
});
test("if the list cannot be read the screen says so", async () => {
	const {
		screen,
		root
	} = make([], {
		get: async () => {
			throw new ApiError(501, "URI support needs the bowman package.");
		}
	});
	await screen.refresh(true);
	assert.equal(root.querySelector(".uri-none")?.textContent, "URI support needs the bowman package.");
});
