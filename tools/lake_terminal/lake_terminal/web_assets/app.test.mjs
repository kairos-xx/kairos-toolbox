/** Behavioral tests for the live dashboard and ANSI terminal. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
/** @typedef {import("./sandbox-dom.mjs").FakeElement} FakeElement */
const {
	document
} = buildSandbox();
const {
	AnsiTerminal,
	Dashboard,
	Format,
	Sparkline
} = await import("./app.js");
/**
 * Handle output.
 * @returns {HTMLElement} Test output element.
 */
const output = () => /** @type {HTMLElement} */ ( /** @type {unknown} */ (document.createElement("pre")));
/**
 * Handle text.
 * @param {ArrayLike<{textContent:string|null}>} nodes Rendered nodes.
 * @returns {string} Text.
 */
const text = nodes => Array.from(nodes).map(node => node.textContent ?? "").join("");
test("SGR styles accumulate, replace foreground, and reset", () => {
	assert.deepEqual(AnsiTerminal.sgr([], [1, 2, 3, 4, 31]), ["ansi-bold", "ansi-dim", "ansi-italic", "ansi-underline", "ansi-fg-red", ]);
	assert.deepEqual(AnsiTerminal.sgr(["ansi-bold", "ansi-fg-blue"], [32]), ["ansi-bold", "ansi-fg-green", ]);
	assert.deepEqual(AnsiTerminal.sgr(["ansi-bold", "ansi-fg-blue"], [39]), ["ansi-bold"]);
	assert.deepEqual(AnsiTerminal.sgr(["ansi-bold"], [0]), []);
});
test("ANSI renderer handles cursor movement, erase, carriage return, and backspace", () => {
	assert.equal(text(AnsiTerminal.nodes("abc\rZ")), "Zbc");
	assert.equal(text(AnsiTerminal.nodes("abc\bZ")), "abZ");
	assert.equal(text(AnsiTerminal.nodes("abc\x1b[2DXY")), "aXY");
	assert.equal(text(AnsiTerminal.nodes("one\ntwo\x1b[1A\x1b[1GZ")), "Zne\ntwo");
	assert.equal(text(AnsiTerminal.nodes("abcdef\x1b[3G\x1b[KZ")), "abZ");
	assert.equal(text(AnsiTerminal.nodes("old\x1b[2Jnew")), "new");
});
test("ANSI renderer saves cursor and creates safe OSC-8 anchors", () => {
	const nodes = AnsiTerminal.nodes("ab\x1b[sCD\x1b[uZ");
	assert.equal(text(nodes), "abZD");
	const linked = AnsiTerminal.nodes("\x1b]8;;https://example.test\x07go\x1b]8;;\x07");
	const anchor = linked[0];
	assert.ok(anchor instanceof HTMLAnchorElement);
	assert.equal( /** @type {HTMLAnchorElement} */ (anchor).href, "https://example.test");
	assert.equal( /** @type {HTMLAnchorElement} */ (anchor).rel, "noreferrer");
	assert.equal( /** @type {HTMLAnchorElement} */ (anchor).target, "_blank");
});
test("terminal render replaces children and avoids redundant state ambiguity", () => {
	const element = output();
	const terminal = new AnsiTerminal(element);
	terminal.render("\x1b[31mred\x1b[0m");
	assert.equal(terminal.text, "\x1b[31mred\x1b[0m");
	assert.equal(text( /** @type {HTMLElement} */ ( /** @type {unknown} */ (element)).children), "red");
	assert.equal(element.children[0]?.className, "ansi-fg-red");
});
test("mib formats byte counts", () => {
	assert.equal(Dashboard.mib(0), "0.0");
	assert.equal(Dashboard.mib(1572864), "1.5");
});
test("Format renders ages, clocks, and durations", () => {
	assert.equal(Format.age(45), "45s");
	assert.equal(Format.age(130), "2m");
	assert.equal(Format.age(3750), "1h 2m");
	assert.equal(Format.clock("2026-01-02T03:04:05+00:00"), "03:04:05");
	assert.equal(Format.clock("not-a-date"), "—");
	assert.equal(Format.duration(null), "—");
	assert.equal(Format.duration(0.42), "420ms");
	assert.equal(Format.duration(2.75), "2.8s");
});
test("Sparkline renders Unicode level bars", () => {
	const element = output();
	Sparkline.render( /** @type {HTMLElement} */ ( /** @type {unknown} */ (element)), [0, 5, 10]);
	assert.equal(element.textContent, "▁▅█");
	Sparkline.render( /** @type {HTMLElement} */ ( /** @type {unknown} */ (element)), []);
	assert.equal(element.textContent, "");
});
/**
 * Handle element.
 * @param {string} tag Tag. @param {string} className Class.
 * @returns {FakeElement} Element.
 * @param {string} className className.
 */
