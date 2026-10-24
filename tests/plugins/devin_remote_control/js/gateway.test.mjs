import assert from "node:assert/strict";
import {
	execFileSync
} from "node:child_process";
import {
	EventEmitter
} from "node:events";
import {
	mkdir,
	mkdtemp,
	rm,
	writeFile
} from "node:fs/promises";
import {
	tmpdir
} from "node:os";
import {
	join
} from "node:path";
import test from "node:test";
import {
	createRequire
} from "node:module";
const require = createRequire(import.meta.url);
const {
	FileService
} = require("../../../../plugins/devin_remote_control/src/extension/lib/file-service.js");
const {
	GatewayServer
} = require("../../../../plugins/devin_remote_control/src/extension/lib/gateway-server.js");
const {
	SecurityService
} = require("../../../../plugins/devin_remote_control/src/extension/lib/security.js");
test("gateway pairs a project and serves authenticated project APIs", async () => {
	const root = await mkdtemp(join(tmpdir(), "devin-gateway-project-"));
	const assets = await mkdtemp(join(tmpdir(), "devin-gateway-assets-"));
	const project = {
		id: "abcdef123456",
		name: "sample",
		root,
		gitRemote: ""
	};
	await writeFile(join(root, "hello.txt"), "hello\n", "utf8");
	await writeFile(join(assets, "index.html"), "<!doctype html><title>ok</title>", "utf8");
	const security = new SecurityService(12);
	const gateway = new GatewayServer({
		registry: /** @type {any} */ ({
			get: /** @type {(id: string) => any} */ (id => {
				assert.equal(id, project.id);
				return project;
			}),
			publicList: () => [{
				id: project.id,
				name: project.name,
				gitRemote: ""
			}]
		}),
		security,
		files: new FileService(1024 * 1024, []),
		devin: /** @type {any} */ ({
			configured: () => false,
			listSessions: async () => [],
			createSession: async () => ({}),
			messages: async () => [],
			sendMessage: async () => ({}),
			archive: async () => ({}),
			terminate: async () => ({})
		}),
		providers: /** @type {any} */ ({
			providers: async () => [],
			chat: async () => ({
				text: ""
			})
		}),
		terminals: /** @type {any} */ ({
			closeAll: () => {},
			create: () => {
				throw new Error("unused");
			}
		}),
		assetsRoot: assets
	});
	try {
		const port = await gateway.start(0);
		const base = `http://127.0.0.1:${port}/p/${project.id}/`;
		const pairing = security.createPairing(project.id);
		const paired = await fetch(`${base}api/pair`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				code: pairing.code
			})
		});
		assert.equal(paired.status, 200);
		const cookie = paired.headers.get("set-cookie")?.split(";", 1)[0] ?? "";
		const {
			csrf
		} = await paired.json();
		assert.ok(cookie.includes("devin_remote_session="));
		assert.ok(csrf);
		const bootstrap = await fetch(`${base}api/bootstrap`, {
			headers: {
				Cookie: cookie
			}
		});
		assert.equal(bootstrap.status, 200);
		assert.equal((await bootstrap.json()).project.name, "sample");
		const saved = await fetch(`${base}api/file`, {
			method: "PUT",
			headers: {
				Cookie: cookie,
				"Content-Type": "application/json",
				"X-Devin-Remote-CSRF": csrf
			},
			body: JSON.stringify({
				path: "hello.txt",
				content: "updated\n"
			})
		});
		assert.equal(saved.status, 200);
		const uploaded = await fetch(`${base}api/upload`, {
			method: "POST",
			headers: {
				Cookie: cookie,
				"Content-Type": "application/json",
				"X-Devin-Remote-CSRF": csrf
			},
			body: JSON.stringify({
				path: "asset.bin",
				base64: Buffer.from([0, 1, 2, 255]).toString("base64")
			})
		});
		assert.equal(uploaded.status, 201);
		const downloaded = await fetch(`${base}api/file/raw?path=asset.bin`, {
			headers: {
				Cookie: cookie
			}
		});
		assert.equal(downloaded.status, 200);
		assert.deepEqual([...new Uint8Array(await downloaded.arrayBuffer())], [0, 1, 2, 255]);
	} finally {
		await gateway.stop();
		await Promise.all([
			rm(root, {
				recursive: true,
				force: true
			}),
			rm(assets, {
				recursive: true,
				force: true
			})
		]);
	}
});
test("gateway serves the full remote-control API surface", async () => {
	const upstream = await mkdtemp(join(tmpdir(), "devin-gateway-origin-"));
	const work = await mkdtemp(join(tmpdir(), "devin-gateway-work-"));
	const assets = await mkdtemp(join(tmpdir(), "devin-gateway-assets2-"));
	const root = join(work, "repo");
	execFileSync("git", ["init", "--bare", join(upstream, "origin.git")]);
	execFileSync("git", ["clone", join(upstream, "origin.git"), root]);
	execFileSync("git", ["-C", root, "config", "user.email", "ci@example.com"]);
	execFileSync("git", ["-C", root, "config", "user.name", "CI"]);
	await writeFile(join(root, "tracked.txt"), "alpha\nbeta\n", "utf8");
	await writeFile(join(root, "sample.js"), "export function helper() { return 1; }\nexport class Widget {}\n", "utf8");
	await writeFile(join(root, "package.json"), JSON.stringify({
		scripts: {
			test: "node --test"
		}
	}), "utf8");
	await writeFile(join(root, "pyproject.toml"), "[project]\nname = 'demo'\n", "utf8");
	await writeFile(join(root, "Makefile"), "build:\n\techo ok\n.PHONY: build\n", "utf8");
	execFileSync("git", ["-C", root, "add", "-A"]);
	execFileSync("git", ["-C", root, "commit", "-m", "initial"]);
	execFileSync("git", ["-C", root, "push", "-u", "origin", "HEAD"]);
	const mainBranch = String(execFileSync("git",
		["-C", root, "branch", "--show-current"])).trim();
	const project = {
		id: "abcdef123456",
		name: "surface",
		root,
		gitRemote: "origin"
	};
	await writeFile(join(assets, "index.html"), "<!doctype html><title>app</title>", "utf8");
	await mkdir(join(assets, "js"), {
		recursive: true
	});
	await writeFile(join(assets, "js", "service-worker.js"), "const scope = '__PROJECT_SCOPE__';", "utf8");
	await writeFile(join(assets, "app.js"), "console.log('app');", "utf8");
	await writeFile(join(assets, "style.css"), "body { margin: 0; }", "utf8");
	await writeFile(join(assets, "icon.svg"), "<svg xmlns='http://www.w3.org/2000/svg'/>", "utf8");
	await writeFile(join(assets, "data.bin"), "binary", "utf8");
	const security = new SecurityService(12);
	const terminal = /** @type {any} */ (new EventEmitter());
	terminal.snapshot = () => ({
		id: "term-1",
		shell: "/bin/zsh",
		running: true
	});
	terminal.write = () => {};
	terminal.close = () => {};
	let devinCalls = 0;
	const gateway = new GatewayServer({
		registry: /** @type {any} */ ({
			get: /** @type {(id: string) => any} */ (id => (id === project.id ? project : undefined)),
			publicList: () => [{
				id: project.id,
				name: project.name,
				gitRemote: "origin"
			}],
			diagnostics: () => [{
				level: "info",
				message: "all good"
			}],
			openLocation: ( /** @type {string} id */ _id, /** @type {string} file */ file) => ({
				path: file
			})
		}),
		security,
		files: new FileService(1024 * 1024, []),
		devin: /** @type {any} */ ({
			configured: () => true,
			listSessions: async () => {
				devinCalls += 1;
				if (devinCalls === 1) throw new Error("devin temporarily down");
				return [{
					id: "devin-mock-1",
					status: "running"
				}];
			},
			createSession: async () => ({
				id: "devin-new-1"
			}),
			messages: async () => [{
				role: "user",
				content: "hello"
			}],
			sendMessage: async () => ({
				ok: true
			}),
			attachments: async () => [{
				name: "log.txt"
			}],
			tags: async () => ({
				tags: ["review"]
			}),
			insights: async () => ({
				summary: "healthy"
			}),
			generateInsights: async () => ({
				queued: true
			}),
			replaceTags: async () => ({
				tags: ["a", "b"]
			}),
			archive: async () => ({
				archived: true
			}),
			terminate: async () => ({
				stopped: true
			})
		}),
		providers: /** @type {any} */ ({
			providers: async () => [{
				id: "mock",
				models: ["m1"]
			}],
			chat: async () => ({
				text: "reply",
				model: "m1"
			})
		}),
		terminals: /** @type {any} */ ({
			list: () => [{
				id: "term-1"
			}],
			create: () => terminal,
			get: () => terminal,
			closeAll: () => {}
		}),
		assetsRoot: assets
	});
	try {
		const port = await gateway.start(0);
		const origin = `http://127.0.0.1:${port}`;
		const base = `${origin}/p/${project.id}/`;
		const pairing = security.createPairing(project.id);
		const paired = await fetch(`${base}api/pair`, {
			method: "POST",
			headers: {
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				code: pairing.code
			})
		});
		assert.equal(paired.status, 200);
		const cookie = paired.headers.get("set-cookie")?.split(";", 1)[0] ?? "";
		let csrf = String((await paired.json()).csrf ?? "");
		/**
		 * Call an authenticated API endpoint.
		 * @param {string} suffix API path suffix.
		 * @param {{method?: string, body?: unknown, raw?: string}} [options] Request options.
		 * @returns {Promise<Response>} The fetch response.
		 */
		const api = (suffix, options = {}) => fetch(`${base}api${suffix}`, {
			method: options.method ?? "GET",
			headers: {
				Cookie: cookie,
				...(["GET", "HEAD"].includes(options.method ?? "GET") ? {} : {
					"Content-Type": "application/json",
					"X-Devin-Remote-CSRF": csrf
				})
			},
			...(options.raw === undefined ? (options.body === undefined ? {} : {
				body: JSON.stringify(options.body)
			}) : {
				body: options.raw
			})
		});
		// Unauthenticated surface: health, app shell, manifest, worker, assets, 404s.
		assert.equal((await fetch(`${origin}/health`)).status, 200);
		assert.equal((await fetch(base)).status, 200);
		assert.equal((await fetch(`${base}service-worker.js`)).status, 200);
		const manifest = await fetch(`${base}manifest.webmanifest`);
		assert.equal(manifest.status, 200);
		assert.equal((await manifest.json()).name, "surface · Devin Remote");
		assert.equal((await fetch(`${origin}/assets/icon.svg`)).status, 200);
		assert.equal((await fetch(`${origin}/assets/app.js`)).status, 200);
		assert.equal((await fetch(`${origin}/assets/style.css`)).status, 200);
		assert.equal((await fetch(`${origin}/assets/data.bin`)).status, 200);
		assert.equal((await fetch(`${origin}/assets/bad%20name`)).status, 404);
		assert.equal((await fetch(`${origin}/assets/missing.svg`)).status, 404);
		assert.equal((await fetch(`${base}not-api`)).status, 404);
		assert.equal((await fetch(`${origin}/outside`)).status, 404);
		assert.equal((await fetch(`${origin}/p/000000000000/`)).status, 200);
		assert.equal((await fetch(`${origin}/p/000000000000/api/session`, {
			headers: {
				Cookie: cookie
			}
		})).status, 403);
		assert.equal((await fetch(`${origin}/p/000000000000/api/session`)).status, 401);
		const missingCsrf = await fetch(`${base}api/file`, {
			method: "PUT",
			headers: {
				Cookie: cookie,
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				path: "tracked.txt",
				content: "x"
			})
		});
		assert.equal(missingCsrf.status, 403);
		// Event stream stays open so mutations broadcast to it.
		const eventsController = new AbortController();
		const eventsResponse = await fetch(`${base}api/events`, {
			headers: {
				Cookie: cookie
			},
			signal: eventsController.signal
		});
		assert.equal(eventsResponse.status, 200);
		// Session and file surface.
		const sessionResponse = await api("/session");
		assert.equal(sessionResponse.status, 200);
		csrf = String((await sessionResponse.json()).csrf ?? csrf);
		assert.equal((await api("/bootstrap")).status, 200);
		assert.equal((await api("/tree?path=")).status, 200);
		assert.equal((await api("/file?path=tracked.txt")).status, 200);
		assert.equal((await api("/file")).status, 400);
		assert.equal((await api("/file/raw?path=tracked.txt")).status, 200);
		assert.equal((await api("/upload", {
			method: "POST",
			body: {
				path: "blob.bin",
				base64: Buffer.from("blob").toString("base64"),
				overwrite: true
			}
		})).status, 201);
		assert.equal((await api("/file", {
			method: "PUT",
			body: {
				path: "tracked.txt",
				content: "alpha\nbeta\ngamma\n"
			}
		})).status, 200);
		assert.equal((await api("/file", {
			method: "PUT",
			body: {
				path: "tracked.txt",
				content: "x",
				expectedMtimeMs: 1
			}
		})).status, 409);
		assert.equal((await api("/directory", {
			method: "POST",
			body: {
				path: "docs"
			}
		})).status, 201);
		assert.equal((await api("/rename", {
			method: "POST",
			body: {
				path: "docs",
				nextPath: "guides"
			}
		})).status, 200);
		assert.equal((await api("/copy", {
			method: "POST",
			body: {
				path: "sample.js",
				nextPath: "copy.js"
			}
		})).status, 201);
		assert.equal((await api("/file?path=copy.js", {
			method: "DELETE"
		})).status, 200);
		assert.equal((await api("/workspace/stats")).status, 200);
		assert.equal((await api("/file/inspect?path=sample.js")).status, 200);
		assert.equal((await api("/search?q=alpha")).status, 200);
		// Git read surface.
		assert.equal((await api("/git/status")).status, 200);
		assert.equal((await api("/git/diff?path=tracked.txt")).status, 200);
		const logResponse = await api("/git/log");
		assert.equal(logResponse.status, 200);
		const headHash = String((await logResponse.json()).items?.[0]?.hash ?? "HEAD");
		assert.equal((await api("/git/branches")).status, 200);
		assert.equal((await api(`/git/commit?hash=${headHash}`)).status, 200);
		assert.equal((await api("/git/blame?path=tracked.txt")).status, 200);
		assert.equal((await api("/git/file-history?path=tracked.txt")).status, 200);
		assert.equal((await api(`/git/file-revision?path=tracked.txt&hash=${headHash}`)).status, 200);
		assert.equal((await api("/git/compare?base=HEAD")).status, 200);
		assert.equal((await api("/git/stashes")).status, 200);
		// Git write surface.
		assert.equal((await api("/git/stage", {
			method: "POST",
			body: {
				path: "tracked.txt"
			}
		})).status, 200);
		assert.equal((await api("/git/unstage", {
			method: "POST",
			body: {
				path: "tracked.txt"
			}
		})).status, 200);
		assert.equal((await api("/git/stage-all", {
			method: "POST"
		})).status, 200);
		assert.equal((await api("/git/unstage-all", {
			method: "POST"
		})).status, 200);
		assert.equal((await api("/git/stage", {
			method: "POST",
			body: {
				path: "tracked.txt"
			}
		})).status, 200);
		assert.equal((await api("/git/commit", {
			method: "POST",
			body: {
				message: "second commit"
			}
		})).status, 200);
		assert.equal((await api("/git/branch", {
			method: "POST",
			body: {
				name: "feature-x",
				checkout: true
			}
		})).status, 201);
		assert.equal((await api("/git/checkout", {
			method: "POST",
			body: {
				name: mainBranch
			}
		})).status, 200);
		await writeFile(join(root, "tracked.txt"), "dirty\n", "utf8");
		assert.equal((await api("/git/stash", {
			method: "POST",
			body: {
				message: "wip"
			}
		})).status, 201);
		assert.equal((await api("/git/stash/apply", {
			method: "POST",
			body: {
				ref: "stash@{0}",
				drop: true
			}
		})).status, 200);
		assert.equal((await api("/git/stash/apply", {
			method: "POST",
			body: {
				ref: ""
			}
		})).status, 400);
		await writeFile(join(root, "tracked.txt"), "dirty again\n", "utf8");
		assert.equal((await api("/git/discard", {
			method: "POST",
			body: {
				path: "tracked.txt"
			}
		})).status, 200);
		assert.equal((await api("/git/pull", {
			method: "POST"
		})).status, 200);
		assert.equal((await api("/git/push", {
			method: "POST"
		})).status, 200);
		// Agent session surface.
		assert.equal((await api("/agents")).status, 200);
		assert.equal((await api("/agents", {
			method: "POST",
			body: {}
		})).status, 400);
		assert.equal((await api("/agents", {
			method: "POST",
			body: {
				prompt: "Fix the tests",
				title: "Fix",
				maxAcuLimit: 5,
				repo: "r",
				tags: ["t"]
			}
		})).status, 201);
		assert.equal((await api("/agents/devin-mock-1/messages")).status, 200);
		assert.equal((await api("/agents/devin-mock-1/messages", {
			method: "POST",
			body: {
				message: "ping"
			}
		})).status, 200);
		assert.equal((await api("/agents/devin-mock-1/messages", {
			method: "POST",
			body: {}
		})).status, 400);
		assert.equal((await api("/agents/devin-mock-1/details")).status, 200);
		assert.equal((await api("/agents/devin-mock-1/insights/generate", {
			method: "POST"
		})).status, 200);
		assert.equal((await api("/agents/devin-mock-1/tags", {
			method: "PUT",
			body: {
				tags: [" a ", "b", "", "a"]
			}
		})).status, 200);
		assert.equal((await api("/agents/devin-mock-1/archive", {
			method: "POST"
		})).status, 200);
		assert.equal((await api("/agents/devin-mock-1/terminate", {
			method: "POST",
			body: {
				archive: true
			}
		})).status, 200);
		// Workspace services.
		assert.equal((await api("/diagnostics")).status, 200);
		assert.equal((await api("/vscode/open", {
			method: "POST",
			body: {
				path: "tracked.txt",
				line: 2,
				column: 3
			}
		})).status, 200);
		assert.equal((await api("/activity")).status, 200);
		const securitySessions = await api("/security/sessions");
		assert.equal(securitySessions.status, 200);
		const deviceId = String((await securitySessions.json()).currentId ?? "");
		assert.ok(deviceId);
		assert.equal((await api("/tasks")).status, 200);
		assert.equal((await api("/system")).status, 200);
		assert.equal((await api("/providers")).status, 200);
		assert.equal((await api("/llm/chat", {
			method: "POST",
			body: {
				provider: "mock",
				messages: [{
					role: "user",
					content: "hi"
				}],
				model: "m1"
			}
		})).status, 200);
		assert.equal((await api("/llm/chat", {
			method: "POST",
			body: {
				messages: []
			}
		})).status, 400);
		// Terminal surface.
		assert.equal((await api("/terminal")).status, 200);
		assert.equal((await api("/terminal", {
			method: "POST"
		})).status, 201);
		assert.equal((await api("/terminal/term-1/input", {
			method: "POST",
			body: {
				input: "ls\n"
			}
		})).status, 200);
		const terminalEventsController = new AbortController();
		const terminalEvents = await fetch(`${base}api/terminal/term-1/events`, {
			headers: {
				Cookie: cookie
			},
			signal: terminalEventsController.signal
		});
		assert.equal(terminalEvents.status, 200);
		assert.equal((await api("/terminal/term-1", {
			method: "DELETE"
		})).status, 200);
		// Remaining error surface.
		assert.equal((await api("/nope")).status, 404);
		assert.equal((await api("/agents", {
			method: "POST",
			raw: "{invalid"
		})).status, 400);
		gateway.notifyProject(project.id, "files", {
			action: "test"
		});
		terminalEventsController.abort();
		// Revoking the current device invalidates the session.
		assert.equal((await api(`/security/sessions/${deviceId}`, {
			method: "DELETE"
		})).status, 200);
		assert.equal((await api("/session")).status, 401);
		eventsController.abort();
		assert.ok(eventsResponse.body);
	} finally {
		await gateway.stop();
		await Promise.all([
			rm(upstream, {
				recursive: true,
				force: true
			}),
			rm(work, {
				recursive: true,
				force: true
			}),
			rm(assets, {
				recursive: true,
				force: true
			})
		]);
	}
});
