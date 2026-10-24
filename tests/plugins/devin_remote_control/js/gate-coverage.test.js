"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const {
	EventEmitter
} = require("node:events");
/** @typedef {typeof Module & {_load(request: string, parent: NodeModule | null | undefined, isMain: boolean): unknown}} ModuleLoader */
const loader = /** @type {ModuleLoader} */ ( /** @type {unknown} */ (Module));
const original = loader._load;
/** @type {Map<string, unknown>} */
const stubs = new Map();
loader._load = (
	/** @type {string} */
	request,
	/** @type {NodeModule | null | undefined} */
	parent,
	/** @type {boolean} */
	isMain, ) => {
	for (const [suffix, stub] of stubs) {
		if (request === suffix || request.endsWith(suffix)) return stub;
	}
	return original.call(loader, request, parent, isMain);
};
/**
 * Registers a module stub for a require suffix.
 * @param {string} suffix Module request suffix.
 * @param {unknown} stub Replacement module export.
 * @returns {void} Nothing.
 */
const intercept = (suffix, stub) => {
	stubs.set(suffix, stub);
};
/**
 * Clears every registered module stub.
 * @returns {void} Nothing.
 */
const restore = () => {
	stubs.clear();
};
/**
 * Replaces globalThis.fetch for a test stretch.
 * @param {unknown} impl Fake fetch implementation.
 * @returns {void} Nothing.
 */
const setFetch = impl => {
	globalThis.fetch = /** @type {typeof globalThis.fetch} */ (impl);
};
// ---------------------------------------------------------------------------
// NgrokManager — fake child_process + global fetch.
// ---------------------------------------------------------------------------
/** @type {Array<{command: string, args: string[], options: unknown}>} */
const spawns = [];
/** @type {Array<EventEmitter & {killed: boolean, stdout: EventEmitter, stderr: EventEmitter, kill: () => boolean}>} */
const children = [];
/**
 * Records and returns a fake ngrok child process.
 * @returns {*} Fake child process.
 */
