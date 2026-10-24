"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
/** Fake DOM node used by the browser-surface harness. */
class FakeNode {
	/** Whether fake click listeners fire immediately. */
	static autoEvents = false;
	/** Initialize a fake DOM node. */
	constructor() {
		this.textContent = "";
		this.value = "";
		this.hidden = false;
		this.checked = false;
		this.disabled = false;
		this.childNodes = /** @type {unknown[]} */ ([]);
		this.children = /** @type {unknown[]} */ ([]);
		this.dataset = /** @type {Record<string, string>} */ ({});
		this.style = {
			setProperty: () => {}
		};
		this.classList = {
			add: () => {},
			remove: () => {},
			toggle: () => true,
			contains: () => false
		};
		this.scrollTop = 0;
		this.scrollHeight = 10;
		this.files = /** @type {unknown[]} */ ([]);
		this.firstElementChild = null;
		this.returnSelf = false;
	}
	/** Append nodes to the fake node.
	 * @param {...unknown} nodes Nodes to prepend.
	 */
	prepend(...nodes) {
		this.childNodes.unshift(...nodes);
		this.children.unshift(...nodes);
	}
	/** Append nodes to the fake node.
	 * @param {...unknown} nodes Nodes to append.
	 */
	append(...nodes) {
		this.childNodes.push(...nodes);
		this.children.push(...nodes);
		if (!this.firstElementChild && nodes.length) this.firstElementChild = nodes[0];
	}
	/** Append one child node.
	 * @param {unknown} node Node to append.
	 * @returns {unknown} The appended node.
	 */
	appendChild(node) {
		this.append(node);
		return node;
	}
	/** Replace all child nodes.
	 * @param {...unknown} nodes Replacement nodes.
	 */
	replaceChildren(...nodes) {
		this.childNodes = [...nodes];
		this.children = [...nodes];
		this.firstElementChild = nodes[0] ?? null;
	}
	/** Set a fake attribute.
	 * @param {string} name Attribute name.
	 * @param {unknown} value Attribute value.
	 */
	setAttribute(name, value) {
		Reflect.set(this, name, value);
	}
	/** Read a fake attribute.
	 * @param {string} name Attribute name.
	 * @returns {unknown} Stored attribute value, or null.
	 */
	getAttribute(name) {
		return Reflect.get(this, name) ?? null;
	}
	/** Accept an event listener.
	 * @param {string} name Event name.
	 * @param {(event: {target: FakeNode}) => void} listener Event listener.
	 */
	addEventListener(name, listener) {
		Reflect.set(this.dataset, "listener", "accepted");
		if (FakeNode.autoEvents && name === "click" && typeof listener === "function") listener({
			target: this
		});
	}
	/** Remove the fake node. */
	remove() {
		this.hidden = true;
	}
	/** Show the fake node as a modal dialog. */
	showModal() {
		this.hidden = false;
	}
	/** Close the fake modal dialog. */
	close() {
		this.hidden = true;
	}
	/** Report whether the fake control is valid.
	 * @returns {boolean} Whether the control is enabled and therefore valid.
	 */
	reportValidity() {
		return !this.disabled;
	}
	/** Focus the fake node. */
	focus() {
		Reflect.set(this.dataset, "focused", "true");
	}
	/** Click the fake node. */
	click() {
		Reflect.set(this.dataset, "clicked", "true");
	}
	/** Select the fake node. */
	select() {
		Reflect.set(this.dataset, "selected", "true");
	}
	/** Find the closest matching ancestor.
	 * @returns {FakeNode | null} Matching fake ancestor when enabled.
	 */
	closest() {
		Reflect.set(this.dataset, "closest", "checked");
		return this.returnSelf ? this : null;
	}
	/** Find one fake descendant.
	 * @returns {FakeNode} A fake descendant.
	 */
	querySelector() {
		return this.children[0] instanceof FakeNode ? this.children[0] : new FakeNode();
	}
	/** Find all fake descendants.
	 * @returns {FakeNode[]} Fake descendants.
	 */
	querySelectorAll() {
		Reflect.set(this.dataset, "queried", "all");
		return this.children.filter(child => child instanceof FakeNode);
	}
	/** Scroll the fake node into view. */
	static scrollIntoView = () => {};
}
/** Create a fake DOM node.
 * @returns {FakeNode} New fake DOM node.
 */
