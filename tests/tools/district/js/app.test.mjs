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
} = await import("../../../../tools/district/web/js/app.js");
const {
	Route
} = await import("../../../../tools/district/web/js/route.js");
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
/**
 * Install the dashboard document fixture used by dashboard tests.
 * @returns {void} Nothing.
 */
const mountDashboard = () => {
	document.body.replaceChildren();
	/** @param {string} id Element id. @param {string} [tag] Tag. @returns {FakeElement} The element, mounted. */
	const mount = (id, tag = "div") => {
		const node = element(tag);
		node.id = id;
		document.body.append(node);
		return node;
	};
	for (const id of ["connection-label", "server-version", "server-uptime", "updated-ago", "view-title", "view-sub", "crumb", "stats", "filters", "scope", "agent-list", "overview-activity", "overview-commands", "session-list", "empty-state", "empty-text", "empty-clear", "agent-sessions", "agent-activity", "agent-commands", "sessions", "missing", "session-activity", "session-commands", "activity-feed", "command-feed", "toasts"]) mount(id);
	for (const name of ["sessions", "active", "idle", "dead", "cpu", "mem", "procs", "consumers"]) {
		const tile = element("a", "stat");
		const value = element("span");
		value.id = `stat-${name}`;
		tile.append(value);
		document.body.append(tile);
	}
	mount("server-mode");
	for (const id of ["filter", "agent-filter", "state-filter", "sort"]) {
		const node = mount(id, "input");
		/** @type {{value:string}} */
		( /** @type {unknown} */ (node)).value = "";
	}
	for (const name of ["overview", "sessions", "agent", "session", "activity", "commands"]) {
		const view = element("section", "view");
		/** @type {{view?:string}} */
		(view.dataset).view = name;
		document.body.append(view);
	}
	for (const name of ["overview", "sessions", "activity", "commands"]) {
		const tab = element("a", "tab-link");
		/** @type {{tab?:string}} */
		(tab.dataset).tab = name;
		document.body.append(tab);
	}
	document.body.append(element("div", "connection"));
	const template = /** @type {FakeElement & {content: FakeElement}} */ ( /** @type {unknown} */ (document.createElement("template")));
	template.id = "session-template";
	const card = element("article", "session-card");
	/** @type {Array<[string, string]>} */
	const parts = [
		["div", "agent"],
		["div", "state"],
		["div", "metadata"],
		["span", "badge-age"],
		["span", "badge-clients"],
		["span", "badge-provider"],
		["span", "badge-editor"],
		["meter", "cpu-meter"],
		["span", "cpu-value"],
		["span", "rss-value"],
		["div", "io-text"],
		["div", "cpu-spark"],
		["div", "rss-spark"],
		["div", "ports"],
		["code", "command"],
		["div", "resources"],
		["pre", "process-tree"],
		["pre", "terminal"],
		["details", "more"],
		["button", "copy"],
		["button", "view"],
		["button", "history"],
		["button", "attach"],
		["button", "attach-command"],
		["button", "kill"],
		["form", "exec"],
		["input", "exec-input"],
		["button", "exec-run"],
		["pre", "exec-output"],
		["form", "type"],
		["input", "type-input"],
		["button", "type-send"],
		["div", "type-keys"],
		["button", "key-enter"],
		["button", "key-escape"],
		["button", "key-tab"],
		["button", "key-interrupt"],
		["button", "key-up"],
		["button", "key-down"]
	];
	parts.forEach(([tag, className]) => {
		card.append(element(tag, className));
	});
	template.content.append(card);
	document.body.append(template);
};
/**
 * Create a dashboard instance with isolated mutable test state.
 * @param {string} [hash] Location hash used to initialize the route.
 * @returns {InstanceType<typeof Dashboard>} Test dashboard.
 */