const fakeSpawn = (
	/** @type {string} */
	command,
	/** @type {string[]} */
	args,
	/** @type {unknown} */
	options, ) => {
	const child = Object.assign(new EventEmitter(), {
		killed: false,
		stdout: new EventEmitter(),
		stderr: new EventEmitter(),
		stdin: null,
		kill: () => {
			child.killed = true;
			return true;
		},
	});
	spawns.push({
		command,
		args,
		options
	});
	children.push(child);
	return child;
};
intercept("node:child_process", {
	spawn: fakeSpawn
});
const {
	NgrokManager
} = require("../../../../plugins/devin_remote_control/src/extension/lib/ngrok-manager.js");
restore();
test("ngrok snapshot and start wire the child and token env", async () => {
	spawns.length = 0;
	const manager = new NgrokManager("ngrok-bin", async () => "token-1");
	/** @type {unknown[]} */
	const changes = [];
	manager.on("change", ( /** @type {unknown} */ snapshot) => changes.push(snapshot));
	const realFetch = globalThis.fetch;
	setFetch(async () => ({
		ok: true,
		json: async () => ({
			tunnels: [{
				public_url: "https://tun.ngrok.io"
			}]
		}),
	}));
	const snapshot = await manager.start(8080);
	setFetch(realFetch);
	assert.equal(snapshot.state, "online");
	assert.equal(snapshot.publicUrl, "https://tun.ngrok.io");
	assert.equal(spawns.length, 1);
	const env = /** @type {{env: {NGROK_AUTHTOKEN?: string}} | undefined} */ (
		/** @type {unknown} */
		(spawns[0]?.options));
	assert.equal(env?.env.NGROK_AUTHTOKEN, "token-1");
	assert.equal(changes.length >= 1, true);
	// Second start while child alive returns early without a new spawn.
	const again = await manager.start(8080);
	assert.equal(spawns.length, 1);
	assert.equal(again.state, "online");
});
test("ngrok inspectLog parses url, nested url, and error lines", () => {
	const manager = new NgrokManager("ngrok");
	manager.inspectLog(Buffer.from('{"url":"https://a.ngrok.io"}\n'));
	assert.equal(manager.publicUrl, "https://a.ngrok.io");
	assert.equal(manager.state, "online");
	manager.inspectLog(Buffer.from('{"obj":{"url":"https://b.ngrok.io"}}\n'));
	assert.equal(manager.publicUrl, "https://b.ngrok.io");
	manager.inspectLog(Buffer.from("ERR_NGROK 1234 something failed\n"));
	assert.match(manager.error, /ERR_NGROK/u);
	manager.inspectLog(Buffer.from("plain harmless line\n"));
});
test("ngrok discoverAttempt guards and retries through fetch", async () => {
	const manager = new NgrokManager("ngrok");
	await manager.discoverAttempt(32); // attempt limit
	await manager.discoverUrl(); // no child → returns immediately
	manager.child = /** @type {import("node:child_process").ChildProcess} */ (
		/** @type {unknown} */
		({}));
	const realFetch = globalThis.fetch;
	setFetch(async () => {
		throw new Error("refused");
	});
	// fetch throws → retries; cap work by cutting child after first failure.
	const discovery = manager.discoverAttempt(0);
	await new Promise(resolve => {
		setTimeout(resolve, 30);
	});
	manager.child = null;
	await discovery;
	setFetch(async () => ({
		ok: true,
		json: async () => ({
			tunnels: [{
				public_url: "http://no-https"
			}]
		}),
	}));
	manager.child = /** @type {import("node:child_process").ChildProcess} */ (
		/** @type {unknown} */
		({}));
	const second = manager.discoverAttempt(0);
	await new Promise(resolve => {
		setTimeout(resolve, 30);
	});
	manager.child = null;
	await second;
	setFetch(realFetch);
});
test("ngrok child events, stop and dispose manage state", async () => {
	spawns.length = 0;
	children.length = 0;
	const manager = new NgrokManager("ngrok", async () => "");
	setFetch(async () => ({
		ok: false,
		json: async () => ({})
	}));
	const pending = manager.start(8080);
	await new Promise(resolve => {
		setImmediate(resolve);
	});
	const child = children[0];
	assert.ok(child);
	child.emit("error", new Error("spawn failed"));
	assert.equal(manager.state, "error");
	child.emit("close", 2);
	assert.equal(manager.state, "error");
	assert.equal(manager.child, null);
	await pending;
	manager.stop(); // no child → no-op
	// Start again and exercise the stopping branch.
	setFetch(async () => ({
		ok: true,
		json: async () => ({
			tunnels: [{
				public_url: "https://x.ngrok.io"
			}]
		}),
	}));
	const second = manager.start(8080);
	await new Promise(resolve => {
		setImmediate(resolve);
	});
	const live = children.at(-1);
	assert.ok(live);
	manager.stop();
	assert.equal(manager.state, "stopping");
	live.emit("close", 0);
	assert.equal(manager.state, "stopped");
	await second;
	manager.dispose();
});
// ---------------------------------------------------------------------------
// ShellSession + TerminalManager — fake spawn child with writable stdin.
// ---------------------------------------------------------------------------
/** @type {Array<{command: string, args: string[]}>} */
const shellSpawns = [];
/**
 * Records and returns a fake shell child process.
 * @returns {*} Fake child process.
 */
