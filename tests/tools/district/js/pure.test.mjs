/** Tests for the routing, filtering and console layout logic, which need no DOM. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	Filters
} from "../../../../tools/district/web/js/filters.js";
import {
	Route
} from "../../../../tools/district/web/js/route.js";
import {
	Console,
	Keyboard,
	Layout,
	Tabs,
	Transcript
} from "../../../../tools/district/web/js/console.js";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
const {
	document
} = buildSandbox();
/** @typedef {import("./sandbox-dom.mjs").FakeElement} FakeElement */
/** @typedef {{command_id: string, session_id: string, finished_at: string, exit_code: number | null}} Run */
/**
 * Build a session fixture with stable defaults.
 * @param {Partial<import("../../../../tools/district/web/js/filters.js").ScopedSession>} overrides Overrides.
 * @returns {import("../../../../tools/district/web/js/filters.js").ScopedSession} A session.
 */
const session = (overrides = {}) => ({
	id: "s1",
	agent: "devin",
	agentSession: "dv",
	state: "EXECUTING",
	command: "pytest -q",
	cwd: "/work/app",
	workspace: "/work",
	clients: 0,
	cpuPercent: 10,
	rssBytes: 100,
	processCount: 2,
	...overrides
});
test("a hash round-trips through the router, and bad places fall back to the overview", () => {
	const place = Route.parse("#/sessions?agent=devin&state=live&sort=cpu");
	assert.deepEqual(place, {
		view: "sessions",
		id: "",
		scope: {
			agent: "devin",
			state: "live",
			sort: "cpu"
		}
	});
	assert.equal(Route.format(place), "#/sessions?state=live&agent=devin&sort=cpu");
	assert.equal(Route.format(Route.parse("#/session/a%2Fb")), "#/session/a%2Fb");
	assert.equal(Route.parse("#/session/a%2Fb").id, "a/b");
	assert.equal(Route.format(Route.parse("")), "#/");
	assert.equal(Route.parse("#/nowhere").view, "overview");
	assert.equal(Route.parse("#/agent").view, "overview", "an agent screen needs an agent");
	assert.equal(Route.parse("#/session/%E0%A4%A").id, "", "malformed escapes do not throw");
	assert.equal(Route.parse("#/activity/ignored").id, "", "only agent and session screens have a subject");
});
test("the scope a screen matches with includes the thing it is about", () => {
	assert.deepEqual(Route.effective(Route.parse("#/agent/devin?q=x")), {
		q: "x",
		agent: "devin"
	});
	assert.deepEqual(Route.effective(Route.parse("#/session/s1")), {
		session: "s1"
	});
	assert.equal(Route.href("agent", "devin", {
		state: "live"
	}), "#/agent/devin?state=live");
	assert.equal(Route.href("overview"), "#/");
});
test("filters read and write the hash, dropping what is empty, unknown or default", () => {
	assert.deepEqual(Filters.parse("#q=%20pytest%20&state=&bogus=1&sort=newest"), {
		q: "pytest"
	});
	assert.equal(Filters.format({}), "#");
	assert.equal(Filters.format({
		agent: "devin",
		q: "a b"
	}), "#q=a+b&agent=devin");
	assert.equal(Filters.link({
		agent: "devin",
		state: "IDLE"
	}, {
		state: "",
		sort: "cpu"
	}), "#agent=devin&sort=cpu");
	assert.equal(Filters.narrowed({
		sort: "cpu"
	}), false, "sorting does not narrow the view");
	assert.equal(Filters.narrowed({
		agent: "devin"
	}), true);
});
test("sessions match on agent, id, attachment, state and every search word", () => {
	assert.equal(Filters.session(session(), {}), true);
	assert.equal(Filters.session(session(), {
		agent: "codex"
	}), false);
	assert.equal(Filters.session(session({
		agent: ""
	}), {
		agent: "shell"
	}), true, "an unnamed agent is the shell");
	assert.equal(Filters.session(session(), {
		session: "other"
	}), false);
	assert.equal(Filters.session(session({
		clients: 0
	}), {
		clients: "1"
	}), false);
	assert.equal(Filters.session(session({
		clients: 2
	}), {
		clients: "1"
	}), true);
	assert.equal(Filters.session(session({
		state: "THINKING"
	}), {
		state: "live"
	}), true, "live is anything not idle or dead");
	assert.equal(Filters.session(session({
		state: "IDLE"
	}), {
		state: "live"
	}), false);
	assert.equal(Filters.session(session({
		state: "DEAD"
	}), {
		state: "live"
	}), false);
	assert.equal(Filters.session(session(), {
		state: "IDLE"
	}), false);
	assert.equal(Filters.session(session(), {
		q: "PYTEST app"
	}), true, "case-insensitive, any order");
	assert.equal(Filters.session(session(), {
		q: "pytest missing"
	}), false, "every word must appear");
});
test("events and commands match on who ran them and the search text", () => {
	const record = {
		agent: "devin",
		session_id: "s1",
		command: "make test",
		state: "",
		event_type: "COMMAND_STARTED"
	};
	assert.equal(Filters.record(record, {
		agent: "devin",
		session: "s1"
	}), true);
	assert.equal(Filters.record(record, {
		agent: "codex"
	}), false);
	assert.equal(Filters.record(record, {
		session: "s2"
	}), false);
	assert.equal(Filters.record(record, {
		q: "command started"
	}), true);
	assert.equal(Filters.record({
		...record,
		agent: ""
	}, {
		agent: "shell"
	}), true);
	assert.equal(Filters.record(record, {
		state: "DEAD"
	}), true, "a session's state does not apply to its records");
});
test("scoped totals are computed the way the server computes them", () => {
	const sessions = [session({
		state: "EXECUTING",
		cpuPercent: 10.26,
		clients: 1
	}), session({
		state: "IDLE",
		cpuPercent: 1.11,
		processCount: 3
	}), session({
		state: "DEAD",
		cpuPercent: 0,
		rssBytes: 50
	})];
	assert.deepEqual(Filters.totals(sessions), {
		sessions: 3,
		active: 1,
		idle: 1,
		dead: 1,
		cpuPercent: 11.4,
		rssBytes: 250,
		processes: 7,
		clients: 1
	});
	assert.deepEqual(Filters.agents([{
		agent: "b"
	}, {
		agent: ""
	}, {
		agent: "a"
	}, {
		agent: "b"
	}]), ["a", "b", "shell"]);
	assert.equal(Filters.label("agent", "devin"), "agent devin");
	assert.equal(Filters.label("state", "live"), "state live");
	assert.equal(Filters.label("clients", "1"), "attached only");
});
test("the font fits the widest line to the screen and stays within its limits", () => {
	assert.equal(Layout.columns("ab\ncdef   \n\ng"), 4, "trailing blanks do not count");
	assert.deepEqual(Layout.fit(360, 80, 0.6), {
		size: Layout.minimum > 7.5 ? Layout.minimum : 7.5,
		wide: true
	});
	assert.deepEqual(Layout.fit(480, 60, 0.6), {
		size: 13,
		wide: false
	});
	assert.deepEqual(Layout.fit(2000, 10, 0.6), {
		size: Layout.maximum,
		wide: false
	}, "a short pane is not blown up");
	assert.deepEqual(Layout.fit(0, 80, 0.6), {
		size: Layout.maximum,
		wide: false
	}, "no width yet means leave the default");
	const portrait = Layout.fit(360, 100, 0.6).size;
	const landscape = Layout.fit(780, 100, 0.6).size;
	assert.ok(landscape > portrait, "rotating to a wider screen enlarges the text");
	assert.equal(Layout.atEnd({
		scrollHeight: 1000,
		scrollTop: 590,
		clientHeight: 400
	}, 24), true);
	assert.equal(Layout.atEnd({
		scrollHeight: 1000,
		scrollTop: 500,
		clientHeight: 400
	}, 24), false);
});
test("the readable mode keeps a comfortable size and says when lines overflow it", () => {
	assert.deepEqual(Layout.read(390, 75, 0.6), {
		size: Layout.reading,
		wide: true
	});
	assert.deepEqual(Layout.read(900, 75, 0.6), {
		size: Layout.reading,
		wide: false
	});
	assert.ok(Layout.fit(390, 75, 0.6).size < Layout.reading, "fit shrinks below the readable size to show whole lines");
});
test("hardware keys become the names a session accepts", () => {
	/**
	 * Translate a synthetic hardware key event.
	 * @param {string} key Keyboard key value.
	 * @param {Partial<KeyboardEvent>} [extra] Additional keyboard state.
	 * @returns {ReturnType<typeof Keyboard.translate>} Translated terminal action.
	 */
	const press = (key, extra = {}) => Keyboard.translate({
		key,
		ctrlKey: false,
		metaKey: false,
		altKey: false,
		...extra
	});
	assert.deepEqual(press("Enter"), {
		key: "Enter"
	});
	assert.deepEqual(press("ArrowUp"), {
		key: "Up"
	});
	assert.deepEqual(press("Backspace"), {
		key: "BSpace"
	});
	assert.deepEqual(press("a"), {
		text: "a"
	});
	assert.deepEqual(press("é"), {
		text: "é"
	});
	assert.deepEqual(press("c", {
		ctrlKey: true
	}), {
		key: "C-c"
	});
	assert.deepEqual(press("C", {
		ctrlKey: true
	}), {
		key: "C-c"
	});
	assert.equal(press("1", {
		ctrlKey: true
	}), null);
	assert.equal(press("Shift"), null, "modifier keys alone send nothing");
	assert.equal(press("a", {
		metaKey: true
	}), null, "browser shortcuts stay with the browser");
});
test("the sizing mode is remembered, and defaults to fit without storage", () => {
	Object.defineProperty(globalThis, "localStorage", {
		configurable: true,
		value: undefined
	});
	assert.equal(Tabs.loadMode(), "fit");
	assert.doesNotThrow(() => Tabs.saveMode("read"));
	/** @type {Record<string,string>} */
	const kept = {};
	Object.defineProperty(globalThis, "localStorage", {
		configurable: true,
		value: {
			getItem: ( /** @type {string} */ key) => kept[key] ?? null,
			setItem: ( /** @type {string} */ key, /** @type {string} */ value) => {
				kept[key] = value;
			}
		}
	});
	assert.equal(Tabs.loadMode(), "fit");
	Tabs.saveMode("read");
	assert.equal(Tabs.loadMode(), "read");
	kept[Tabs.modeKey] = "anything else";
	assert.equal(Tabs.loadMode(), "fit");
});
test("open tabs survive without storage", () => {
	assert.deepEqual(Tabs.load(), []);
	assert.doesNotThrow(() => Tabs.save(["a"]));
	Object.defineProperty(globalThis, "localStorage", {
		configurable: true,
		value: {
			getItem: () => "[\"a\",3,\"b\"]",
			setItem: () => {
				throw new Error("full");
			}
		}
	});
	assert.deepEqual(Tabs.load(), ["a", "b"], "only ids are kept");
	assert.doesNotThrow(() => Tabs.save(["a"]), "a full or blocked store is not an error");
	Object.defineProperty(globalThis, "localStorage", {
		configurable: true,
		value: {
			getItem: () => "not json",
			setItem: () => undefined
		}
	});
	assert.deepEqual(Tabs.load(), []);
});
/**
 * Build a transcript command entry with stable defaults.
 * @param {Partial<import("../../../../tools/district/web/js/console.js").Entry>} overrides Overrides.
 * @returns {{command_id:string,command:string,cwd:string,started_at:string,finished_at:string,exit_code:number|null,duration_seconds:number,output:string}} A command.
 */