const element = (tag, className = "") => {
	const node = document.createElement(tag);
	node.className = className;
	return node;
};
/** @typedef {{id:string,agent:string,agentSession:string,agentState:string,state:string,command:string,cwd:string,surface:string,editor:string,backend:string,workspace:string,attribution:string,hostId:string,output:string,clients:number,ageSeconds:number,lastActivityAt:string,reconnectable:boolean,processCount:number,cpuPercent:number,rssBytes:number,vmsBytes:number,readBytes:number,writeBytes:number,ports:number[],connections:string[],networkBytesSupported:boolean,partialResources:boolean,processes:ProcessSnapshot[],cpuHistory:number[],rssHistory:number[]}} SessionSnapshot */
/** @typedef {{pid:number,parentPid:number,name:string,command:string,cpuPercent:number,rssBytes:number,readBytes:number,writeBytes:number,ports:number[]}} ProcessSnapshot */
/**
 * Handle session.
 * @param {Partial<SessionSnapshot>} overrides Overrides.
 * @returns {SessionSnapshot} Session.
 */
const session = (overrides = {}) => ({
	id: "abc123",
	agent: "codex",
	agentSession: "cx",
	agentState: "EXECUTING",
	state: "EXECUTING",
	command: "pytest",
	cwd: "/tmp/project",
	surface: "editor",
	editor: "vscode",
	backend: "tmux",
	workspace: "/tmp/project",
	attribution: "hook",
	hostId: "host-1",
	output: "\x1b[32mok\x1b[0m",
	clients: 1,
	ageSeconds: 2,
	lastActivityAt: "2026-01-02T03:04:05+00:00",
	reconnectable: true,
	processCount: 1,
	cpuPercent: 12.5,
	rssBytes: 1048576,
	vmsBytes: 2097152,
	readBytes: 524288,
	writeBytes: 262144,
	ports: [8000],
	connections: ["127.0.0.1:8000 LISTEN"],
	networkBytesSupported: false,
	partialResources: false,
	cpuHistory: [1, 5, 12.5],
	rssHistory: [100, 200, 300],
	processes: [{
		pid: 10,
		parentPid: 1,
		name: "python",
		command: "pytest",
		cpuPercent: 12.5,
		rssBytes: 1048576,
		readBytes: 1,
		writeBytes: 2,
		ports: [8000]
	}],
	...overrides,
});
/** @typedef {{mode:string,consumers:number,version:string,pid:number,startedAt:string,uptimeSeconds:number,sampleMs:number}} ServerMeta */
/** @typedef {{sessions:number,active:number,idle:number,dead:number,cpuPercent:number,rssBytes:number,processes:number,clients:number}} FleetTotals */
/** @typedef {{id:string,event_type:string,occurred_at:string,agent:string,agent_session:string,session_id:string,state:string,command:string,exit_code:number|null,duration_seconds:number}} ActivityEvent */
/** @typedef {{command_id:string,session_id:string,agent:string,command:string,started_at:string,finished_at:string,exit_code:number|null,duration_seconds:number|null}} CommandRecord */
/** @typedef {{sessions:SessionSnapshot[],server:ServerMeta,totals:FleetTotals,events:ActivityEvent[],commands:CommandRecord[]}} DashboardPayload */
/**
 * Handle payload.
 * @param {Partial<DashboardPayload>} overrides Overrides.
 * @returns {DashboardPayload} Payload.
 */