const fakeShellSpawn = (
	/** @type {string} */
	command,
	/** @type {string[]} */
	args, ) => {
	shellSpawns.push({
		command,
		args
	});
	const child = Object.assign(new EventEmitter(), {
		stdout: new EventEmitter(),
		stderr: new EventEmitter(),
		stdin: Object.assign(new EventEmitter(), {
			writable: true,
			written: /** @type {string[]} */ ([]),
			write( /** @type {string} */ chunk) {
				this.written.push(chunk);
			},
		}),
		kill: () => {
			child.killed = true;
			return true;
		},
		killed: false,
	});
	return child;
};
intercept("node:child_process", {
	spawn: fakeShellSpawn
});
const {
	ShellSession,
	TerminalManager
} = require("../../../../plugins/devin_remote_control/src/extension/lib/shell-session.js");
restore();
const project = /** @type {any} */ ({
	id: "proj-1",
	root: "/tmp/root"
});
test("shell session lifecycle covers output, write, close", () => {
	shellSpawns.length = 0;
	const session = new ShellSession(project, "");
	assert.equal(shellSpawns.length, 1);
	/** @type {Array<{stream: string}>} */
	const seen = [];
	session.on("terminal", ( /** @type {{stream: string}} */ event) => seen.push(event));
	session.push("stdout", Buffer.from("hello"));
	assert.equal(session.buffer, "hello");
	assert.equal(seen.at(-1)?.stream, "stdout");
	session.write("pwd\n");
	const stdin = /** @type {{written: string[]}} */ (
		/** @type {unknown} */
		(session.process.stdin));
	assert.deepEqual(stdin.written, ["pwd\n"]);
	// close twice → idempotent.
	session.close();
	session.close();
	assert.equal(session.closed, true);
	assert.throws(() => session.write("x"), /closed/u);
	const snapshot = session.snapshot();
	assert.equal(snapshot.projectId, "proj-1");
	assert.equal(snapshot.closed, true);
});
test("shell session honors requested and default shell fallbacks", () => {
	shellSpawns.length = 0;
	const first = new ShellSession(project, "/bin/zsh");
	assert.equal(first.shell(), "/bin/zsh");
	const second = new ShellSession(project, "");
	assert.equal(second.shell().length > 0, true);
	first.close();
	second.close();
});
test("terminal manager tracks, finds, lists, and closes sessions", () => {
	const manager = new TerminalManager("");
	const other = /** @type {any} */ ({
		id: "proj-2",
		root: "/tmp/other"
	});
	const one = manager.create(project);
	const two = manager.create(other);
	assert.equal(manager.get("proj-1", one.id), one);
	assert.throws(() => manager.get("proj-1", two.id), /not found/u);
	assert.throws(() => manager.get("proj-1", "missing"), /not found/u);
	const listed = manager.list("proj-1");
	assert.equal(listed.length, 1);
	assert.equal(listed[0]?.id, one.id);
	// The close event removes the session from the map.
	one.process.emit("close", 0);
	assert.equal(manager.sessions.size, 1);
	manager.closeAll();
	assert.equal(manager.sessions.size, 0);
});
// ---------------------------------------------------------------------------
// ProjectRegistry — fake vscode + GitService.
// ---------------------------------------------------------------------------
/** Fake workspace folder URI. */
const folderUri = /** @type {import("vscode").Uri} */ (
	/** @type {unknown} */
	({
		fsPath: "/ws/alpha",
		toString: () => "file:///ws/alpha"
	}));
const folder = {
	uri: folderUri,
	name: "alpha",
	index: 0
};
/** Fake diagnostic file URI inside the workspace. */
const diagUri = {
	fsPath: "/ws/alpha/src/x.ts",
	toString: () => "file:///ws/alpha/src/x.ts"
};
/** @type {unknown[]} */
const openedDocs = [];
/** @type {Array<{document: unknown, selection: unknown, revealRange: () => void}>} */
const editors = [];
/** @type {Array<[string, unknown]>} */
const registryListeners = [];
/** @type {{document: {uri: unknown}} | undefined} */
let activeEditor;
/**
 * Sets the fake active editor.
 * @param {{document: {uri: unknown}} | undefined} value Editor or none.
 * @returns {void} Nothing.
 */