const entry = (overrides = {}) => ({
	command_id: "c1",
	command: "ls -la",
	cwd: "/w",
	started_at: "t0",
	finished_at: "t1",
	exit_code: 0,
	duration_seconds: 0.25,
	output: "a\nb\n",
	...overrides
});
test("a transcript reads like a terminal: prompt, output, then how it ended", () => {
	const text = Transcript.text([entry(), entry({
		command: "false",
		output: "",
		exit_code: 1,
		duration_seconds: 2.5
	})]);
	assert.equal(text.startsWith("\u001b[1;32m$\u001b[0m \u001b[1mls -la\u001b[0m\na\nb\n"), true);
	assert.equal(text.includes("exit 0\u001b[0m\u001b[2m · 250ms]"), true);
	assert.equal(text.includes("\u001b[31mexit 1\u001b[0m\u001b[2m · 2.5s]"), true);
	assert.equal(text.split("\n\n").length, 2, "commands are separated by a blank line");
	assert.match(Transcript.entry(entry({
		finished_at: "",
		exit_code: null
	})), /… running/u);
	assert.match(Transcript.status(entry({
		exit_code: null
	})), /exit \?/u);
});
test("a transcript keeps a command from restyling the screen and shortens long ones", () => {
	assert.equal(Transcript.clean("a\x1b[31mb\x07c\td\ne"), "a[31mbc\td\ne", "escape and bell are removed, tab and newline kept");
	const long = Transcript.entry(entry({
		command: Array.from({
			length: 9
		}, (_, index) => `line ${index}`).join("\n"),
		output: ""
	}));
	assert.match(long, /line 5/u);
	assert.doesNotMatch(long, /line 6/u);
	assert.match(long, /… 3 more lines/u);
	const big = Transcript.entry(entry({
		output: `${"x".repeat(5000)}END`
	}));
	assert.match(big, /… 1003 earlier characters not shown/u);
	assert.ok(big.endsWith("\x1b[0m") && big.includes("xxxEND"), "the end of the output is the part kept");
});
test("the transcript is refetched only when a command starts or finishes", () => {
	/** @type {Run[]} */
	const runs = [{
		command_id: "a",
		session_id: "s1",
		finished_at: "",
		exit_code: null
	}, {
		command_id: "b",
		session_id: "s2",
		finished_at: "t",
		exit_code: 0
	}];
	const before = Transcript.signature(runs, "s1");
	assert.equal(Transcript.signature(runs.slice().reverse(), "s1").length > 0, true);
	assert.equal(Transcript.signature([ /** @type {Run} */ (runs[1]), {
		... /** @type {Run} */ (runs[0]),
		command_id: "a"
	}], "s1"), before, "another session's runs do not matter");
	assert.notEqual(Transcript.signature([{
		... /** @type {Run} */ (runs[0]),
		finished_at: "t2",
		exit_code: 0
	}], "s1"), before, "finishing changes it");
});
test("a shell is a stream unless it is a tmux pane, and only tmux panes take input", () => {
	/**
	 * Build a shell fixture for backend capability checks.
	 * @param {string} backend Shell backend name.
	 * @param {string} [state] Shell lifecycle state.
	 * @returns {import("../../../../tools/district/web/js/console.js").Shell} Shell fixture.
	 */
	const shell = (backend, state = "ACTIVE") => ({
		id: "s",
		agent: "a",
		state,
		output: "",
		command: "",
		cwd: "",
		backend,
		surface: "vscode"
	});
	assert.equal(Console.streams(shell("hook")), true);
	assert.equal(Console.streams(shell("tmux")), false);
	assert.equal(Console.interactive(shell("tmux")), true);
	assert.equal(Console.interactive(shell("tmux", "DEAD")), false);
	assert.equal(Console.interactive(shell("hook")), false);
	assert.match(Console.note(shell("hook"), "s", 3, undefined), /Loading/u);
	assert.match(Console.note(shell("hook"), "s", 3, {
		text: ""
	}), /Nothing run yet/u);
	assert.equal(Console.note(shell("hook"), "s", 3, {
		text: "$ ls"
	}), "");
	assert.equal(Console.note(shell("tmux"), "s", 3, undefined), "No output yet.");
	assert.equal(Console.note(undefined, "s", 0, undefined), "Connecting to the shell list…");
	assert.equal(Console.note(undefined, "s", 3, undefined), "This shell has ended.");
	assert.equal(Console.note(undefined, "", 3, undefined), "No shell open. Tap + to choose one.");
	assert.doesNotMatch(Console.readOnlyNote(true), /persist_output/u);
	assert.doesNotMatch(Console.readOnlyNote(undefined), /persist_output/u);
	assert.match(Console.readOnlyNote(false), /persist_output/u);
});
/**
 * Install one browser-global test double without assigning a partial DOM type.
 * @param {string} name Global property name.
 * @param {unknown} value Test-double value.
 * @returns {void}
 */
