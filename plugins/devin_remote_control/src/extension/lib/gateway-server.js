"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
const fs = require("node:fs/promises");
const http = require("node:http");
const path = require("node:path");
const {
	EventEmitter
} = require("node:events");
const {
	ActivityService
} = require("./activity-service");
const {
	Constants
} = require("./constants");
const {
	HttpError,
	UpstreamError
} = require("./errors");
const {
	FileService
} = require("./file-service");
const {
	GitService
} = require("./git-service");
const {
	TaskService
} = require("./task-service");
const {
	SecurityService
} = require("./security");
/**
 * @typedef {InstanceType<typeof import("./project-registry").ProjectRegistry>} Registry
 * @typedef {InstanceType<typeof import("./security").SecurityService>} Security
 * @typedef {InstanceType<typeof import("./file-service").FileService>} Files
 * @typedef {InstanceType<typeof import("./devin-client").DevinClient>} Devin
 * @typedef {InstanceType<typeof import("./provider-service").ProviderService>} Providers
 * @typedef {InstanceType<typeof import("./shell-session").TerminalManager>} Terminals
 * @typedef {InstanceType<typeof import("./activity-service").ActivityService>} Activity
 * @typedef {object} Project
 * @property {string} id Stable twelve-character project identifier.
 * @property {string} name Display name.
 * @property {string} root Workspace folder path.
 * @property {string} gitRemote Upstream remote URL.
 * @typedef {object} Session
 * @property {string} id Session identifier.
 * @property {string} projectId Owning project identifier.
 * @property {string} csrfHash Hashed CSRF token.
 * @property {number} expiresAt Epoch milliseconds when the session expires.
 * @property {number} createdAt Creation timestamp.
 * @property {number} lastSeenAt Last activity timestamp.
 * @property {string} remoteAddress Client address.
 * @property {string} userAgent Client user agent.
 * @typedef {object} GatewayOptions
 * @property {Registry} registry Project registry.
 * @property {Security} security Pairing and session service.
 * @property {Files} files File service.
 * @property {Devin} devin Devin API client.
 * @property {Providers} providers LLM provider service.
 * @property {Terminals} terminals Terminal manager.
 * @property {Activity} [activity] Activity log service.
 * @property {string} assetsRoot Web asset directory.
 */