const setActiveEditor = value => {
	activeEditor = value;
};
const vscodeFake = {
	workspace: {
		workspaceFolders: [folder],
		onDidChangeWorkspaceFolders: ( /** @type {unknown} */ callback) => {
			registryListeners.push(["folders", callback]);
			return {
				dispose: () => {}
			};
		},
		getWorkspaceFolder: ( /** @type {{fsPath?: string} | string} */ uri) => {
			const fsPath = typeof uri === "object" ? uri.fsPath : uri;
			return (fsPath ?? "").startsWith("/ws/alpha") ? folder : undefined;
		},
		asRelativePath: () => "src/x.ts",
		openTextDocument: async ( /** @type {unknown} */ uri) => {
			openedDocs.push(uri);
			return {
				uri
			};
		},
	},
	window: {
		onDidChangeActiveTextEditor: ( /** @type {unknown} */ callback) => {
			registryListeners.push(["editor", callback]);
			return {
				dispose: () => {}
			};
		},
		get activeTextEditor() {
			return activeEditor;
		},
		showTextDocument: async ( /** @type {unknown} */ doc) => {
			/** Fake text editor returned by showTextDocument. */
			const editor = {
				document: doc,
				selection: null,
				revealRange: () => {}
			};
			editors.push(editor);
			return editor;
		},
	},
	languages: {
		onDidChangeDiagnostics: ( /** @type {unknown} */ callback) => {
			registryListeners.push(["diag", callback]);
			return {
				dispose: () => {}
			};
		},
		getDiagnostics: () => [
			[diagUri, [{
				severity: 0,
				message: "boom",
				source: "ts",
				code: {
					value: "E1"
				},
				range: {
					start: {
						line: 1,
						character: 2
					},
					end: {
						line: 1,
						character: 5
					},
				},
			}, {
				severity: 1,
				message: "careful",
				range: {
					start: {
						line: 0,
						character: 0
					},
					end: {
						line: 0,
						character: 1
					},
				},
			}, ]]
		],
	},
	DiagnosticSeverity: {
		Error: 0,
		Warning: 1,
		Information: 2,
		Hint: 3
	},
	Uri: {
		file: ( /** @type {string} */ fsPath) => ({
			fsPath,
			toString: () => `file://${fsPath}`
		}),
		joinPath: ( /** @type {{fsPath: string}} */ base, /** @type {string[]} */ ...parts) => {
			const segments = [base.fsPath, ...parts].join("/").split("/").filter(Boolean);
			/** @type {string[]} */
			const resolved = [];
			segments.forEach(segment => {
				if (segment === "..") resolved.pop();
				else if (segment !== ".") resolved.push(segment);
			});
			return {
				fsPath: `/${resolved.join("/")}`,
				toString: () => `file:///${resolved.join("/")}`,
			};
		},
	},
	Position: class {
		/**
		 * Creates a position.
		 * @param {number} line Zero-based line.
		 * @param {number} character Zero-based character.
		 */
		constructor(line, character) {
			this.line = line;
			this.character = character;
		}
	},
	Selection: class {
		/**
		 * Creates a selection.
		 * @param {unknown} anchor Anchor position.
		 * @param {unknown} active Active position.
		 */
		constructor(anchor, active) {
			this.anchor = anchor;
			this.active = active;
		}
	},
	Range: class {
		/**
		 * Creates a range.
		 * @param {unknown} start Start position.
		 * @param {unknown} end End position.
		 */
		constructor(start, end) {
			this.start = start;
			this.end = end;
		}
	},
};
/** Structural vscode substitute cast to the real module type. */
const vscodeReg = /** @type {typeof import("vscode")} */ (
	/** @type {unknown} */
	(vscodeFake));