const dashboard = (hash = "#/session/abc123") => {
	const instance = /** @type {InstanceType<typeof Dashboard>} */ (Object.create(Dashboard.prototype));
	instance.cards = new Map();
	instance.sessions = [];
	instance.socket = null;
	instance.receivedAt = Date.now();
	instance.place = Route.parse(hash);
	instance.payload = null;
	return instance;
};
test("the session screen renders server metadata and the full card", async () => {
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
				command: "district abc123",
				output: "hello\n",
				exitStatus: 0
			})
		});
	});
	const instance = dashboard();
	instance.update(payload({
		sessions: [session()]
	}));
	assert.equal(document.querySelector("#server-version")?.textContent, "v1.0.0");
	assert.equal(document.querySelector("#server-uptime")?.textContent, "up 2m");
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
	assert.equal( /** @type {FakeElement & {hidden:boolean}} */ ( /** @type {unknown} */ (document.querySelector("#missing"))).hidden, true);
	card.querySelector(".copy")?.dispatchEvent(new Event("click"));
	await Promise.resolve();
	assert.equal(clipboard.pop(), "pytest");
	assert.equal(card.querySelector(".copy")?.textContent, "Copied");
	card.querySelector(".view")?.dispatchEvent(new Event("click"));
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	assert.equal(requests.pop(), "/api/sessions/abc123/view");
	assert.equal(clipboard.pop(), "district abc123");
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
/**
 * Return the mounted child rows matching a selector.
 * @param {string} selector Selector.
 * @returns {FakeElement[]} Matching element children.
 */
const rows = selector => /** @type {FakeElement[]} */ ( /** @type {unknown} */ (document.querySelector(selector)?.children ?? []));
/**
 * Return the mounted fixture element matching a selector.
 * @param {string} selector Selector.
 * @returns {FakeElement} Matching mounted element.
 */