/** Local HTTP/WebSocket gateway for the webview app. */
class GatewayServer extends EventEmitter {
	/**
	 * Creates an instance of GatewayServer.
	 * @param {GatewayOptions} options Dependencies and configuration.
	 */
	constructor(options) {
		super();
		/** @type {Registry} */
		this.registry = options.registry;
		/** @type {Security} */
		this.security = options.security;
		/** @type {Files} */
		this.files = options.files;
		/** @type {Devin} */
		this.devin = options.devin;
		/** @type {Providers} */
		this.providers = options.providers;
		/** @type {Terminals} */
		this.terminals = options.terminals;
		/** @type {Activity} */
		this.activity = options.activity ?? new ActivityService();
		/** @type {string} */
		this.assetsRoot = options.assetsRoot;
		/** @type {import("node:http").Server | null} */
		this.server = null;
		/** @type {number} */
		this.port = 0;
		/** @type {Record<string, unknown>[]} */
		this.agentCache = [];
		/** @type {string} */
		this.agentError = "";
		/** @type {Map<string, Set<import("node:http").ServerResponse>>} */
		this.clients = new Map();
		/** @type {NodeJS.Timeout | null} */
		this.agentTimer = null;
		/** @type {NodeJS.Timeout | null} */
		this.cleanupTimer = null;
	}
	/**
	 * Starts the HTTP server on the requested port.
	 * @param {number} port Port.
	 * @returns {Promise<number>} The bound port.
	 */
	async start(port = 0) {
		if (this.server) return this.port;
		this.server = http.createServer((request, response) => {
			this.handle(request, response).catch(
				/** @param {unknown} error Failure. */
				error => GatewayServer.error(response, error));
		});
		const server = this.server;
		await new Promise((resolve, reject) => {
			server.once("error", reject);
			server.listen(port, "127.0.0.1", () => {
				server.off("error", reject);
				resolve(null);
			});
		});
		const address = server.address();
		this.port = typeof address === "object" && address ? address.port : port;
		this.agentTimer = setInterval(() => this.refreshAgents(), Constants.agentRefreshMs);
		this.cleanupTimer = setInterval(() => this.security.cleanup(), 60000);
		this.refreshAgents();
		this.emit("change", this.snapshot());
		return this.port;
	}
	/**
	 * Returns the public gateway status snapshot.
	 * @returns {{running: boolean, port: number, localUrl: string}} The status snapshot.
	 */
	snapshot() {
		return {
			running: Boolean(this.server),
			port: this.port,
			localUrl: this.server ? `http://127.0.0.1:${this.port}` : ""
		};
	}
	/** Refreshes agents. */
	async refreshAgents() {
		if (!this.devin.configured()) return;
		try {
			this.agentCache = await this.devin.listSessions(false);
			this.agentError = "";
			this.broadcastAll("agents", {
				sessions: this.agentCache
			});
		} catch (error) {
			this.agentError = error instanceof Error ? getAttribute(error, "message") : String(error);
			this.broadcastAll("upstream-error", {
				service: "Devin",
				message: this.agentError
			});
		}
	}
	/** Stops. */
	async stop() {
		if (!this.server) return;
		if (this.agentTimer) clearInterval(this.agentTimer);
		if (this.cleanupTimer) clearInterval(this.cleanupTimer);
		this.clients.forEach(group => {
			group.forEach(response => {
				response.end();
			});
		});
		this.clients.clear();
		this.terminals.closeAll();
		await new Promise(resolve => {
			this.server?.close(() => resolve(null));
		});
		this.server = null;
		this.port = 0;
		this.agentTimer = null;
		this.cleanupTimer = null;
		this.emit("change", this.snapshot());
	}
	/**
	 * Notifies project.
	 * @param {string} projectId ProjectId.
	 * @param {string} event Event.
	 * @param {Record<string, unknown>} data Data.
	 */
	notifyProject(projectId, event, data = {}) {
		this.broadcast(projectId, event, data);
	}
	/**
	 * Records an activity entry and broadcasts it to project clients.
	 * @param {string} projectId ProjectId.
	 * @param {string} type Type.
	 * @param {string} title Title.
	 * @param {string} detail Detail.
	 * @param {Record<string, unknown>} metadata Metadata.
	 * @returns {unknown} The recorded activity item.
	 */
	record(projectId, type, title, detail = "", metadata = {}) {
		const item = this.activity.record(projectId, type, title, detail, metadata);
		this.broadcast(projectId, "activity", {
			item
		});
		return item;
	}
	/**
	 * Broadcasts all.
	 * @param {string} event Event.
	 * @param {unknown} data Data.
	 */
	broadcastAll(event, data) {
		this.clients.forEach((_, projectId) => {
			this.broadcast(projectId, event, data);
		});
	}
	/**
	 * Broadcasts.
	 * @param {string} projectId ProjectId.
	 * @param {string} event Event.
	 * @param {unknown} data Data.
	 */
	broadcast(projectId, event, data) {
		const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
		this.clients.get(projectId)?.forEach(response => {
			response.write(payload);
		});
	}
	/**
	 * Handles an incoming HTTP request.
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @returns {Promise<void>} Nothing.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async handle(request, response) {
		try {
			const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
			GatewayServer.applySecurityHeaders(response);
			if (url.pathname === "/health") return GatewayServer.json(response, 200, this.snapshot());
			if (url.pathname.startsWith("/assets/")) return this.serveAsset(url.pathname, response);
			const match = /^\/p\/([a-f0-9]{12})(\/.*)?$/u.exec(url.pathname);
			if (!match) throw new HttpError(404, "Not found.");
			const projectId = match[1] ?? "";
			const suffix = match[2] || "/";
			const project = this.registry.get(projectId);
			if (suffix === "/" && request.method === "GET") return this.serveApp(response);
			if (suffix === "/service-worker.js" && request.method === "GET") return this.serveServiceWorker(response, getAttribute(project, "id"));
			if (suffix === "/manifest.webmanifest" && request.method === "GET") return GatewayServer.json(response, 200, {
				name: `${getAttribute(project, "name")} · Devin Remote`,
				short_name: "Devin Remote",
				start_url: `/p/${getAttribute(project, "id")}/`,
				scope: `/p/${getAttribute(project, "id")}/`,
				display: "standalone",
				background_color: "#080b10",
				theme_color: "#080b10",
				shortcuts: [{
					name: "Agents",
					short_name: "Agents",
					url: `/p/${getAttribute(project, "id")}/#agents`
				}, {
					name: "Code",
					short_name: "Code",
					url: `/p/${getAttribute(project, "id")}/#files`
				}, {
					name: "Git",
					short_name: "Git",
					url: `/p/${getAttribute(project, "id")}/#diff`
				}, {
					name: "Runtime",
					short_name: "Runtime",
					url: `/p/${getAttribute(project, "id")}/#terminal`
				}],
				icons: [{
					src: "/assets/icon.svg",
					sizes: "any",
					type: "image/svg+xml",
					purpose: "any maskable"
				}]
			}, "application/manifest+json; charset=utf-8");
			if (!suffix.startsWith("/api/")) throw new HttpError(404, "Not found.");
			if (suffix === "/api/pair" && request.method === "POST") return this.pair(request, response, project);
			const session = this.security.authenticate(getAttribute(project, "id"), GatewayServer.cookie(request, Constants.cookieName));
			if (!["GET", "HEAD"].includes(request.method ?? "")) SecurityService.authorizeMutation(session, String(request.headers["x-devin-remote-csrf"] ?? ""));
			return this.api(request, response, url, suffix, project, session);
		} catch (error) {
			return GatewayServer.error(response, error);
		}
	}
	/** @param {import("node:http").ServerResponse} response Response. */
	static applySecurityHeaders = response => {
		Object.entries({
			"Cache-Control": Constants.noCache,
			"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
			"Cross-Origin-Opener-Policy": "same-origin",
			"Cross-Origin-Resource-Policy": "same-origin",
			"Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
			"Referrer-Policy": "no-referrer",
			"X-Content-Type-Options": "nosniff",
			"X-Frame-Options": "DENY"
		}).forEach(pair => {
			response.setHeader(String(pair[0]), String(pair[1]));
		});
	};
	/**
	 * Serves asset.
	 * @param {string} pathname Pathname.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async serveAsset(pathname, response) {
		const name = pathname.slice("/assets/".length);
		if (!/^[a-z0-9._-]+$/iu.test(name)) throw new HttpError(404, "Asset not found.");
		const types = new Map([
			[".css", "text/css; charset=utf-8"],
			[".js", "text/javascript; charset=utf-8"],
			[".svg", "image/svg+xml; charset=utf-8"]
		]);
		const filename = path.join(this.assetsRoot, name);
		let content;
		try {
			content = await fs.readFile(filename);
		} catch (error) {
			if (error instanceof Error && /** @type {NodeJS.ErrnoException} */ (error).code === "ENOENT") throw new HttpError(404, "Asset not found.");
			throw error;
		}
		response.writeHead(200, {
			"Cache-Control": Constants.publicAssetsCache,
			"Content-Type": types.get(path.extname(name)) ?? "application/octet-stream"
		});
		response.end(content);
	}
	/**
	 * Serves app.
	 * @param {import("node:http").ServerResponse} response Response.
	 */
	async serveApp(response) {
		const content = await fs.readFile(path.join(this.assetsRoot, "index.html"));
		response.writeHead(200, {
			"Content-Type": "text/html; charset=utf-8"
		});
		response.end(content);
	}
	/**
	 * Serves service worker.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {string} projectId ProjectId.
	 */
	async serveServiceWorker(response, projectId) {
		const source = await fs.readFile(path.join(this.assetsRoot, "js", "service-worker.js"), "utf8");
		response.writeHead(200, {
			"Content-Type": "text/javascript; charset=utf-8",
			"Service-Worker-Allowed": `/p/${projectId}/`
		});
		response.end(source.replaceAll("__PROJECT_SCOPE__", `/p/${projectId}/`));
	}
	/**
	 * Pairs.
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {Project} project Project.
	 * @returns {Promise<void>} Nothing.
	 */
	async pair(request, response, project) {
		const body = await GatewayServer.body(request);
		const paired = this.security.pair(getAttribute(project, "id"), String(getAttribute(body, "code") ?? ""), GatewayServer.remoteAddress(request), String(request.headers["user-agent"] ?? ""));
		response.setHeader("Set-Cookie", [`${Constants.cookieName}=${paired.token}; Path=/p/${getAttribute(project, "id")}/; HttpOnly; Secure; SameSite=Strict; Max-Age=${Math.floor((paired.expiresAt - Date.now()) / 1000)}`]);
		return GatewayServer.json(response, 200, {
			csrf: paired.csrf,
			expiresAt: paired.expiresAt
		});
	}
	/**
	 * Routes an authenticated API request.
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {URL} url Url.
	 * @param {string} suffix Suffix.
	 * @param {Project} project Project.
	 * @param {Session} session Session.
	 * @returns {Promise<void>} Nothing.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async api(request, response, url, suffix, project, session) {
		const method = request.method ?? "GET";
		if (suffix === "/api/session" && method === "GET") return GatewayServer.json(response, 200, {
			csrf: SecurityService.issueCsrf(session),
			expiresAt: session.expiresAt
		});
		if (suffix === "/api/bootstrap" && method === "GET") return this.bootstrap(response, project, session);
		if (suffix === "/api/events" && method === "GET") return this.events(request, response, getAttribute(project, "id"));
		if (suffix === "/api/tree" && method === "GET") return GatewayServer.json(response, 200, {
			items: await this.files.list(project, url.searchParams.get("path") ?? "")
		});
		if (suffix === "/api/file" && method === "GET") return GatewayServer.json(response, 200, await this.files.read(project, GatewayServer.requiredQuery(url, "path")));
		if (suffix === "/api/file/raw" && method === "GET") {
			const file = await this.files.readRaw(project, GatewayServer.requiredQuery(url, "path"));
			response.writeHead(200, {
				"Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(path.basename(getAttribute(file, "path")))}`,
				"Content-Length": String(file.size),
				"Content-Type": file.contentType
			});
			response.end(getAttribute(file, "content"));
			return undefined;
		}
		if (suffix === "/api/upload" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await this.files.writeBase64(project, String(getAttribute(body, "path") ?? ""), String(getAttribute(body, "base64") ?? ""), Boolean(getAttribute(body, "overwrite")));
			this.record(getAttribute(project, "id"), "files", "Uploaded file", getAttribute(result, "path"));
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "upload",
				path: getAttribute(result, "path")
			});
			return GatewayServer.json(response, 201, result);
		}
		if (suffix === "/api/file" && method === "PUT") {
			const body = await GatewayServer.body(request);
			const result = await this.files.write(project, String(getAttribute(body, "path") ?? ""), String(getAttribute(body, "content") ?? ""), Number.isFinite(Number(getAttribute(body, "expectedMtimeMs"))) ? Number(getAttribute(body, "expectedMtimeMs")) : null);
			this.record(getAttribute(project, "id"), "files", "Saved file", getAttribute(result, "path"));
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "write",
				path: getAttribute(result, "path")
			});
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/file" && method === "DELETE") {
			const result = await FileService.remove(project, GatewayServer.requiredQuery(url, "path"));
			this.record(getAttribute(project, "id"), "files", "Removed item", result.removed);
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "remove",
				path: result.removed
			});
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/directory" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await FileService.createDirectory(project, String(getAttribute(body, "path") ?? ""));
			this.record(getAttribute(project, "id"), "files", "Created folder", getAttribute(result, "path"));
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "mkdir",
				path: getAttribute(result, "path")
			});
			return GatewayServer.json(response, 201, result);
		}
		if (suffix === "/api/rename" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await FileService.rename(project, String(getAttribute(body, "path") ?? ""), String(getAttribute(body, "nextPath") ?? ""));
			this.record(getAttribute(project, "id"), "files", "Renamed item", `${getAttribute(body, "path") ?? ""} → ${getAttribute(result, "path")}`);
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "rename",
				path: getAttribute(result, "path")
			});
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/copy" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await FileService.copy(project, String(getAttribute(body, "path") ?? ""), String(getAttribute(body, "nextPath") ?? ""));
			this.record(getAttribute(project, "id"), "files", "Copied item", `${getAttribute(body, "path") ?? ""} → ${getAttribute(result, "path")}`);
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "copy",
				path: getAttribute(result, "path")
			});
			return GatewayServer.json(response, 201, result);
		}
		if (suffix === "/api/workspace/stats" && method === "GET") return GatewayServer.json(response, 200, await this.files.stats(project));
		if (suffix === "/api/file/inspect" && method === "GET") return GatewayServer.json(response, 200, await this.files.inspect(project, GatewayServer.requiredQuery(url, "path")));
		if (suffix === "/api/search" && method === "GET") return GatewayServer.json(response, 200, {
			items: await this.files.search(project, url.searchParams.get("q") ?? "", Number(url.searchParams.get("limit") ?? 60))
		});
		if (suffix === "/api/git/status" && method === "GET") return GatewayServer.json(response, 200, await new GitService(project).status());
		if (suffix === "/api/git/diff" && method === "GET") return GatewayServer.json(response, 200, await new GitService(project).diff(GatewayServer.requiredQuery(url, "path")));
		if (suffix === "/api/git/stage" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await new GitService(project).stage(String(getAttribute(body, "path") ?? ""));
			this.broadcast(getAttribute(project, "id"), "git", result);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/unstage" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await new GitService(project).unstage(String(getAttribute(body, "path") ?? ""));
			this.broadcast(getAttribute(project, "id"), "git", result);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/log" && method === "GET") return GatewayServer.json(response, 200, {
			items: await new GitService(project).log(Number(url.searchParams.get("limit") ?? 30))
		});
		if (suffix === "/api/git/branches" && method === "GET") return GatewayServer.json(response, 200, {
			items: await new GitService(project).branches()
		});
		if (suffix === "/api/git/commit" && method === "GET") return GatewayServer.json(response, 200, await new GitService(project).commitDetail(GatewayServer.requiredQuery(url, "hash")));
		if (suffix === "/api/git/blame" && method === "GET") return GatewayServer.json(response, 200, {
			items: await new GitService(project).blame(GatewayServer.requiredQuery(url, "path"))
		});
		if (suffix === "/api/git/file-history" && method === "GET") return GatewayServer.json(response, 200, {
			items: await new GitService(project).fileHistory(GatewayServer.requiredQuery(url, "path"), Number(url.searchParams.get("limit") ?? 40))
		});
		if (suffix === "/api/git/file-revision" && method === "GET") return GatewayServer.json(response, 200, await new GitService(project).fileAtRevision(GatewayServer.requiredQuery(url, "path"), GatewayServer.requiredQuery(url, "hash")));
		if (suffix === "/api/git/compare" && method === "GET") return GatewayServer.json(response, 200, await new GitService(project).compare(GatewayServer.requiredQuery(url, "base"), url.searchParams.get("head") ?? "HEAD"));
		if (suffix === "/api/git/stashes" && method === "GET") return GatewayServer.json(response, 200, {
			items: await new GitService(project).stashes()
		});
		if (suffix === "/api/git/stash" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await new GitService(project).stash(String(getAttribute(body, "message") ?? "Remote workspace stash"));
			this.record(getAttribute(project, "id"), "git", "Stashed changes", String(getAttribute(body, "message") ?? "Remote workspace stash"));
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			return GatewayServer.json(response, 201, result);
		}
		if (suffix === "/api/git/stash/apply" && method === "POST") {
			const body = await GatewayServer.body(request);
			const ref = String(getAttribute(body, "ref") ?? "").trim();
			if (!ref) throw new HttpError(400, "Stash reference is required.");
			const result = await new GitService(project).applyStash(ref, Boolean(getAttribute(body, "drop")));
			this.record(getAttribute(project, "id"), "git", getAttribute(body, "drop") ? "Popped stash" : "Applied stash", ref);
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "stash",
				path: ""
			});
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/branch" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await new GitService(project).createBranch(String(getAttribute(body, "name") ?? ""), getAttribute(body, "checkout") !== false);
			this.record(getAttribute(project, "id"), "git", "Created branch", String(getAttribute(body, "name") ?? ""));
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			return GatewayServer.json(response, 201, result);
		}
		if (suffix === "/api/git/checkout" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await new GitService(project).checkout(String(getAttribute(body, "name") ?? ""));
			this.record(getAttribute(project, "id"), "git", "Switched branch", String(getAttribute(body, "name") ?? ""));
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/commit" && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await new GitService(project).commit(String(getAttribute(body, "message") ?? ""));
			this.record(getAttribute(project, "id"), "git", "Created commit", String(getAttribute(body, "message") ?? ""));
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/pull" && method === "POST") {
			const result = await new GitService(project).pull();
			this.record(getAttribute(project, "id"), "git", "Pulled changes", result.output);
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/push" && method === "POST") {
			const result = await new GitService(project).push();
			this.record(getAttribute(project, "id"), "git", "Pushed changes", result.output);
			this.broadcast(getAttribute(project, "id"), "git", result.status);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/discard" && method === "POST") {
			const body = await GatewayServer.body(request);
			const file = String(getAttribute(body, "path") ?? "");
			const result = await new GitService(project).discard(file);
			this.record(getAttribute(project, "id"), "git", "Discarded changes", file);
			this.broadcast(getAttribute(project, "id"), "git", result);
			this.broadcast(getAttribute(project, "id"), "files", {
				action: "discard",
				path: file
			});
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/stage-all" && method === "POST") {
			const result = await new GitService(project).stageAll();
			this.record(getAttribute(project, "id"), "git", "Staged all changes");
			this.broadcast(getAttribute(project, "id"), "git", result);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/git/unstage-all" && method === "POST") {
			const result = await new GitService(project).unstageAll();
			this.record(getAttribute(project, "id"), "git", "Unstaged all changes");
			this.broadcast(getAttribute(project, "id"), "git", result);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/agents" && method === "GET") return GatewayServer.json(response, 200, {
			sessions: this.agentCache,
			error: this.agentError
		});
		if (suffix === "/api/agents" && method === "POST") {
			const body = await GatewayServer.body(request);
			if (!String(getAttribute(body, "prompt") ?? "").trim()) throw new HttpError(400, "Prompt is required.");
			const result = await this.devin.createSession({
				prompt: String(getAttribute(body, "prompt")),
				title: getAttribute(body, "title") ? String(getAttribute(body, "title")) : "",
				maxAcuLimit: Number(getAttribute(body, "maxAcuLimit")) || null,
				repo: getAttribute(body, "repo") ? String(getAttribute(body, "repo")) : "",
				tags: Array.isArray(getAttribute(body, "tags")) ? getAttribute(body, "tags").map(String) : []
			}, project);
			this.record(getAttribute(project, "id"), "agent", "Started Devin session", String(getAttribute(body, "title") ?? getAttribute(body, "prompt") ?? "").slice(0, 160));
			this.refreshAgents();
			return GatewayServer.json(response, 201, result);
		}
		const messageMatch = /^\/api\/agents\/(devin-[A-Za-z0-9_-]+)\/messages$/u.exec(suffix);
		const messageSession = messageMatch?.[1] ?? "";
		if (messageMatch && method === "GET") return GatewayServer.json(response, 200, {
			items: await this.devin.messages(messageSession)
		});
		if (messageMatch && method === "POST") {
			const body = await GatewayServer.body(request);
			const message = String(getAttribute(body, "message") ?? "").trim();
			if (!message) throw new HttpError(400, "Message is required.");
			const result = await this.devin.sendMessage(messageSession, message);
			this.record(getAttribute(project, "id"), "agent", "Messaged Devin", message.slice(0, 160), {
				sessionId: messageSession
			});
			this.refreshAgents();
			return GatewayServer.json(response, 200, result);
		}
		const detailsMatch = /^\/api\/agents\/(devin-[A-Za-z0-9_-]+)\/details$/u.exec(suffix);
		if (detailsMatch && method === "GET") {
			const sessionId = detailsMatch[1] ?? "";
			const [attachments, tags, insights] = await Promise.allSettled([
				this.devin.attachments(sessionId),
				this.devin.tags(sessionId),
				this.devin.insights(sessionId)
			]);
			return GatewayServer.json(response, 200, {
				attachments: attachments.status === "fulfilled" ? attachments.value : [],
				tags: tags.status === "fulfilled" ? getAttribute(tags.value, "tags") ?? [] : [],
				insights: insights.status === "fulfilled" ? insights.value : null,
				errors: [attachments, tags, insights].filter(result => result.status === "rejected").map(result => String(result.reason?.message ?? "Unable to load agent detail."))
			});
		}
		const insightMatch = /^\/api\/agents\/(devin-[A-Za-z0-9_-]+)\/insights\/generate$/u.exec(suffix);
		const insightSession = insightMatch?.[1] ?? "";
		if (insightMatch && method === "POST") {
			const result = await this.devin.generateInsights(insightSession);
			this.record(getAttribute(project, "id"), "agent", "Requested Devin insights", insightSession);
			return GatewayServer.json(response, 200, result);
		}
		const tagsMatch = /^\/api\/agents\/(devin-[A-Za-z0-9_-]+)\/tags$/u.exec(suffix);
		const tagsSession = tagsMatch?.[1] ?? "";
		if (tagsMatch && method === "PUT") {
			const body = await GatewayServer.body(request);
			const tags = Array.isArray(getAttribute(body, "tags")) ? [...new Set(getAttribute(body, "tags").map(String).map( /** @type {(tag: string) => string} */ (tag => tag.trim())).filter(Boolean))].slice(0, 64) : [];
			const result = await this.devin.replaceTags(tagsSession, tags);
			this.record(getAttribute(project, "id"), "agent", "Updated Devin tags", tags.join(", "), {
				sessionId: tagsSession
			});
			this.refreshAgents();
			return GatewayServer.json(response, 200, result);
		}
		const archiveMatch = /^\/api\/agents\/(devin-[A-Za-z0-9_-]+)\/archive$/u.exec(suffix);
		const archiveSession = archiveMatch?.[1] ?? "";
		if (archiveMatch && method === "POST") {
			const result = await this.devin.archive(archiveSession);
			this.record(getAttribute(project, "id"), "agent", "Archived Devin session", archiveSession);
			this.refreshAgents();
			return GatewayServer.json(response, 200, result);
		}
		const terminateMatch = /^\/api\/agents\/(devin-[A-Za-z0-9_-]+)\/terminate$/u.exec(suffix);
		const terminateSession = terminateMatch?.[1] ?? "";
		if (terminateMatch && method === "POST") {
			const body = await GatewayServer.body(request);
			const result = await this.devin.terminate(terminateSession, Boolean(getAttribute(body, "archive")));
			this.record(getAttribute(project, "id"), "agent", "Terminated Devin session", terminateSession);
			this.refreshAgents();
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/diagnostics" && method === "GET") return GatewayServer.json(response, 200, {
			items: typeof this.registry.diagnostics === "function" ? this.registry.diagnostics(getAttribute(project, "id")) : []
		});
		if (suffix === "/api/vscode/open" && method === "POST") {
			const body = await GatewayServer.body(request);
			if (typeof this.registry.openLocation !== "function") throw new HttpError(501, "VS Code navigation is unavailable in this host.");
			const result = await this.registry.openLocation(getAttribute(project, "id"), String(getAttribute(body, "path") ?? ""), Number(getAttribute(body, "line") ?? 1), Number(getAttribute(body, "column") ?? 1));
			this.record(getAttribute(project, "id"), "code", "Opened file in VS Code", getAttribute(result, "path"));
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/activity" && method === "GET") return GatewayServer.json(response, 200, {
			items: this.activity.list(getAttribute(project, "id"), Number(url.searchParams.get("limit") ?? 80))
		});
		if (suffix === "/api/security/sessions" && method === "GET") return GatewayServer.json(response, 200, {
			items: this.security.listProjectSessions(getAttribute(project, "id")),
			currentId: getAttribute(session, "id")
		});
		const securitySessionMatch = /^\/api\/security\/sessions\/([A-Za-z0-9_-]+)$/u.exec(suffix);
		const securitySessionId = securitySessionMatch?.[1] ?? "";
		if (securitySessionMatch && method === "DELETE") {
			this.security.revokeSession(getAttribute(project, "id"), securitySessionId);
			this.record(getAttribute(project, "id"), "security", "Revoked paired device", securitySessionId);
			return GatewayServer.json(response, 200, {
				ok: true
			});
		}
		if (suffix === "/api/tasks" && method === "GET") return GatewayServer.json(response, 200, {
			items: await TaskService.discover(project)
		});
		if (suffix === "/api/system" && method === "GET") return GatewayServer.json(response, 200, {
			platform: process.platform,
			arch: process.arch,
			node: process.version,
			uptimeSeconds: Math.round(process.uptime()),
			gatewayPort: this.port
		});
		if (suffix === "/api/providers" && method === "GET") return GatewayServer.json(response, 200, {
			providers: await this.providers.providers()
		});
		if (suffix === "/api/llm/chat" && method === "POST") {
			const body = await GatewayServer.body(request);
			const messages = Array.isArray(getAttribute(body, "messages")) ? getAttribute(body, "messages").slice(-60).map(
				/** @param {Record<string, unknown>} item Item. */
				item => ({
					role: String(getAttribute(item, "role")),
					content: String(getAttribute(item, "content"))
				})) : [];
			if (!messages.length) throw new HttpError(400, "At least one message is required.");
			const result = await this.providers.chat(String(getAttribute(body, "provider") ?? ""), messages, String(getAttribute(body, "model") ?? "").trim());
			this.record(getAttribute(project, "id"), "model", "Project model request", `${getAttribute(body, "provider") ?? ""} · ${getAttribute(result, "model")}`);
			return GatewayServer.json(response, 200, result);
		}
		if (suffix === "/api/terminal" && method === "GET") return GatewayServer.json(response, 200, {
			items: this.terminals.list(getAttribute(project, "id"))
		});
		if (suffix === "/api/terminal" && method === "POST") {
			const terminal = this.terminals.create(project).snapshot();
			this.record(getAttribute(project, "id"), "terminal", "Opened terminal", terminal.shell, {
				terminalId: getAttribute(terminal, "id")
			});
			return GatewayServer.json(response, 201, terminal);
		}
		const terminalMatch = /^\/api\/terminal\/([A-Za-z0-9_-]+)(\/events|\/input)?$/u.exec(suffix);
		const terminalId = terminalMatch?.[1] ?? "";
		if (terminalMatch && terminalMatch[2] === "/events" && method === "GET") return this.terminalEvents(request, response, getAttribute(project, "id"), terminalId);
		if (terminalMatch && terminalMatch[2] === "/input" && method === "POST") {
			const body = await GatewayServer.body(request);
			this.terminals.get(getAttribute(project, "id"), terminalId).write(String(getAttribute(body, "input") ?? ""));
			return GatewayServer.json(response, 200, {
				ok: true
			});
		}
		if (terminalMatch && !terminalMatch[2] && method === "DELETE") {
			this.terminals.get(getAttribute(project, "id"), terminalId).close();
			return GatewayServer.json(response, 200, {
				ok: true
			});
		}
		throw new HttpError(404, "API route not found.");
	}
	/**
	 * Bootstraps.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {Project} project Project.
	 * @param {Session} session Session.
	 * @returns {Promise<void>} Nothing.
	 */
	async bootstrap(response, project, session) {
		const [git, providers, workspace, tasks, gitLog, branches, stashes] = await Promise.all([
			new GitService(project).status(),
			this.providers.providers(),
			this.files.stats(project),
			TaskService.discover(project),
			new GitService(project).log(12),
			new GitService(project).branches(),
			new GitService(project).stashes()
		]);
		const diagnostics = typeof this.registry.diagnostics === "function" ? this.registry.diagnostics(getAttribute(project, "id")) : [];
		return GatewayServer.json(response, 200, {
			project: {
				id: getAttribute(project, "id"),
				name: getAttribute(project, "name"),
				gitRemote: project.gitRemote
			},
			git,
			gitLog,
			branches,
			stashes,
			terminals: typeof this.terminals.list === "function" ? this.terminals.list(getAttribute(project, "id")) : [],
			workspace,
			diagnostics,
			tasks,
			activity: this.activity.list(getAttribute(project, "id"), 30),
			pairedDevices: this.security.listProjectSessions(getAttribute(project, "id")),
			currentDeviceId: getAttribute(session, "id"),
			system: {
				platform: process.platform,
				arch: process.arch,
				node: process.version,
				uptimeSeconds: Math.round(process.uptime()),
				gatewayPort: this.port
			},
			devin: {
				configured: this.devin.configured(),
				error: this.agentError
			},
			sessions: this.agentCache,
			providers,
			projects: this.registry.publicList().map(
				/** @param {Record<string, unknown>} item Item. */
				item => ({
					...item,
					path: `/p/${String(getAttribute(item, "id"))}/`
				}))
		});
	}
	/**
	 * Events.
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {string} projectId ProjectId.
	 */
	events(request, response, projectId) {
		response.writeHead(200, {
			"Cache-Control": Constants.noCache,
			Connection: "keep-alive",
			"Content-Type": "text/event-stream; charset=utf-8"
		});
		response.write(`event: agents\ndata: ${JSON.stringify({ sessions: this.agentCache })}\n\n`);
		const group = this.clients.get(projectId) ?? new Set();
		group.add(response);
		this.clients.set(projectId, group);
		const heartbeat = setInterval(() => response.write(": heartbeat\n\n"), Constants.eventHeartbeatMs);
		request.on("close", () => {
			clearInterval(heartbeat);
			group.delete(response);
			if (!group.size) this.clients.delete(projectId);
		});
	}
	/**
	 * Terminal events.
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {string} projectId ProjectId.
	 * @param {string} sessionId SessionId.
	 */
	terminalEvents(request, response, projectId, sessionId) {
		const terminal = this.terminals.get(projectId, sessionId);
		response.writeHead(200, {
			"Cache-Control": Constants.noCache,
			Connection: "keep-alive",
			"Content-Type": "text/event-stream; charset=utf-8"
		});
		response.write(`event: terminal\ndata: ${JSON.stringify({ type: "snapshot", ...terminal.snapshot() })}\n\n`);
		const listener = /** @param {unknown} event Event. */ event => response.write(`event: terminal\ndata: ${JSON.stringify(event)}\n\n`);
		terminal.on("terminal", listener);
		const heartbeat = setInterval(() => response.write(": heartbeat\n\n"), Constants.eventHeartbeatMs);
		request.on("close", () => {
			clearInterval(heartbeat);
			terminal.off("terminal", listener);
		});
	}
	/**
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @param {string} name Name.
	 */
	static cookie = (request, name) => {
		const cookies = String(request.headers.cookie ?? "").split(";").map(item => item.trim().split("=")).filter(parts => parts.length >= 2);
		return cookies.find(([key]) => key === name)?.slice(1).join("=") ?? "";
	};
	/** @param {import("node:http").IncomingMessage} request Request. */
	static remoteAddress = request => String(request.headers["x-forwarded-for"] ?? request.socket.remoteAddress ?? "unknown").split(",")[0]?.trim() ?? "unknown";
	/**
	 * @param {import("node:http").IncomingMessage} request Request.
	 * @returns {Promise<Record<string, unknown>>} Parsed body.
	 */
	static body = async request => {
		/** @type {Buffer[]} */
		const chunks = [];
		let size = 0;
		for await (const chunk of request) {
			size += chunk.length;
			if (size > Constants.maxBodyBytes) throw new HttpError(413, "Request body is too large.");
			chunks.push(chunk);
		}
		if (!chunks.length) return {};
		try {
			const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
			return parsed && typeof parsed === "object" ? parsed : {};
		} catch {
			throw new HttpError(400, "Request body must be valid JSON.");
		}
	};
	/**
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {unknown} error Error.
	 */
	static error = (response, error) => {
		if (response.headersSent) {
			response.end();
			return;
		}
		const known = error instanceof HttpError || error instanceof UpstreamError;
		GatewayServer.json(response, known ? error.status : 500, {
			error: known ? getAttribute(error, "message") : "Internal server error.",
			...(error instanceof UpstreamError ? {
				service: error.service
			} : {})
		});
	};
	/**
	 * @param {URL} url Url.
	 * @param {string} name Name.
	 */
	static requiredQuery = (url, name) => {
		const value = url.searchParams.get(name);
		if (!value) throw new HttpError(400, `Missing query parameter: ${name}.`);
		return value;
	};
	/**
	 * @param {import("node:http").ServerResponse} response Response.
	 * @param {number} status Status.
	 * @param {unknown} payload Payload.
	 * @param {string} contentType ContentType.
	 */
	static json = (response, status, payload, contentType = "application/json; charset=utf-8") => {
		response.writeHead(status, {
			"Content-Type": contentType
		});
		response.end(JSON.stringify(payload));
	};
}
module.exports = {
	GatewayServer
};