/** Deterministic stand-in for GitService remote lookups. */
class FakeGitService {
	/** Creates the fake service with a canned remote. */
	constructor() {
		this.remoteUrl = "git@example.com:alpha.git";
	}
	/**
	 * Returns the canned remote URL.
	 * @returns {Promise<string>} Remote URL.
	 */
	async remote() {
		return this.remoteUrl;
	}
}
intercept("vscode", vscodeFake);
intercept("/lib/git-service", {
	GitService: FakeGitService
});
intercept("./git-service", {
	GitService: FakeGitService
});
const {
	ProjectRegistry
} = require("../../../../plugins/devin_remote_control/src/extension/lib/project-registry.js");
restore();
test("registry refresh, events, list, active, and get", async () => {
	const registry = new ProjectRegistry(vscodeReg);
	assert.equal(registryListeners.length, 3);
	await new Promise(resolve => {
		setTimeout(resolve, 10);
	});
	const projectId = ProjectRegistry.idFor(folderUri);
	assert.equal(registry.all().length, 1);
	assert.equal(registry.get(projectId).name, "alpha");
	assert.equal(registry.get(projectId).gitRemote, "git@example.com:alpha.git");
	assert.throws(() => registry.get("missing"), /no longer open/u);
	assert.deepEqual(registry.publicList()[0]?.gitRemote, "git@example.com:alpha.git");
	// active: no editor → first project.
	assert.equal(registry.active()?.name, "alpha");
	// editor inside workspace → owning project.
	setActiveEditor({
		document: {
			uri: diagUri
		}
	});
	assert.equal(registry.active()?.name, "alpha");
	setActiveEditor(undefined);
	registry.dispose();
	assert.equal(registry.listenerCount("projects"), 0);
});
test("registry diagnostics maps severity and sorts errors first", () => {
	const registry = new ProjectRegistry(vscodeReg);
	const projectId = ProjectRegistry.idFor(folderUri);
	const items = registry.diagnostics(projectId);
	assert.equal(items.length, 2);
	assert.equal(items[0]?.severity, "error");
	assert.equal(items[0]?.code, "E1");
	assert.equal(items[0]?.line, 2);
	assert.equal(items[1]?.severity, "warning");
	assert.equal(items[1]?.source, "");
	registry.dispose();
});
test("registry openLocation resolves and rejects outside paths", async () => {
	openedDocs.length = 0;
	editors.length = 0;
	const registry = new ProjectRegistry(vscodeReg);
	const projectId = ProjectRegistry.idFor(folderUri);
	const result = await registry.openLocation(projectId, "src/x.ts", 3, 5);
	assert.equal(result.ok, true);
	assert.equal(result.line, 3);
	assert.equal(result.column, 5);
	assert.equal(openedDocs.length, 1);
	assert.equal(editors.length, 1);
	assert.ok(editors[0]?.selection);
	await assert.rejects(
		() => registry.openLocation(projectId, "../outside.txt"), /outside the project workspace/u, );
	await assert.rejects(
		() => registry.openLocation("missing", "x"), /no longer open/u, );
	registry.dispose();
});
// ---------------------------------------------------------------------------
// ExtensionController — fake vscode + fs + every lib module.
// ---------------------------------------------------------------------------
/** @type {Array<{type: string, payload: unknown}>} */
const panelMessages = [];
/** @type {string[]} */
const stored = [];
/** @type {Map<string, unknown>} */
const secrets = new Map([
	["devinRemote.devinToken", "tok"]
]);
/** @type {Array<[string, unknown]>} */
const registeredCommands = [];
/** @type {unknown[]} */
const disposables = [];
const gatewayEvents = new EventEmitter();
const fakeGateway = {
	on: gatewayEvents.on.bind(gatewayEvents),
	start: async () => 8080,
	stop: async () => true,
	snapshot: () => ({
		running: true,
		port: 8080
	}),
	notifyProject: () => {},
};
const ngrokEvents = new EventEmitter();
const fakeNgrok = {
	on: ngrokEvents.on.bind(ngrokEvents),
	start: async () => ({
		state: "online"
	}),
	stop: () => {},
	dispose: () => {},
	snapshot: () => ({
		state: "stopped",
		publicUrl: "",
		error: ""
	}),
};
/**
 * Restores the fake ngrok start/stop implementations after a test.
 * @param {() => void} stop Stop implementation.
 * @param {() => Promise<{state: string}>} start Start implementation.
 * @returns {void} Nothing.
 */
const setNgrokImpls = (stop, start) => {
	fakeNgrok.stop = stop;
	fakeNgrok.start = start;
};
const registryEvents = new EventEmitter();
const fakeRegistry = Object.assign(registryEvents, {
	all: () => [],
	publicList: () => [],
	diagnostics: () => [],
	get: () => ({
		id: "p",
		name: "p",
		root: "/ws",
		uri: {},
		gitRemote: ""
	}),
	active: () => null,
	dispose: () => {},
});
/** Fake status bar item. */
const statusBar = {
	command: "",
	name: "",
	tooltip: "",
	show: () => {},
	dispose: () => {}
};
/** @type {(() => void) | null} */
let disposeCallback = null;
const webview = {
	cspSource: "csp-x",
	asWebviewUri: ( /** @type {{fsPath: string}} */ uri) => ({
		toString: () => `wv://${uri.fsPath}`
	}),
	onDidReceiveMessage: () => ({
		dispose: () => {}
	}),
	postMessage: ( /** @type {{type: string}} */ message) => {
		panelMessages.push({
			type: message.type,
			payload: message
		});
	},
	html: "",
};
const panel = {
	webview,
	reveal: () => {},
	onDidDispose: ( /** @type {() => void} */ callback) => {
		disposeCallback = callback;
		return {
			dispose: () => {}
		};
	},
};
/** @type {Array<unknown>} */
const quickPicks = [];
/** @type {Array<unknown>} */
const inputs2 = [];
/** @type {Array<[string, unknown]>} */
const updates2 = [];
/** @type {Record<string, unknown>} */
const configValues = {
	"gateway.autoStart": false,
	"gateway.port": 0,
	"ngrok.autoStart": false,
	"ngrok.command": "ngrok",
	"session.hours": 12,
	"files.maxBytes": 1024,
	"files.excludes": [],
	"devin.orgId": "org-1",
	"shell": "",
	"providers.openAICompatible.baseUrl": "https://api.openai.com/v1",
	"providers.openAICompatible.model": "",
	"providers.anthropic.model": "",
};
/** Mutable error-message sink swapped by tests. @type {(message: string) => void} */
let errorSink = () => {};
/**
 * Swaps the fake error-message sink.
 * @param {(message: string) => void} sink Replacement sink.
 * @returns {void} Nothing.
 */