const mounted = selector => /** @type {FakeElement} */ (document.querySelector(selector));
test("the sessions screen filters, sorts and keeps a row for a session across refreshes", () => {
	mountDashboard();
	const instance = dashboard("#/sessions");
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
	assert.deepEqual(rows("#session-list").map(row => row.getAttribute("data-key")), ["busy1", "quiet1"]);
	const first = rows("#session-list")[0];
	instance.update(payload({
		sessions: [session({
			...busy,
			cpuPercent: 91
		}), quiet]
	}));
	assert.equal(rows("#session-list")[0], first, "the row is reused, not rebuilt");
	assert.match(first?.children[0]?.children[4]?.textContent ?? "", /^91\.0% · 1\.0 MiB · 1 proc$/u);
	instance.place = Route.parse("#/sessions?agent=devin");
	instance.renderAll();
	assert.deepEqual(rows("#session-list").map(row => row.getAttribute("data-key")), ["busy1"]);
	instance.place = Route.parse("#/sessions?sort=cpu");
	instance.renderAll();
	assert.deepEqual(rows("#session-list").map(row => row.getAttribute("data-key")), ["busy1", "quiet1"]);
	instance.place = Route.parse("#/sessions?state=IDLE");
	instance.renderAll();
	assert.equal(rows("#session-list").length, 0);
	assert.equal(mounted("#empty-state").hidden, false);
	assert.equal(mounted("#empty-clear").hidden, false);
	assert.equal(mounted("#view-sub").textContent, "0 of 2");
});
test("a state filter chip and the live state match what the totals count", () => {
	mountDashboard();
	const instance = dashboard("#/sessions?state=live&agent=devin&q=pytest");
	instance.update(payload({
		sessions: [session({
			id: "a",
			agent: "devin",
			state: "EXECUTING"
		}), session({
			id: "b",
			agent: "devin",
			state: "IDLE"
		})]
	}));
	assert.deepEqual(rows("#session-list").map(row => row.getAttribute("data-key")), ["a"]);
	const chips = rows("#scope");
	assert.deepEqual(chips.map(chip => chip.textContent), ["agent devin ×", "state live ×", "search pytest ×", "Clear all"]);
	assert.equal(chips[0]?.href, "#/sessions?q=pytest&state=live");
	assert.equal(mounted("#scope").hidden, false);
});
test("the overview totals, agents and recent activity all link onward", () => {
	mountDashboard();
	const instance = dashboard("#/");
	instance.update(payload({
		sessions: [session({
			id: "a",
			agent: "devin"
		}), session({
			id: "b",
			agent: "codex",
			state: "DEAD"
		})],
		totals: {
			sessions: 2,
			active: 1,
			idle: 0,
			dead: 1,
			cpuPercent: 25,
			rssBytes: 2097152,
			processes: 2,
			clients: 2
		},
		events: [{
			id: "e1",
			event_type: "COMMAND_STARTED",
			occurred_at: "2026-01-02T03:04:05+00:00",
			agent: "devin",
			agent_session: "dv",
			session_id: "a",
			state: "",
			command: "pytest",
			exit_code: null,
			duration_seconds: 0
		}],
		commands: [{
			command_id: "c1",
			session_id: "a",
			agent: "devin",
			command: "pytest -q",
			started_at: "2026-01-02T03:04:05+00:00",
			finished_at: "2026-01-02T03:04:07+00:00",
			exit_code: 0,
			duration_seconds: 2.25
		}]
	}));
	assert.equal(mounted("#view-title").textContent, "Overview");
	assert.equal(mounted("#stats").hidden, false);
	assert.equal(mounted("#filters").hidden, true);
	assert.equal(mounted("#stat-active").textContent, "1");
	assert.equal(mounted("#stat-active").parentNode?.href, "#/sessions?state=live");
	assert.equal(mounted("#stat-dead").parentNode?.href, "#/sessions?state=DEAD");
	assert.equal(mounted("#stat-mem").parentNode?.href, "#/sessions?sort=memory");
	assert.deepEqual(rows("#agent-list").map(row => row.getAttribute("data-key")), ["codex", "devin"]);
	assert.equal(rows("#agent-list")[1]?.children[0]?.href, "#/agent/devin");
	const event = rows("#overview-activity")[0];
	assert.match(event?.textContent ?? "", /03:04:05.*command started.*devin · a.*pytest/u);
	assert.equal(event?.children[2]?.children[0]?.href, "#/agent/devin");
	assert.equal(event?.children[2]?.children[2]?.href, "#/session/a");
	const command = rows("#overview-commands")[0];
	assert.equal(command?.children[0]?.textContent, "pytest -q");
	assert.match(command?.children[1]?.textContent ?? "", /03:04:05.*devin · a.*exit 0.*2\.3s/u);
	assert.equal(command?.getAttribute("data-exit"), "ok");
	assert.equal(document.querySelectorAll(".tab-link").filter(tab => tab.ariaCurrent === "page").map(tab => tab.getAttribute("data-tab")).join(), "overview");
});
test("an agent screen scopes its totals, sessions, activity and commands", () => {
	mountDashboard();
	const instance = dashboard("#/agent/devin");
	instance.update(payload({
		sessions: [session({
			id: "a",
			agent: "devin",
			cpuPercent: 10
		}), session({
			id: "b",
			agent: "devin",
			state: "IDLE",
			cpuPercent: 5
		}), session({
			id: "c",
			agent: "codex"
		})],
		events: [{
			id: "e1",
			event_type: "COMMAND_STARTED",
			occurred_at: "2026-01-02T03:04:05+00:00",
			agent: "codex",
			agent_session: "cx",
			session_id: "c",
			state: "",
			command: "ls",
			exit_code: null,
			duration_seconds: 0
		}]
	}));
	assert.equal(mounted("#view-title").textContent, "devin");
	assert.equal(mounted("#view-sub").textContent, "2 sessions");
	assert.equal(mounted("#stat-sessions").textContent, "2");
	assert.equal(mounted("#stat-active").textContent, "1");
	assert.equal(mounted("#stat-cpu").textContent, "15.0%");
	assert.equal(mounted("#stat-active").parentNode?.href, "#/sessions?state=live&agent=devin");
	assert.deepEqual(rows("#agent-sessions").map(row => row.getAttribute("data-key")), ["a", "b"]);
	assert.equal(rows("#agent-activity").length, 0, "another agent's events are out of scope");
	assert.equal(mounted("#crumb").children[0]?.href, "#/");
});
test("a session screen shows one card, its breadcrumb, and a notice once it ends", () => {
	mountDashboard();
	const instance = dashboard("#/session/a");
	instance.update(payload({
		sessions: [session({
			id: "a",
			agent: "devin"
		}), session({
			id: "b",
			agent: "codex"
		})]
	}));
	assert.deepEqual([...instance.cards.keys()], ["a"]);
	assert.equal(mounted("#missing").hidden, true);
	assert.equal(mounted("#view-title").hidden, true);
	assert.equal(mounted("#crumb").children[2]?.href, "#/agent/devin");
	const agentCard = instance.cards.get("a");
	assert.ok(agentCard);
	const agentElement = agentCard.card.querySelector(".agent");
	assert.ok(agentElement);
	const sessionLink = agentElement.children[2];
	assert.ok(sessionLink);
	assert.equal( /** @type {HTMLAnchorElement} */ (sessionLink).href, "#/session/a");
	instance.update(payload({
		sessions: [session({
			id: "b",
			agent: "codex"
		})]
	}));
	assert.deepEqual([...instance.cards.keys()], []);
	assert.equal(mounted("#missing").hidden, false);
	assert.equal(mounted("#crumb").children[2]?.href, "#/agent/devin", "the breadcrumb survives the session ending");
});
test("activity and command screens list every record, newest first, and reuse rows", () => {
	mountDashboard();
	const instance = dashboard("#/activity");
	/**
	 * Build an activity event fixture.
	 * @param {string} id Event identifier.
	 * @param {string} time Event occurrence timestamp.
	 * @returns {ActivityEvent} Activity event fixture.
	 */
	const event = (id, time) => ({
		id,
		event_type: "COMMAND_STARTED",
		occurred_at: time,
		agent: "devin",
		agent_session: "dv",
		session_id: "abc123",
		state: "",
		command: `cmd ${id}`,
		exit_code: null,
		duration_seconds: 0
	});
	instance.update(payload({
		events: [event("e1", "2026-01-02T03:04:05+00:00"), event("e2", "2026-01-02T03:04:06+00:00")]
	}));
	assert.deepEqual(rows("#activity-feed").map(row => row.getAttribute("data-key")), ["e2", "e1"]);
	const kept = rows("#activity-feed")[0];
	instance.update(payload({
		events: [event("e1", "2026-01-02T03:04:05+00:00"), event("e2", "2026-01-02T03:04:06+00:00"), event("e3", "2026-01-02T03:04:07+00:00")]
	}));
	assert.deepEqual(rows("#activity-feed").map(row => row.getAttribute("data-key")), ["e3", "e2", "e1"]);
	assert.equal(rows("#activity-feed")[1], kept);
	instance.place = Route.parse("#/activity?q=e1");
	instance.renderAll();
	assert.deepEqual(rows("#activity-feed").map(row => row.getAttribute("data-key")), ["e1"]);
	instance.place = Route.parse("#/commands");
	instance.update(payload({
		commands: [{
			command_id: "c1",
			session_id: "abc123",
			agent: "devin",
			command: "make",
			started_at: "2026-01-02T03:04:05+00:00",
			finished_at: "",
			exit_code: null,
			duration_seconds: null
		}]
	}));
	const running = rows("#command-feed")[0];
	assert.equal(running?.getAttribute("data-exit"), "running");
	instance.update(payload({
		commands: [{
			command_id: "c1",
			session_id: "abc123",
			agent: "devin",
			command: "make",
			started_at: "2026-01-02T03:04:05+00:00",
			finished_at: "2026-01-02T03:04:07+00:00",
			exit_code: 2,
			duration_seconds: 2
		}]
	}));
	assert.equal(rows("#command-feed")[0], running);
	assert.equal(running?.getAttribute("data-exit"), "failed");
	assert.match(running?.children[1]?.textContent ?? "", /exit 2.*2\.0s/u);
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
test("dashboard says so when the server never accepted this browser", () => {
	mountDashboard();
	Dashboard.connection(false);
	Dashboard.unpaired();
	assert.equal(document.querySelector("#connection-label")?.textContent, "Not paired — scan the QR code");
});