const installGlobal = (name, value) => {
	Object.defineProperty(globalThis, name, {
		configurable: true,
		writable: true,
		value
	});
};
/**
 * Build the minimum console-page DOM needed by controller tests.
 * @returns {Record<string, FakeElement>} Console fixture nodes keyed by id.
 */
const consoleDom = () => {
	document.body.replaceChildren();
	const ids = ["screen", "notice", "latest", "link", "link-label", "zoom", "empty", "line", "keys", "add", "picker-close", "picker", "picker-list", "tabs", "text"];
	/** @type {Record<string, FakeElement>} */
	const nodes = {};
	ids.forEach(id => {
		const node = document.createElement(id === "zoom" ? "button" : "div");
		node.id = id;
		document.body.append(node);
		nodes[id] = node;
	});
	return nodes;
};
/**
 * Require a named console fixture node.
 * @param {Record<string, FakeElement>} nodes Fixture node map.
 * @param {string} id Required node id.
 * @returns {FakeElement} Existing fixture node.
 * @throws {Error} When the requested fixture node does not exist.
 */
const consoleNode = (nodes, id) => {
	const node = nodes[id];
	if (node === undefined) throw new Error(`Missing console fixture ${id}`);
	return node;
};
test("console construction restores requested state and initializes controller services", () => {
	const nodes = consoleDom();
	const screen = consoleNode(nodes, "screen");
	installGlobal("location", {
		search: "?session=requested",
		pathname: "/console",
		protocol: "http:",
		host: "example.test"
	});
	installGlobal("window", {
		addEventListener: () => {},
		visualViewport: null
	});
	const originals = {
		showMode: Console.prototype.showMode,
		measure: Console.prototype.measure,
		resize: Console.prototype.resize,
		connect: Console.prototype.connect,
		render: Console.prototype.render
	};
	Object.assign(Console.prototype, {
		showMode: () => {},
		measure: () => {},
		resize: () => {},
		connect: () => {},
		render: () => {}
	});
	try {
		const page = new Console();
		assert.equal(page.screen, screen);
		assert.deepEqual(page.ids, ["requested"]);
		assert.equal(page.active, "requested");
		assert.equal(page.following, true);
		assert.equal(page.retry, 1000);
		assert.equal(page.mode, "fit");
		assert.equal(page.ratio, 0.6);
	} finally {
		Object.assign(Console.prototype, originals);
	}
});
test("console status, sending, transcript loading, and follow state cover success and failure paths", async () => {
	const nodes = consoleDom();
	const notice = consoleNode(nodes, "notice");
	const screen = consoleNode(nodes, "screen");
	const latest = consoleNode(nodes, "latest");
	installGlobal("location", {
		search: "",
		pathname: "/console",
		protocol: "http:",
		host: "example.test"
	});
	const page = Object.create(Console.prototype);
	page.active = "s1";
	page.screen = screen;
	page.shells = new Map([
		["s1", {
			id: "s1",
			agent: "a",
			state: "ACTIVE",
			output: "",
			command: "",
			cwd: "",
			backend: "hook",
			surface: "vscode"
		}]
	]);
	page.transcripts = new Map();
	page.loading = new Set();
	page.holdOff = new Map();
	page.stale = false;
	page.following = true;
	page.render = () => {
		page.rendered = true;
	};
	installGlobal("fetch", async ( /** @type {string | URL | Request} */ url) => String(url).endsWith("/send") ? {
		ok: true
	} : {
		ok: true,
		json: async () => ({
			commands: [entry()],
			persistOutput: true
		})
	});
	assert.equal(await page.send({
		text: "hello"
	}), true);
	assert.equal(notice.textContent, "");
	await page.load("s1", "sig");
	assert.equal(page.transcripts.get("s1")?.signature, "sig");
	assert.equal(page.transcripts.get("s1")?.saved, true);
	assert.equal(page.loading.size, 0);
	assert.equal(page.rendered, true);
	page.watch([{
		command_id: "new",
		session_id: "s1",
		finished_at: "",
		exit_code: null
	}]);
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	assert.equal(page.loading.size, 0);
	screen.scrollHeight = 100;
	screen.clientHeight = 20;
	screen.scrollTop = 0;
	page.scrolled();
	assert.equal(page.following, false);
	assert.equal(latest.hidden, false);
	page.toEnd();
	assert.equal(page.following, true);
	assert.equal(screen.scrollTop, 100);
	page.active = "";
	assert.equal(await page.send({
		key: "Enter"
	}), false);
	installGlobal("fetch", async () => ({
		ok: false,
		status: 409,
		json: async () => ({
			detail: "blocked"
		})
	}));
	Object.defineProperty(page, "active", {
		configurable: true,
		value: "s1",
		writable: true
	});
	assert.equal(await page.send({
		text: "x"
	}), false);
	assert.match(notice.textContent, /blocked/u);
	installGlobal("fetch", async () => {
		throw new Error("offline");
	});
	assert.equal(await page.send({
		text: "x"
	}), false);
	assert.match(notice.textContent, /connection dropped/u);
	await page.load("s1", "failed");
	assert.equal(page.holdOff.has("s1"), true);
});
test("console sizing, mode, activation, and close update visible state", () => {
	const nodes = consoleDom();
	const screen = consoleNode(nodes, "screen");
	const zoom = consoleNode(nodes, "zoom");
	/** @type {unknown[]} */
	let historyArgs = [];
	/** @type {[string, string]} */
	let heightProperty = ["", ""];
	installGlobal("location", {
		search: "",
		pathname: "/console",
		protocol: "http:",
		host: "example.test"
	});
	installGlobal("history", {
		replaceState: ( /** @type {unknown[]} */ ...args) => {
			historyArgs = args;
		}
	});
	installGlobal("getComputedStyle", () => ({
		fontFamily: "monospace",
		paddingLeft: "4",
		paddingRight: "4"
	}));
	installGlobal("window", {
		innerHeight: 720,
		visualViewport: null
	});
	document.documentElement.style.setProperty = (name, value) => {
		heightProperty = [name, value];
	};
	const page = Object.create(Console.prototype);
	page.screen = screen;
	screen.clientWidth = 408;
	screen.textContent = "1234567890";
	screen.setAttribute("data-flow", "grid");
	page.following = true;
	page.ratio = 0.6;
	page.mode = "fit";
	page.ids = ["a", "b"];
	page.active = "a";
	page.signature = "old";
	page.stale = false;
	page.render = () => {
		page.rendered = true;
	};
	page.fit();
	assert.match(screen.style.fontSize, /px$/u);
	page.toggle();
	assert.equal(page.mode, "read");
	assert.equal(zoom.textContent, "Read");
	page.resize();
	assert.deepEqual(heightProperty, ["--primary-app-height", "720px"]);
	page.toEnd = () => {
		page.ended = true;
	};
	page.activate("b");
	assert.equal(page.active, "b");
	assert.equal(page.stale, true);
	assert.equal(page.ended, true);
	assert.equal(historyArgs.length > 0, true);
	page.close("b");
	assert.equal(page.ids.includes("b"), false);
});
test("console rendering builds tabs and output for live and streamed shells", () => {
	const nodes = consoleDom();
	const screen = consoleNode(nodes, "screen");
	const tabs = consoleNode(nodes, "tabs");
	const empty = consoleNode(nodes, "empty");
	const page = Object.create(Console.prototype);
	const tmux = {id: "tmux", agent: "shell", state: "ACTIVE", output: "hello", command: "", cwd: "/tmp", backend: "tmux", surface: "vscode"};
	const hook = {id: "hook", agent: "agent", state: "ACTIVE", output: "", command: "run", cwd: "/work", backend: "hook", surface: "vscode"};
	page.ids = ["tmux", "hook", "gone"];
	page.active = "tmux";
	page.shells = new Map([["tmux", tmux], ["hook", hook]]);
	page.transcripts = new Map([["hook", {signature: "sig", text: "$ run\nok", saved: true}]]);
	page.signature = "";
	page.screen = screen;
	page.stale = true;
	page.following = false;
	page.readOnlyShown = false;
	page.mode = "fit";
	page.ratio = 0.6;
	page.terminal = {
		text: "",
		/** @param {string} text Terminal text to render. */
		render(text) {
			this.text = text;
			screen.textContent = text;
		},
	};
	page.fit = () => {};
	page.toEnd = () => {};
	page.render();
	assert.equal(tabs.children.length, 3);
	assert.equal(screen.textContent, "hello");
	assert.equal(empty.hidden, true);
	const selected = page.tab("tmux", tmux);
	assert.equal(selected.children.length, 2);
	const missing = page.tab("gone", undefined);
	assert.equal(missing.dataset.state, "MISSING");
	page.active = "hook";
	page.stale = true;
	page.output();
	assert.equal(screen.textContent, "$ run\nok");
	assert.equal(screen.getAttribute("data-flow"), "wrap");
	page.dock();
	assert.match(consoleNode(nodes, "notice").textContent, /Read-only/u);
	page.active = "tmux";
	page.dock();
	assert.equal(consoleNode(nodes, "notice").textContent, "");
	page.signature = JSON.stringify([page.active, page.ids.map(/** @param {string} id Shell identifier. */ id => [id, page.shells.get(id)?.state ?? "MISSING", page.shells.get(id)?.agent ?? ""])]);
	page.tabs();
});