const setErrorSink = sink => {
	errorSink = sink;
};
const vscodeExt = {
	workspace: {
		getConfiguration: () => ({
			get: ( /** @type {string} */ key, /** @type {unknown} */ fallback) => configValues[key] ?? fallback,
			update: async (
				/** @type {string} */
				key,
				/** @type {unknown} */
				value, ) => {
				updates2.push([key, value]);
			},
		}),
	},
	window: {
		createStatusBarItem: () => statusBar,
		createWebviewPanel: () => panel,
		showErrorMessage: ( /** @type {string} */ message) => {
			errorSink(message);
		},
		showInformationMessage: () => {},
		showInputBox: async () => inputs2.shift(),
		showQuickPick: async ( /** @type {unknown[]} */ items) => quickPicks.length > 0 ? quickPicks.shift() : items[0],
	},
	commands: {
		registerCommand: ( /** @type {string} */ name, /** @type {unknown} */ callback) => {
			registeredCommands.push([name, callback]);
			return {
				dispose: () => {}
			};
		},
	},
	StatusBarAlignment: {
		Left: 1
	},
	ViewColumn: {
		Active: -1
	},
	Uri: {
		file: ( /** @type {string} */ fsPath) => ({
			fsPath
		}),
		joinPath: ( /** @type {{fsPath?: string} | string} */ base, /** @type {string[]} */ ...parts) => ({
			fsPath: [typeof base === "object" ? base.fsPath : base, ...parts].join("/"),
		}),
	},
	ConfigurationTarget: {
		Global: 1
	},
};
const contextFake = {
	secrets: {
		get: async ( /** @type {string} */ key) => secrets.get(key) ?? "",
		store: async ( /** @type {string} */ key, /** @type {unknown} */ value) => {
			stored.push(`${key}=${String(value)}`);
			secrets.set(key, value);
		},
	},
	subscriptions: disposables,
	extensionPath: "/ext",
	extensionUri: {
		fsPath: "/ext"
	},
};
/** Fake ExtensionContext cast to the real type. */
const context = /** @type {import("vscode").ExtensionContext} */ (
	/** @type {unknown} */
	(contextFake));