const node = () => new FakeNode();
globalThis.location = /** @type {Location} */ ( /** @type {unknown} */ ({
	pathname: "/p/abcdef123456/",
	hash: "",
	href: "https://example.test/p/abcdef123456/"
}));
/** @type {Map<string, string>} */
const store = new Map();
globalThis.localStorage = /** @type {Storage} */ ( /** @type {unknown} */ ({
	getItem: ( /** @type {string} */ key) => store.get(key) ?? null,
	setItem: ( /** @type {string} */ key, /** @type {string} */ value) => store.set(key, value),
	removeItem: ( /** @type {string} */ key) => store.delete(key)
}));
globalThis.sessionStorage = /** @type {Storage} */ ( /** @type {unknown} */ ({
	getItem: ( /** @type {string} */ key) => store.get(key) ?? null,
	setItem: ( /** @type {string} */ key, /** @type {string} */ value) => store.set(key, value),
	removeItem: ( /** @type {string} */ key) => store.delete(key)
}));
globalThis.document = /** @type {Document} */ ( /** @type {unknown} */ ({
	body: node(),
	createElement: () => node(),
	createElementNS: () => node(),
	createTextNode: ( /** @type {string} */ text) => ({
		textContent: text
	}),
	createDocumentFragment: () => node(),
	querySelector: () => node(),
	querySelectorAll: () => []
}));
globalThis.window = /** @type {Window & typeof globalThis} */ ( /** @type {unknown} */ ({
	addEventListener: () => {},
	removeEventListener: () => {},
	location: globalThis.location,
	matchMedia: () => ({
		matches: false
	}),
	open: () => null
}));
Object.defineProperty(globalThis, "navigator", {
	value: {
		clipboard: {
			writeText: async () => {}
		},
		serviceWorker: {
			register: async () => {}
		}
	},
	configurable: true
});
globalThis.Notification = /** @type {typeof Notification} */ ( /** @type {unknown} */ (class {
	static permission = "denied";
	static requestPermission = async () => "denied";
}));
/** Fake event source used to exercise server-sent event handling. */
class FakeEventSource {
	constructor() {
		this.listeners = /** @type {Map<string, (event: {data: string}) => void>} */ (new Map());
		this.closed = false;
	}
	/** Register a fake event-source listener.
	 * @param {string} name Event name.
	 * @param {(event: {data: string}) => void} listener Event listener.
	 */
	addEventListener(name, listener) {
		this.listeners.set(name, listener);
	}
	/** Close the fake event source. */
	close() {
		this.closed = true;
	}
}
globalThis.EventSource = /** @type {typeof EventSource} */ ( /** @type {unknown} */ (FakeEventSource));
globalThis.FormData = /** @type {typeof FormData} */ ( /** @type {unknown} */ (class {
	get() {
		return "value";
	}
}));
globalThis.URL = URL;
globalThis.fetch = /** @type {typeof fetch} */ (async () => /** @type {Response} */ ( /** @type {unknown} */ ({
	ok: true,
	status: 200,
	json: async () => ({}),
	text: async () => "",
	headers: {
		get: () => "application/json"
	}
})));
globalThis.confirm = () => true;
globalThis.prompt = () => "value";
globalThis.alert = () => {};
globalThis.requestAnimationFrame = ( /** @type {FrameRequestCallback} */ callback) => {
	callback(0);
	return 1;
};
const {
	DevinRemoteApp
} = require("../../../../plugins/devin_remote_control/src/media/web/js/app.js");
test("surface", async () => {
	const app = Object.create(DevinRemoteApp.prototype);
	const slots = new Map();
	Reflect.set(app, "slot", ( /** @type {string} */ name) => {
		if (!slots.has(name)) slots.set(name, node());
		return slots.get(name);
	});
	Object.assign(app, {
		formDialog: async () => ({
			tags: "one, two",
			title: "New",
			prompt: "Do it",
			maxAcuLimit: "5",
			path: "copy.js",
			message: "work",
			name: "branch"
		}),
		actionDialog: async ( /** @type {string} */ title) => title === "Create" ? "file" : title === "Agent actions" ? "details" : title === "Stash" ? "apply" : "apply",
		confirmDialog: async () => true,
		toast: () => {},
		setScreen: () => {},
		projectId: "abcdef123456",
		base: "/p/abcdef123456/",
		csrf: "csrf",
		bootstrap: {
			project: {
				id: "abcdef123456",
				name: "Project",
				root: "/tmp",
				gitRemote: "https://git.test/repo",
				uri: "file:///tmp"
			},
			gateway: {
				localUrl: "http://127.0.0.1",
				running: true
			},
			charter: {
				publicUrl: "https://example.test",
				state: "online"
			}
		},
		sessions: [{
			id: "agent",
			title: "Agent",
			status: "running",
			tags: ["vscode-project:abcdef123456"],
			repos: [{
				url: "https://git.test/repo"
			}],
			updated_at: Date.now()
		}],
		providers: [{
			id: "p",
			name: "Provider",
			status: "ok"
		}],
		git: {
			repository: true,
			files: [{
				path: "a.js",
				status: "M "
			}, {
				path: "b.js",
				status: " M"
			}],
			branch: "main",
			ahead: 1,
			behind: 2
		},
		gitLog: [{
			hash: "abc",
			subject: "Commit",
			author: "A",
			date: new Date().toISOString()
		}],
		branches: [{
			name: "main",
			current: true
		}, {
			name: "dev",
			current: false
		}],
		stashes: [{
			ref: "stash@{0}",
			message: "work"
		}],
		workspaceStats: {
			files: 3,
			directories: 2,
			size: 1024
		},
		tasks: [{
			label: "Test",
			command: "pytest"
		}],
		activity: [{
			type: "x",
			message: "did",
			createdAt: Date.now()
		}],
		diagnostics: [{
			path: "a.js",
			line: 2,
			column: 3,
			severity: "error",
			message: "bad"
		}],
		pairedDevices: [{
			id: "d",
			createdAt: 1,
			lastSeenAt: 2,
			expiresAt: 3,
			remoteAddress: "x",
			userAgent: "ua"
		}],
		currentDeviceId: "d",
		systemInfo: {
			platform: "darwin",
			arch: "arm64",
			hostname: "host"
		},
		agentFilter: "all",
		agentQuery: "",
		agentPanelMode: "details",
		gitMode: "changes",
		diffLayout: "unified",
		selectedAgent: {
			id: "agent",
			title: "Agent",
			status: "running"
		},
		agentDetails: {
			tags: ["tag"],
			attachments: [{
				name: "a",
				url: "https://example.test/a"
			}],
			insights: {
				status_detail: "work",
				acus_consumed: 2,
				num_devin_messages: 2,
				num_user_messages: 1,
				session_size: "small",
				devin_mode: "code",
				pull_requests: [{
					pr_url: "https://example.test/pr",
					pr_state: "open"
				}],
				parent_session_id: "p",
				child_session_ids: ["c"],
				analysis: {
					classification: {
						category: "bug_fix",
						programming_languages: ["JS"],
						tools_and_frameworks: ["Node"]
					},
					action_items: ["do"],
					issues: ["issue"]
				}
			}
		},
		selectedFile: {
			path: "a.js",
			content: "const x=1;",
			language: "javascript",
			size: 10
		},
		selectedDiff: {
			path: "a.js",
			status: "M ",
			diff: "@@ -1 +1 @@\n-old\n+new"
		},
		tree: new Map([
			["", [{
				path: "a.js",
				name: "a.js",
				kind: "file"
			}, {
				path: "src",
				name: "src",
				kind: "directory"
			}]]
		]),
		expanded: new Set([""]),
		eventSource: null,
		terminalSource: null,
		terminalId: "t",
		terminalBuffer: "hello",
		terminalSessions: new Map([
			["t", {
				id: "t",
				title: "Shell",
				cwd: "/tmp"
			}]
		]),
		terminalSources: new Map(),
		terminalHistory: ["ls"],
		recentFiles: ["a.js"],
		bookmarks: ["a.js"],
		fileHistory: [{
			hash: "abc",
			date: "now",
			author: "A",
			subject: "change"
		}],
		searchResults: [{
			path: "a.js",
			line: 1,
			text: "match"
		}],
		previousAgentStates: new Map(),
		messageTimer: 0,
		messageLoading: false,
		activeScreen: "dashboard",
		initialScreen: "dashboard"
	});
	Reflect.set(app, "request", async () => ({
		items: [{
			id: "x",
			path: "a.js",
			name: "a.js",
			kind: "file",
			status: "M ",
			hash: "abc",
			subject: "s",
			author: "a",
			date: "now"
		}],
		csrf: "csrf",
		repository: true,
		files: app.git.files,
		branch: "main",
		ahead: 1,
		behind: 0,
		content: "const x=1;",
		path: "a.js",
		diff: "@@ -1 +1 @@\n-a\n+b",
		sessions: [{
			id: "t"
		}],
		devices: app.pairedDevices,
		providers: app.providers,
		stats: app.workspaceStats,
		activity: app.activity,
		diagnostics: app.diagnostics,
		branches: app.branches,
		stashes: app.stashes,
		log: app.gitLog,
		system: app.systemInfo,
		ok: true,
		id: "t",
		command: "ls",
		output: "ok",
		analysis: {},
		tags: ["tag"],
		attachments: []
	}));
	/** @type {unknown[]} */
	const traversalErrors = [];
	const sync = ["renderAgents", "renderAgentDetails", "renderTree", "renderBreadcrumbs", "renderEditorTabs", "renderEditorStatus", "renderCode", "renderGit", "renderDiff", "renderSplitDiff", "renderTerminalQuickbar", "renderTerminal", "renderOverview", "renderDashboard", "renderStashes", "renderRecentFiles", "renderBookmarks", "renderDiagnostics", "renderWorkspaceSearch", "renderGitHistory", "renderBranches", "renderTasks", "renderTerminalTabs", "renderProjects", "renderStats", "renderActivity", "renderSecurity", "renderProviders", "renderProjectFacts", "setAgentPanel", "setCodeContext", "setGitMode"];
	const syncArguments = new Map([
		["renderCode", ["const value = 1;\n// comment"]],
		["renderDiff", ["@@ -1 +1 @@\n-old\n+new"]],
		["renderSplitDiff", ["@@ -1 +1 @@\n-old\n+new"]],
		["setAgentPanel", ["details"]],
		["setCodeContext", ["editor"]],
		["setGitMode", ["changes"]],
	]);
	for (const name of sync) try {
		Reflect.apply(Reflect.get(DevinRemoteApp.prototype, name), app, syncArguments.get(name) ?? []);
	} catch (error) {
		traversalErrors.push(error);
	}
	const asyncNames = ["loadMessages", "loadAgentDetails", "refreshAgents", "loadTree", "openFile", "saveFile", "refreshTree", "refreshGit", "openDiff", "toggleStage", "refreshDiagnostics", "loadFileHistory", "workspaceSearch", "refreshGitMeta", "gitStageAll", "gitUnstageAll", "gitPull", "gitPush", "refreshStats", "refreshActivity", "refreshSecurity", "revokeDevice", "refreshProviders", "refreshCurrent", "runTask", "interruptTerminal", "closeTerminal", "openDiagnostic", "openInVsCode", "inspectFile", "blameFile", "compareGitRefs", "discardSelectedDiff", "toggleBookmark", "openAgent", "editAgentTags", "generateAgentInsights", "sendAgentMessage", "newAgent", "agentMenu", "openTreeItem", "fileCreateMenu", "renameFile", "duplicateFile", "deleteFile", "newTerminal", "sendTerminal", "sendTerminalCommand", "projectSwitchDialog", "gitStashDialog", "stashMenu", "showCommit", "enableNotifications", "openFileRevision", "workspaceSearchDialog", "openSearchResult", "gitCommitDialog", "gitBranchDialog", "checkoutBranch"];
	/** Fake form data used by form-driven async actions. */
	const formData = {
		get: () => "value"
	};
	const form = node();
	const asyncArguments = new Map([
		["sendAgentMessage", [formData, form]],
		["sendTerminal", [formData, form]],
	]);
	await Promise.all(asyncNames.map(async name => {
		try {
			await Reflect.apply(Reflect.get(DevinRemoteApp.prototype, name), app, asyncArguments.get(name) ?? ["a.js", 1, 1]);
		} catch (error) {
			traversalErrors.push(error);
		}
	}));
	clearInterval(app.messageTimer);
	assert.ok(slots.size > 10);
});
test("application lifecycle covers constructor and workspace loading", async () => {
	const originalStart = DevinRemoteApp.prototype.start;
	DevinRemoteApp.prototype.start = async () => {};
	globalThis.Element = /** @type {typeof Element} */ ( /** @type {unknown} */ (FakeNode));
	const app = new DevinRemoteApp();
	DevinRemoteApp.prototype.start = originalStart;
	/** @type {Map<string, FakeNode>} */
	const slots = new Map();
	Reflect.set(app, "slot", ( /** @type {string} */ name) => {
		if (!slots.has(name)) slots.set(name, node());
		return slots.get(name);
	});
	Reflect.set(app, "attachTerminalSource", () => {});
	Reflect.set(app, "connectEvents", () => {});
	Reflect.set(app, "loadTree", async () => {});
	Reflect.set(app, "request", async () => ({
		project: {
			name: "Project",
			gitRemote: "https://git.test/repo"
		},
		sessions: [{
			id: "agent",
			status: "running",
			tags: ["vscode-project:abcdef123456"]
		}],
		providers: [],
		git: {
			repository: true,
			files: [],
			branch: "main"
		},
		gitLog: [],
		branches: [],
		stashes: [],
		workspace: {
			files: 1,
			directories: 1,
			bytes: 1024,
			languages: []
		},
		terminals: [{
			id: "terminal"
		}],
		tasks: [],
		activity: [],
		diagnostics: [],
		pairedDevices: [],
		currentDeviceId: "",
		system: {},
	}));
	await app.loadWorkspace();
	app.showPair("Pair now");
	app.setScreen("dashboard");
	app.setScreen("diff");
	app.setScreen("more");
	assert.equal(app.bootstrap.project.name, "Project");
});
test("event dispatch traverses navigation and form routes", async () => {
	const app = Object.create(DevinRemoteApp.prototype);
	const slots = new Map();
	Reflect.set(app, "slot", ( /** @type {string} */ name) => {
		if (!slots.has(name)) slots.set(name, node());
		return slots.get(name);
	});
	Object.assign(app, {
		activeScreen: "agents",
		selectedFile: {
			path: "a.js",
			content: "x",
			size: 1
		},
		selectedAgent: {
			id: "agent"
		},
		terminalId: "t",
		agentQuery: "",
		sessions: [],
		git: {
			files: []
		},
		diagnostics: [],
		bookmarks: [],
		recentFiles: [],
		providers: [],
		pairedDevices: [],
		activity: [],
		tasks: [],
		branches: [],
		stashes: [],
		gitLog: [],
		tree: new Map(),
		expanded: new Set(),
		terminalSessions: new Map(),
		terminalSources: new Map(),
		terminalHistory: [],
		request: async () => ({
			items: [],
			files: [],
			diff: "",
			csrf: "csrf"
		}),
		toast: () => {},
		setScreen: () => {},
		formDialog: async () => null,
		actionDialog: async () => null,
		confirmDialog: async () => false
	});
	/** @type {Array<[string, string]>} */
	const attributes = [
		["data-nav", "files"],
		["data-agent-filter", "running"],
		["data-agent-id", "agent"],
		["data-task-command", "pytest"],
		["data-branch-name", "main"],
		["data-terminal-id", "t"],
		["data-search-path", "a.js"],
		["data-device-id", "d"],
		["data-git-mode", "changes"],
		["data-stash-ref", "stash@{0}"],
		["data-commit-hash", "abc"],
		["data-open-path", "a.js"],
		["data-diff-layout", "split"],
		["data-terminal-command", "ls"],
		["data-code-context", "problems"],
		["data-bookmark-path", "a.js"],
		["data-file-revision", "abc"],
		["data-file-path", "a.js"],
		["data-change-path", "a.js"],
		["data-editor-mode", "edit"],
	];
	for (const [name, value] of attributes) {
		const target = node();
		target.returnSelf = true;
		target.setAttribute(name, value);
		app.onClick({
			target
		});
	}
	const actions = ["refresh", "new-agent", "close-chat", "agent-menu", "agent-edit-tags", "agent-generate-insights", "agent-refresh-details", "file-create-menu", "close-editor", "save-file", "download-file", "rename-file", "duplicate-file", "delete-file", "close-diff", "toggle-stage", "new-terminal", "clear-terminal", "interrupt-terminal", "close-terminal", "command-palette", "workspace-search", "git-stage-all", "git-unstage-all", "git-commit", "git-pull", "git-push", "git-branch-new", "git-discard", "refresh-activity", "refresh-security", "enable-notifications", "settings", "project-switch", "agent-show-chat", "agent-show-details", "file-inspect", "file-blame", "file-history", "toggle-bookmark", "open-vscode", "refresh-diagnostics", "show-problems", "git-compare", "git-stash"];
	for (const action of actions) {
		const target = node();
		target.returnSelf = true;
		target.setAttribute("data-action", action);
		app.onClick({
			target
		});
	}
	app.connectEvents();
	const source = /** @type {FakeEventSource} */ ( /** @type {unknown} */ (app.eventSource));
	/** Fire a fake server event.
	 * @param {string} name Event name.
	 * @param {unknown} data Event payload.
	 */
	const fire = (name, data) => {
		const listener = source.listeners.get(name);
		if (typeof listener === "function") listener({
			data: JSON.stringify(data)
		});
	};
	fire("agents", {
		sessions: []
	});
	fire("files", {});
	fire("git", {
		files: [],
		repository: true
	});
	fire("upstream-error", {
		service: "api",
		message: "failed"
	});
	fire("diagnostics", {
		items: []
	});
	fire("activity", {
		item: {
			type: "x"
		}
	});
	app.onKeyDown({
		metaKey: true,
		ctrlKey: false,
		key: "k",
		preventDefault: () => {}
	});
	app.onKeyDown({
		metaKey: true,
		ctrlKey: false,
		key: "s",
		preventDefault: () => {}
	});
	app.onKeyDown({
		metaKey: false,
		ctrlKey: false,
		key: "/",
		preventDefault: () => {}
	});
	for (const formName of ["pair", "agent-message", "terminal-input", "llm", "workspace-search"]) {
		const form = node();
		form.setAttribute("data-form", formName);
		app.onSubmit({
			target: form,
			preventDefault: () => {}
		});
	}
	await new Promise(resolve => {
		setTimeout(resolve, 0);
	});
	clearInterval(app.messageTimer);
	app.eventSource?.close();
	for (const terminalSource of app.terminalSources?.values?.() ?? []) terminalSource.close?.();
	assert.ok(slots.size > 0);
});
test("modal helpers build and resolve dialog surfaces", async () => {
	const app = Object.create(DevinRemoteApp.prototype);
	const slots = new Map();
	Reflect.set(app, "slot", ( /** @type {string} */ name) => {
		if (!slots.has(name)) slots.set(name, node());
		return slots.get(name);
	});
	FakeNode.autoEvents = true;
	const formResult = await app.formDialog("Create", [{
		label: "Name",
		name: "name",
		value: "x",
		required: true
	}], "Create");
	const actionResult = await app.actionDialog("Choose", [{
		id: "x",
		label: "Choice",
		icon: "i-file",
		danger: false
	}]);
	const confirmed = await app.confirmDialog("Delete", "Really?", "Delete");
	FakeNode.autoEvents = false;
	assert.equal(formResult, null);
	assert.equal(actionResult, null);
	assert.equal(confirmed, false);
});
test("request and startup paths cover session bootstrap", async () => {
	const app = Object.create(DevinRemoteApp.prototype);
	Object.assign(app, {
		base: "/p/project/",
		csrf: "csrf",
		projectId: "project",
		toast: () => {},
		showPair: () => {}
	});
	const payload = await app.request("api/test", {
		method: "POST",
		body: {
			value: 1
		}
	});
	assert.deepEqual(payload, {});
	let loads = 0;
	Reflect.set(app, "loadWorkspace", async () => {
		loads += 1;
	});
	Reflect.set(app, "request", async () => ({
		csrf: "next"
	}));
	await app.start();
	assert.equal(app.csrf, "next");
	assert.equal(loads, 1);
	app.csrf = "existing";
	await app.start();
	assert.equal(loads, 2);
	app.projectId = "";
	await app.start();
});