const payload = (overrides = {}) => ({
	sessions: [],
	server: {
		mode: "LIVE",
		consumers: 1,
		version: "1.0.0",
		pid: 4242,
		startedAt: "2026-01-02T03:00:00+00:00",
		uptimeSeconds: 130,
		sampleMs: 500
	},
	totals: {
		sessions: 1,
		active: 1,
		idle: 0,
		dead: 0,
		cpuPercent: 12.5,
		rssBytes: 1048576,
		processes: 3,
		clients: 1
	},
	events: [],
	commands: [],
	...overrides,
});
/** Install the dashboard document fixture. @returns {void} */
const mountDashboard = () => {
	document.body.replaceChildren();
	/** @type {string[]} */
	const ids = [
		"sessions", "connection-label", "server-version", "server-uptime", "updated-ago", "server-mode",
		"stat-sessions", "stat-active", "stat-idle", "stat-dead", "stat-cpu", "stat-mem", "stat-procs", "stat-consumers",
		"activity-feed", "command-history", "toasts", "empty-state"
	];
	for (const id of ids) {
		const node = element("div");
		node.id = id;
		document.body.append(node);
	}
	for (const id of ["filter", "state-filter", "sort"]) {
		const node = element("input");
		node.id = id;
		/** @type {{value:string}} */ ( /** @type {unknown} */ (node)).value = "";
		document.body.append(node);
	}
	document.body.append(element("div", "connection"));
	const template = /** @type {FakeElement & {content: FakeElement}} */ ( /** @type {unknown} */ (document.createElement("template")));
	template.id = "session-template";
	const card = element("article", "session-card");
	/** @type {Array<[string, string]>} */
	const parts = [
		["div", "agent"], ["div", "state"], ["div", "metadata"],
		["span", "badge-age"], ["span", "badge-clients"], ["span", "badge-provider"], ["span", "badge-editor"],
		["meter", "cpu-meter"], ["span", "cpu-value"], ["span", "rss-value"], ["div", "io-text"],
		["div", "cpu-spark"], ["div", "rss-spark"], ["div", "ports"],
		["code", "command"], ["div", "resources"], ["pre", "process-tree"], ["pre", "terminal"],
		["details", "more"], ["button", "copy"], ["button", "view"], ["button", "history"], ["button", "attach"], ["button", "kill"],
		["form", "exec"], ["input", "exec-input"], ["button", "exec-run"], ["pre", "exec-output"],
		["form", "type"], ["input", "type-input"], ["button", "type-send"], ["div", "type-keys"],
		["button", "key-enter"], ["button", "key-escape"], ["button", "key-tab"],
		["button", "key-interrupt"], ["button", "key-up"], ["button", "key-down"]
	];
	parts.forEach(([tag, className]) => {
		card.append(element(tag, className));
	});
	template.content.append(card);
	document.body.append(template);
};
/**
 * Handle dashboard.
 * @returns {InstanceType<typeof Dashboard>} Test dashboard.
 */