const fsFake = {
	readFile: async () => "csp:{{CSP_SOURCE}} css:{{PANEL_CSS}} js:{{PANEL_JS}} qr:{{QR_JS}}",
};
intercept("vscode", vscodeExt);
intercept("node:fs/promises", fsFake);
intercept("./lib/activity-service", {
	ActivityService: class {}
});
intercept("./lib/devin-client", {
	DevinClient: class {}
});
intercept("./lib/file-service", {
	FileService: class {}
});
intercept("./lib/gateway-server", {
	/** @returns {unknown} Shared gateway fake. */
	GatewayServer: function FakeGatewayServer() {
		return fakeGateway;
	},
});
intercept("./lib/ngrok-manager", {
	/** @returns {unknown} Shared ngrok fake. */
	NgrokManager: function FakeNgrokManager() {
		return fakeNgrok;
	},
});
intercept("./lib/project-registry", {
	/** @returns {unknown} Shared registry fake. */
	ProjectRegistry: function FakeProjectRegistry() {
		return fakeRegistry;
	},
});
intercept("./lib/provider-service", {
	ProviderService: class {
		/** Seeds a blank provider configuration. */
		constructor() {
			this.configuration = {
				openAIBaseUrl: "",
				openAIModel: "",
				anthropicModel: ""
			};
		}
	}
});
intercept("./lib/security", {
	SecurityService: class {}
});
intercept("./lib/shell-session", {
	TerminalManager: class {}
});
const {
	ExtensionController,
	activate: activateExt,
	deactivate: deactivateExt
} = require("../../../../plugins/devin_remote_control/src/extension/extension.js");
restore();
test("controller constructor wires services, status bar, and commands", async () => {
	registeredCommands.splice(0);
	disposables.splice(0);
	const controller = new ExtensionController(context);
	assert.equal(controller.panel, null);
	assert.equal(statusBar.command, "devinRemote.openProjectPanel");
	assert.equal(registeredCommands.length, 6);
	assert.ok(disposables.length >= 3);
	// autoStart=false → gateway.start not awaited (fake always resolves anyway).
	await controller.stopGateway();
	await controller.dispose();
});
test("controller panel opens, sets html, reveals on reuse, posts state", async () => {
	panelMessages.splice(0);
	const controller = new ExtensionController(context);
	controller.panel = null;
	await controller.openPanel();
	assert.ok(panel.webview.html.includes("csp:csp-x"));
	assert.ok(panel.webview.html.includes("wv:///ext/media/panel/css/panel.css"));
	assert.ok(panelMessages.length >= 1);
	await controller.openPanel(); // reveal path
	if (disposeCallback !== null) disposeCallback();
	assert.equal(controller.panel, null);
	await controller.dispose();
});
test("controller gateway actions update state and surface errors", async () => {
	/** @type {string[]} */
	const errorsExt = [];
	const previousSink = errorSink;
	setErrorSink(message => {
		errorsExt.push(message);
	});
	const controller = new ExtensionController(context);
	fakeGateway.start = async () => 9090;
	await controller.startGateway();
	fakeGateway.start = async () => {
		throw new Error("bind fail");
	};
	await controller.startGateway();
	assert.ok(errorsExt.some(message => /bind fail/u.test(message)));
	setErrorSink(previousSink);
	await controller.dispose();
});
test("controller provider credentials cover both providers and aborts", async () => {
	stored.splice(0);
	updates2.splice(0);
	const controller = new ExtensionController(context);
	// Anthropic path.
	inputs2.splice(0);
	inputs2.push("claude-opus", "key-anth");
	await controller.setProviderCredentials("anthropic");
	assert.ok(stored.some(entry => entry.includes("provider.anthropic")));
	assert.ok(updates2.some(entry => entry[0] === "providers.anthropic.model"));
	// OpenAI-compatible path with baseUrl prompt.
	inputs2.splice(0);
	inputs2.push("gpt-5", "https://alt.example.com/v1", "key-oai");
	await controller.setProviderCredentials("openai-compatible");
	assert.ok(stored.some(entry => entry.includes("provider.openai-compatible")));
	assert.ok(updates2.some(entry => entry[0] === "providers.openAICompatible.baseUrl"));
	// Abort paths: empty provider, undefined model.
	quickPicks.splice(0);
	quickPicks.push(undefined);
	await controller.setProviderCredentials("");
	inputs2.splice(0);
	inputs2.push(undefined);
	await controller.setProviderCredentials("anthropic");
	await controller.dispose();
});
test("controller ngrok token stores and restarts charter when running", async () => {
	stored.splice(0);
	const controller = new ExtensionController(context);
	let restarted = 0;
	const previousStop = fakeNgrok.stop;
	const previousStart = fakeNgrok.start;
	fakeNgrok.stop = () => {
		restarted += 1;
	};
	fakeNgrok.start = async () => {
		restarted += 1;
		return {
			state: "online"
		};
	};
	inputs2.splice(0);
	inputs2.push("ngrok-token-1");
	await controller.setNgrokToken();
	assert.ok(stored.some(entry => entry.includes("ngrokToken=ngrok-token-1")));
	assert.equal(restarted, 2);
	// Undefined token aborts early.
	inputs2.splice(0);
	inputs2.push(undefined);
	await controller.setNgrokToken();
	setNgrokImpls(previousStop, previousStart);
	await controller.dispose();
});
test("extension activate and deactivate manage the controller", async () => {
	activateExt(context);
	await deactivateExt();
});