const dashboard = () => {
	const instance = /** @type {InstanceType<typeof Dashboard>} */ (Object.create(Dashboard.prototype));
	instance.cards = new Map();
	instance.sessions = [];
	instance.socket = null;
	instance.receivedAt = Date.now();
	return instance;
};
test("dashboard renders stats, server metadata, and card widgets", async () => {
	mountDashboard();
	/** @type {string[]} */
	const clipboard = [];
	Object.defineProperty(globalThis, "navigator", {
		configurable: true,
		value: {
			clipboard: {
				writeText: async ( /** @type {string} */ value) => {
					clipboard.push(value);
				}
			}
		}
	});
	/** @type {string[]} */
	const requests = [];
	globalThis.fetch = /** @type {typeof fetch} */ (async input => {
		requests.push(String(input));
		return /** @type {Response} */ ({
			ok: true,
			json: async () => ({
				command: "lake abc123",
				output: "hello\n",
				exitStatus: 0
			})
		});
	});
	const instance = dashboard();
	instance.update(payload({
		sessions: [session()]
	}));
	assert.equal(document.querySelector("#stat-sessions")?.textContent, "1");
	assert.equal(document.querySelector("#stat-active")?.textContent, "1");
	assert.equal(document.querySelector("#stat-cpu")?.textContent, "12.5%");
	assert.equal(document.querySelector("#server-version")?.textContent, "v1.0.0");
	assert.equal(document.querySelector("#server-uptime")?.textContent, "up 2m");
	assert.equal(document.querySelector("#server-mode")?.textContent, "live · 500ms");
	const card = /** @type {FakeElement} */ ( /** @type {unknown} */ (instance.cards.get("abc123")?.card));
	assert.ok(card);
	assert.equal(card.querySelector(".badge-age")?.textContent, "age 2s");
	assert.equal(card.querySelector(".badge-clients")?.textContent, "1 client");
	assert.equal(card.querySelector(".badge-provider")?.textContent, "codex@cx");
	assert.equal(card.querySelector(".cpu-value")?.textContent, "12.5%");
	assert.equal(card.querySelector(".rss-value")?.textContent, "1.0 MiB");
	assert.equal( /** @type {FakeElement & {value:number}} */ ( /** @type {unknown} */ (card.querySelector(".cpu-meter"))).value, 12.5);
	assert.match(card.querySelector(".cpu-spark")?.textContent ?? "", /^[▁-█]{3}$/u);
	assert.equal(card.querySelector(".ports")?.children[0]?.textContent, ":8000");
	assert.match(card.querySelector(".resources")?.textContent ?? "", /1 processes.*1 sockets.*unavailable.*last activity 03:04:05/u);
	assert.match(card.querySelector(".process-tree")?.textContent ?? "", /python.*Sockets.*LISTEN/su);
	assert.equal( /** @type {FakeElement & {disabled:boolean}} */ ( /** @type {unknown} */ (card.querySelector(".attach"))).disabled, false);
	assert.equal( /** @type {FakeElement & {hidden:boolean}} */ ( /** @type {unknown} */ (document.querySelector("#empty-state"))).hidden, true);
	card.querySelector(".copy")?.dispatchEvent(new Event("click"));
	await Promise.resolve();
	assert.equal(clipboard.pop(), "pytest");
	assert.equal(card.querySelector(".copy")?.textContent, "Copied");
	card.querySelector(".view")?.dispatchEvent(new Event("click"));
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	assert.equal(requests.pop(), "/api/sessions/abc123/view");
	assert.equal(clipboard.pop(), "lake abc123");
	assert.equal(document.querySelector("#toasts")?.children.length, 1);
	card.querySelector(".kill")?.dispatchEvent(new Event("click"));
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	assert.equal(requests.pop(), "/api/sessions/abc123/kill");
});
test("dashboard exec posts commands and renders captured output", async () => {
	mountDashboard();
	/** @type {string[]} */
	const requests = [];
	globalThis.fetch = /** @type {typeof fetch} */ (async (input, init) => {
		requests.push(`${String(input)} ${JSON.stringify(init?.body)}`);
		return /** @type {Response} */ ({
			ok: true,
			json: async () => ({
				output: "pong\n",
				exitStatus: 0
			})
		});
	});
	const instance = dashboard();
	instance.update(payload({
		sessions: [session()]
	}));
	const card = /** @type {FakeElement} */ ( /** @type {unknown} */ (instance.cards.get("abc123")?.card));
	assert.ok(card);
	const input = /** @type {FakeElement & {value:string}} */ ( /** @type {unknown} */ (card.querySelector(".exec-input")));
	input.value = "echo pong";
	card.querySelector(".exec")?.dispatchEvent(new Event("submit"));
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	assert.match(requests.pop() ?? "", /\/api\/sessions\/abc123\/exec.*echo pong/u);
	const result = card.querySelector(".exec-output");
	assert.equal( /** @type {FakeElement & {hidden:boolean}} */ ( /** @type {unknown} */ (result)).hidden, false);
	assert.match(result?.textContent ?? "", /\$ echo pong\npong\n\[exit 0\]/u);
	assert.equal( /** @type {{dataset:{exit?:string}}} */ ( /** @type {unknown} */ (result)).dataset.exit, "0");
});
test("dashboard type posts literal text and key presses", async () => {
	mountDashboard();
	/** @type {string[]} */
	const requests = [];
	globalThis.fetch = /** @type {typeof fetch} */ (async (input, init) => {
		requests.push(`${String(input)} ${JSON.stringify(init?.body)}`);
		return /** @type {Response} */ ({
			ok: true,
			json: async () => ({
				status: "ok"
			})
		});
	});
	const instance = dashboard();
	instance.update(payload({
		sessions: [session()]
	}));
	const card = /** @type {FakeElement} */ ( /** @type {unknown} */ (instance.cards.get("abc123")?.card));
	assert.ok(card);
	const input = /** @type {FakeElement & {value:string}} */ ( /** @type {unknown} */ (card.querySelector(".type-input")));
	input.value = "y";
	card.querySelector(".type")?.dispatchEvent(new Event("submit"));
	card.querySelector(".key-interrupt")?.dispatchEvent(new Event("click"));
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	assert.match(requests[0] ?? "", /\/api\/sessions\/abc123\/send.*text.*y/u);
	assert.match(requests[1] ?? "", /\/api\/sessions\/abc123\/send.*key.*C-c/u);
	assert.equal(input.value, "");
	assert.equal(card.querySelector(".key-interrupt")?.textContent, "Sent");
});
test("dashboard exec failure raises a toast instead of failing silently", async () => {
	mountDashboard();
	globalThis.fetch = /** @type {typeof fetch} */ (async () => /** @type {Response} */ ({
		ok: false,
		status: 404,
		json: async () => ({
			detail: "Unknown session"
		})
	}));
	const instance = dashboard();
	instance.update(payload({
		sessions: [session()]
	}));
	const card = /** @type {FakeElement} */ ( /** @type {unknown} */ (instance.cards.get("abc123")?.card));
	assert.ok(card);
	const input = /** @type {FakeElement & {value:string}} */ ( /** @type {unknown} */ (card.querySelector(".exec-input")));
	input.value = "ls";
	card.querySelector(".exec")?.dispatchEvent(new Event("submit"));
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	const toasts = document.querySelector("#toasts");
	assert.equal(toasts?.children.length, 1);
	const toastItems = /** @type {FakeElement[]} */ ( /** @type {unknown} */ (toasts?.children ?? []));
	assert.match(toastItems[0]?.textContent ?? "", /Unknown session/u);
	assert.equal( /** @type {{dataset:{kind?:string}}} */ ( /** @type {unknown} */ (toastItems[0])).dataset.kind, "bad");
});
test("dashboard filters, sorts, and hides filtered-out cards", () => {
	mountDashboard();
	const instance = dashboard();
	const busy = session({
		id: "busy1",
		agent: "devin",
		cpuPercent: 90,
		ageSeconds: 10
	});
	const quiet = session({
		id: "quiet1",
		agent: "codex",
		cpuPercent: 1,
		ageSeconds: 20
	});
	instance.update(payload({
		sessions: [busy, quiet]
	}));
	assert.equal(instance.cards.size, 2);
	const filter = /** @type {FakeElement & {value:string}} */ ( /** @type {unknown} */ (document.querySelector("#filter")));
	filter.value = "devin";
	instance.renderSessions();
	assert.equal(instance.cards.size, 1);
	assert.ok(instance.cards.has("busy1"));
	filter.value = "";
	const sort = /** @type {FakeElement & {value:string}} */ ( /** @type {unknown} */ (document.querySelector("#sort")));
	sort.value = "cpu";
	instance.renderSessions();
	const grid = document.querySelector("#sessions");
	const gridCards = /** @type {FakeElement[]} */ ( /** @type {unknown} */ (grid?.children ?? []));
	assert.equal( /** @type {{dataset:{state?:string}}} */ ( /** @type {unknown} */ (gridCards[0])).dataset.state ?? "", "EXECUTING");
	const stateFilter = /** @type {FakeElement & {value:string}} */ ( /** @type {unknown} */ (document.querySelector("#state-filter")));
	stateFilter.value = "IDLE";
	instance.renderSessions();
	assert.equal(instance.cards.size, 0);
	assert.equal( /** @type {FakeElement & {hidden:boolean}} */ ( /** @type {unknown} */ (document.querySelector("#empty-state"))).hidden, false);
});
test("dashboard renders activity feed and command history", () => {
	mountDashboard();
	const instance = dashboard();
	instance.update(payload({
		events: [{
			id: "e1",
			event_type: "COMMAND_STARTED",
			occurred_at: "2026-01-02T03:04:05+00:00",
			agent: "devin",
			agent_session: "dv",
			session_id: "abc123",
			state: "",
			command: "pytest",
			exit_code: null,
			duration_seconds: 0
		}],
		commands: [{
			command_id: "c1",
			session_id: "abc123",
			agent: "devin",
			command: "pytest -q",
			started_at: "2026-01-02T03:04:05+00:00",
			finished_at: "2026-01-02T03:04:07+00:00",
			exit_code: 0,
			duration_seconds: 2.25
		}]
	}));
	const feed = document.querySelector("#activity-feed");
	assert.equal(feed?.children.length, 1);
	const feedItems = /** @type {FakeElement[]} */ ( /** @type {unknown} */ (feed?.children ?? []));
	assert.match(text( /** @type {FakeElement[]} */ ( /** @type {unknown} */ (feedItems[0]?.children ?? []))), /03:04:05.*command started.*devin.*abc123.*pytest/u);
	const body = document.querySelector("#command-history");
	assert.equal(body?.children.length, 1);
	const bodyRows = /** @type {FakeElement[]} */ ( /** @type {unknown} */ (body?.children ?? []));
	assert.match(text( /** @type {FakeElement[]} */ ( /** @type {unknown} */ (bodyRows[0]?.children ?? []))), /03:04:05.*abc123.*devin.*pytest -q.*0.*2\.3s/u);
});
test("dashboard connection indicator reflects socket state", () => {
	mountDashboard();
	Dashboard.connection(true);
	const connection = document.querySelector(".connection");
	if (connection === null) throw new Error("Missing connection");
	assert.equal( /** @type {{connected?:string}} */ (connection.dataset).connected, "true");
	assert.equal(document.querySelector("#connection-label")?.textContent, "Live");
	Dashboard.connection(false);
	assert.equal(document.querySelector("#connection-label")?.textContent, "Reconnecting");
});
