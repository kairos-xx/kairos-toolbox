"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
/** Remote-control web app front end. */
class DevinRemoteApp {
	static apiTimeoutMs = 30000;
	static messageRefreshMs = 3500;
	static terminalAnsi = new RegExp(`${String.fromCharCode(27)}(?:[@-Z\\-_]|[[0-?]*[ -/]*[@-~])`, "gu");
	/** @type {(id: string) => SVGSVGElement} */
	static icon = id => {
		const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
		const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
		svg.classList.add("icon");
		svg.setAttribute("aria-hidden", "true");
		use.setAttribute("href", `#${id}`);
		svg.append(use);
		return svg;
	};
	/** @type {<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string) => HTMLElementTagNameMap[K]} */
	static element = (tag, className = "", text = "") => {
		const element = document.createElement(tag);
		if (className) element.className = className;
		if (text) element.textContent = text;
		return element;
	};
	/** @type {(value: unknown) => string} */
	static text = value => String(value ?? "");
	/** @type {(error: unknown) => string} */
	static errorText = error => error instanceof Error ? error.message : String(error);
	/** @type {(session: any) => string} */
	static sessionId = session => DevinRemoteApp.text(session.devin_id ?? session.id ?? session.session_id);
	/** @type {(session: any) => string} */
	static status = session => DevinRemoteApp.text(getAttribute(session, "status") ?? session.state ?? (session.is_archived ? "archived" : "unknown")).toLowerCase();
	/** @type {(status: string) => string} */
	static statusClass = status => /running|working|active|online/u.test(status) ? "running" : /wait|suspend|pause|idle/u.test(status) ? "waiting" : /fail|error|terminate/u.test(status) ? "failed" : "";
	/** @type {(session: any) => string[]} */
	static tags = session => Array.isArray(session.tags) ? session.tags.map(String) : Array.isArray(session.metadata?.tags) ? session.metadata.tags.map(String) : [];
	/** @type {(session: any) => string} */
	static title = session => DevinRemoteApp.text(session.title ?? session.name ?? session.prompt ?? DevinRemoteApp.sessionId(session));
	/** @type {(item: any) => string} */
	static messageText = item => {
		const content = item.message ?? item.content ?? item.text ?? "";
		if (typeof content === "string") return content;
		if (Array.isArray(content)) return content.map(part => typeof part === "string" ? part : part?.text ?? "").join("\n");
		return DevinRemoteApp.text(content?.text ?? content);
	};
	/** @type {(item: any) => boolean} */
	static messageIsAgent = item => item.is_devin === true || item.source === "devin" || item.role === "assistant" || item.author === "devin";
	/** @type {(value: unknown) => string} */
	static relativeTime = value => {
		const stamp = typeof value === "number" ? value : Date.parse(String(value ?? ""));
		if (!Number.isFinite(stamp)) return "";
		const seconds = Math.max(0, Math.round((Date.now() - stamp) / 1000));
		if (seconds < 60) return `${seconds}s ago`;
		if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
		if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
		return `${Math.floor(seconds / 86400)}d ago`;
	};
	constructor() {
		const match = /^\/p\/([a-f0-9]{12})\//u.exec(location.pathname);
		/** @type {string} */
		this.projectId = match?.[1] ?? "";
		/** @type {string} */
		this.base = match?.[0] ?? "/";
		/** @type {string} */
		this.csrf = sessionStorage.getItem("devin-remote-csrf") ?? "";
		/** @type {any} */
		this.bootstrap = null;
		/** @type {any[]} */
		this.sessions = [];
		/** @type {any[]} */
		this.providers = [];
		/** @type {{ repository: boolean, files: any[], branch: string | null, ahead?: number, behind?: number }} */
		this.git = {
			repository: false,
			files: [],
			branch: null
		};
		/** @type {any[]} */
		this.gitLog = [];
		/** @type {any[]} */
		this.branches = [];
		/** @type {any[]} */
		this.stashes = [];
		/** @type {any} */
		this.workspaceStats = null;
		/** @type {any[]} */
		this.tasks = [];
		/** @type {any[]} */
		this.activity = [];
		/** @type {any[]} */
		this.diagnostics = [];
		/** @type {any[]} */
		this.pairedDevices = [];
		/** @type {string} */
		this.currentDeviceId = "";
		/** @type {any} */
		this.systemInfo = null;
		/** @type {string} */
		this.agentFilter = "project";
		/** @type {string} */
		this.agentQuery = "";
		/** @type {string} */
		this.agentPanelMode = "chat";
		/** @type {string} */
		this.gitMode = "changes";
		/** @type {string} */
		this.diffLayout = "unified";
		/** @type {any} */
		this.selectedAgent = null;
		/** @type {any} */
		this.agentDetails = null;
		/** @type {any} */
		this.selectedFile = null;
		/** @type {any} */
		this.selectedDiff = null;
		/** @type {Map<string, any>} */
		this.tree = new Map();
		/** @type {Set<string>} */
		this.expanded = new Set([""]);
		/** @type {EventSource | null} */
		this.eventSource = null;
		/** @type {EventSource | null} */
		this.terminalSource = null;
		/** @type {string} */
		this.terminalId = "";
		/** @type {string} */
		this.terminalBuffer = "";
		/** @type {Map<string, any>} */
		this.terminalSessions = new Map();
		/** @type {Map<string, EventSource>} */
		this.terminalSources = new Map();
		/** @type {string[]} */
		this.terminalHistory = JSON.parse(localStorage.getItem("devin-remote-terminal-history") ?? "[]");
		/** @type {any[]} */
		this.recentFiles = JSON.parse(localStorage.getItem("devin-remote-recent-files") ?? "[]");
		/** @type {any[]} */
		this.bookmarks = JSON.parse(localStorage.getItem("devin-remote-bookmarks") ?? "[]");
		/** @type {any[]} */
		this.fileHistory = [];
		/** @type {any[]} */
		this.searchResults = [];
		/** @type {Map<string, string>} */
		this.previousAgentStates = new Map();
		/** @type {ReturnType<typeof setTimeout> | 0} */
		this.messageTimer = 0;
		/** @type {boolean} */
		this.messageLoading = false;
		/** @type {string} */
		this.activeScreen = "dashboard";
		/** @type {string} */
		this.initialScreen = ["agents", "files", "diff", "terminal", "more"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "dashboard";
		/** @type {Map<string, HTMLElement>} */
		this.slots = new Map(
			[...document.querySelectorAll("[data-slot]")].map(element => [ /** @type {HTMLElement} */ getAttribute( /** @type {HTMLElement} */ (element).dataset, "slot") ?? "", /** @type {HTMLElement} */ (element)]));
		document.body.addEventListener("click", event => this.onClick(event));
		document.body.addEventListener("submit", event => this.onSubmit(event));
		document.body.addEventListener("input", event => this.onInput(event));
		window.addEventListener("keydown", event => this.onKeyDown(event));
		window.addEventListener("beforeunload", () => this.dispose(), {
			once: true
		});
		if ("serviceWorker" in navigator && this.projectId) navigator.serviceWorker.register(`${this.base}service-worker.js`).catch(() => null);
		this.start();
	}
	/**
	 * Returns the named slot for.
	 * @param {string} name Name.
	 * @returns {any} The resulting value.
	 */
	slot(name) {
		return this.slots.get(name);
	}
	/** Starts. */
	async start() {
		if (!this.projectId) {
			this.toast("Invalid project URL.", "error");
			return;
		}
		if (this.csrf) try {
			const session = await this.request("api/session", {
				method: "GET",
				csrf: false
			});
			this.csrf = session.csrf;
			sessionStorage.setItem("devin-remote-csrf", this.csrf);
			await this.loadWorkspace();
			return;
		} catch {
			this.csrf = "";
			sessionStorage.removeItem("devin-remote-csrf");
		}
		try {
			const session = await this.request("api/session", {
				method: "GET",
				csrf: false
			});
			this.csrf = session.csrf;
			sessionStorage.setItem("devin-remote-csrf", this.csrf);
			await this.loadWorkspace();
		} catch {
			this.showPair();
		}
	}
	/**
	 * Shows pair.
	 * @param {string} message Message.
	 */
	showPair(message = "") {
		/** @type {HTMLElement} */
		(document.querySelector('[data-view="pair"]')).hidden = false;
		/** @type {HTMLElement} */
		(document.querySelector('[data-view="workspace"]')).hidden = true;
		this.slot("pair-status").textContent = message;
	}
	/** Loads workspace. */
	async loadWorkspace() {
		const data = await this.request("api/bootstrap", {
			method: "GET"
		});
		this.bootstrap = data;
		this.sessions = data.sessions ?? [];
		this.providers = data.providers ?? [];
		this.git = data.git ?? {
			repository: false,
			files: [],
			branch: null
		};
		this.gitLog = data.gitLog ?? [];
		this.branches = data.branches ?? [];
		this.stashes = data.stashes ?? [];
		this.workspaceStats = data.workspace ?? null;
		/** @type {any[]} */
		(data.terminals ?? []).forEach(terminal => {
			this.terminalSessions.set(terminal.id, terminal);
		});
		this.tasks = data.tasks ?? [];
		this.activity = data.activity ?? [];
		this.diagnostics = data.diagnostics ?? [];
		this.pairedDevices = data.pairedDevices ?? [];
		this.currentDeviceId = data.currentDeviceId ?? "";
		this.systemInfo = data.system ?? null;
		/** @type {HTMLElement} */
		(document.querySelector('[data-view="pair"]')).hidden = true;
		/** @type {HTMLElement} */
		(document.querySelector('[data-view="workspace"]')).hidden = false;
		this.slot("project-name").textContent = data.project.name;
		this.slot("project-meta").textContent = this.git.repository ? `${this.git.branch ?? "HEAD"} · ${this.git.files.length} change${this.git.files.length === 1 ? "" : "s"}` : "Local workspace";
		this.renderAgents();
		this.renderOverview();
		this.renderDashboard();
		this.renderGit();
		this.renderProviders();
		this.renderProjectFacts();
		this.renderProjects();
		this.renderStats();
		this.renderTasks();
		this.renderStashes();
		this.renderTerminalTabs();
		this.renderTerminalQuickbar();
		this.terminalSessions.forEach(session => {
			this.attachTerminalSource(session.id);
		});
		if (!this.terminalId && this.terminalSessions.size) this.selectTerminal(this.terminalSessions.keys().next().value);
		this.renderActivity();
		this.renderSecurity();
		this.renderRecentFiles();
		this.renderBookmarks();
		this.renderDiagnostics();
		await this.loadTree("");
		this.connectEvents();
		if (this.initialScreen !== "dashboard") this.setScreen(this.initialScreen);
	}
	/**
	 * Request.
	 * @param {string} path Path.
	 * @param {{ method?: string, body?: any, csrf?: boolean, timeout?: number }} options Options.
	 * @returns {Promise<any>} The resulting value.
	 * @throws {Error} When the operation cannot complete.
	 */
	async request(path, options = {}) {
		const method = options.method ?? "GET";
		const response = await fetch(`${this.base}${path}`, {
			method,
			credentials: "same-origin",
			headers: {
				Accept: "application/json",
				...(options.body === undefined ? {} : {
					"Content-Type": "application/json"
				}),
				...(options.csrf === false || ["GET", "HEAD"].includes(method) || !this.csrf ? {} : {
					"X-Devin-Remote-CSRF": this.csrf
				})
			},
			...(options.body === undefined ? {} : {
				body: JSON.stringify(options.body)
			}),
			signal: AbortSignal.timeout(options.timeout ?? DevinRemoteApp.apiTimeoutMs)
		});
		const payload = await response.json().catch(() => ({}));
		if (!response.ok) {
			if (getAttribute(response, "status") === 401 && path !== "api/pair") this.showPair(getAttribute(payload, "error") ?? "Session expired.");
			throw new Error(getAttribute(payload, "error") ?? `Request failed (${getAttribute(response, "status")}).`);
		}
		return payload;
	}
	/** Connects events. */
	connectEvents() {
		this.eventSource?.close();
		this.eventSource = new EventSource(`${this.base}api/events`);
		this.eventSource.addEventListener("agents", event => {
			const next = JSON.parse(event.data).sessions ?? [];
			this.notifyAgentTransitions(next);
			this.sessions = next;
			this.renderAgents();
			this.renderOverview();
			this.renderDashboard();
			if (this.selectedAgent) this.loadMessages(false);
		});
		this.eventSource.addEventListener("files", () => {
			this.refreshTree();
			this.refreshStats();
		});
		this.eventSource.addEventListener("git", event => {
			this.git = JSON.parse(event.data);
			this.renderGit();
			this.renderOverview();
			this.renderDashboard();
		});
		this.eventSource.addEventListener("upstream-error", event => {
			const data = JSON.parse(event.data);
			this.toast(`${data.service}: ${data.message}`, "error");
		});
		this.eventSource.addEventListener("diagnostics", event => {
			this.diagnostics = JSON.parse(event.data).items ?? [];
			this.renderDiagnostics();
		});
		this.eventSource.addEventListener("activity", event => {
			const item = JSON.parse(event.data).item;
			if (item) {
				this.activity.unshift(item);
				this.activity = this.activity.slice(0, 80);
				this.renderActivity();
				this.renderDashboard();
			}
		});
	}
	/**
	 * Sets screen.
	 * @param {*} name Name.
	 */
	setScreen(name) {
		this.activeScreen = name;
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-screen]")).forEach(screen => {
			screen.classList.toggle("active", screen.getAttribute("data-screen") === name);
		});
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-nav]")).forEach(button => {
			const mapped = ["chat"].includes(name) ? "agents" : ["editor"].includes(name) ? "files" : ["diff-detail"].includes(name) ? "diff" : name;
			button.classList.toggle("selected", button.getAttribute("data-nav") === mapped);
		});
		if (name === "dashboard") this.renderDashboard();
		if (name === "diff") this.refreshGit();
		if (name === "terminal" && !this.terminalId) this.newTerminal();
		if (name === "more") Promise.all([
			this.refreshProviders(),
			this.refreshActivity(),
			this.refreshSecurity(),
			this.refreshStats(),
			this.refreshDiagnostics()
		]);
	}
	/**
	 * On click.
	 * @param {*} event Event.
	 * @returns {*} The resulting value.
	 */
	onClick(event) {
		const target = event.target instanceof Element ? event.target.closest("button,[data-agent-id],[data-file-path],[data-change-path],[data-project-path],[data-task-command],[data-branch-name],[data-terminal-id],[data-search-path],[data-device-id],[data-git-mode],[data-stash-ref],[data-commit-hash],[data-open-path],[data-diff-layout],[data-terminal-command],[data-code-context],[data-diagnostic-path],[data-bookmark-path],[data-file-revision]") : null;
		if (!target) return;
		if (target.getAttribute("data-nav")) {
			this.setScreen(target.getAttribute("data-nav"));
			return;
		}
		if (target.getAttribute("data-agent-filter")) {
			this.agentFilter = target.getAttribute("data-agent-filter");
			document.querySelectorAll("[data-agent-filter]").forEach(button => {
				button.classList.toggle("selected", button === target);
			});
			this.renderAgents();
			return;
		}
		if (target.getAttribute("data-agent-id")) {
			this.openAgent(target.getAttribute("data-agent-id"));
			return;
		}
		if (target.getAttribute("data-project-path")) {
			location.href = target.getAttribute("data-project-path");
			return;
		}
		if (target.getAttribute("data-task-command")) {
			this.runTask(target.getAttribute("data-task-command"));
			return;
		}
		if (target.getAttribute("data-branch-name")) {
			this.checkoutBranch(target.getAttribute("data-branch-name"));
			return;
		}
		if (target.getAttribute("data-terminal-id")) {
			this.selectTerminal(target.getAttribute("data-terminal-id"));
			return;
		}
		if (target.getAttribute("data-search-path")) {
			this.openSearchResult(target.getAttribute("data-search-path"));
			return;
		}
		if (target.getAttribute("data-device-id")) {
			this.revokeDevice(target.getAttribute("data-device-id"));
			return;
		}
		if (target.getAttribute("data-git-mode")) {
			this.setGitMode(target.getAttribute("data-git-mode"));
			return;
		}
		if (target.getAttribute("data-stash-ref")) {
			this.stashMenu(target.getAttribute("data-stash-ref"));
			return;
		}
		if (target.getAttribute("data-commit-hash")) {
			this.showCommit(target.getAttribute("data-commit-hash"));
			return;
		}
		if (target.getAttribute("data-open-path")) {
			this.openFile(target.getAttribute("data-open-path"));
			return;
		}
		if (target.getAttribute("data-diff-layout")) {
			this.setDiffLayout(target.getAttribute("data-diff-layout"));
			return;
		}
		if (target.getAttribute("data-terminal-command")) {
			this.sendTerminalCommand(target.getAttribute("data-terminal-command"));
			return;
		}
		if (target.getAttribute("data-code-context")) {
			this.setCodeContext(target.getAttribute("data-code-context"));
			return;
		}
		if (target.getAttribute("data-diagnostic-path")) {
			this.openDiagnostic(target.getAttribute("data-diagnostic-path"), Number(target.getAttribute("data-line") ?? 1), Number(target.getAttribute("data-column") ?? 1));
			return;
		}
		if (target.getAttribute("data-bookmark-path")) {
			this.openFile(target.getAttribute("data-bookmark-path"));
			return;
		}
		if (target.getAttribute("data-file-revision")) {
			this.openFileRevision(target.getAttribute("data-file-revision"));
			return;
		}
		if (target.getAttribute("data-file-path") !== null) {
			this.openTreeItem(target.getAttribute("data-file-path"), target.getAttribute("data-kind"));
			return;
		}
		if (target.getAttribute("data-change-path") !== null) {
			this.openDiff(target.getAttribute("data-change-path"), target.getAttribute("data-status"));
			return;
		}
		if (target.getAttribute("data-editor-mode")) {
			this.setEditorMode(target.getAttribute("data-editor-mode"));
			return;
		}
		const action = target.getAttribute("data-action");
		if (!action) return;
		const actions = {
			refresh: () => this.refreshCurrent(),
			"new-agent": () => this.newAgent(),
			"close-chat": () => this.setScreen("agents"),
			"agent-menu": () => this.agentMenu(),
			"agent-edit-tags": () => this.editAgentTags(),
			"agent-generate-insights": () => this.generateAgentInsights(),
			"agent-refresh-details": () => this.loadAgentDetails(),
			"file-create-menu": () => this.fileCreateMenu(),
			"upload-file": () => this.uploadFile(),
			"close-editor": () => this.setScreen("files"),
			"save-file": () => this.saveFile(),
			"download-file": () => this.downloadFile(),
			"rename-file": () => this.renameFile(),
			"duplicate-file": () => this.duplicateFile(),
			"delete-file": () => this.deleteFile(),
			"close-diff": () => this.setScreen("diff"),
			"toggle-stage": () => this.toggleStage(),
			"new-terminal": () => this.newTerminal(),
			"clear-terminal": () => this.clearTerminal(),
			"interrupt-terminal": () => this.interruptTerminal(),
			"close-terminal": () => this.closeTerminal(),
			"command-palette": () => this.commandPalette(),
			"workspace-search": () => this.workspaceSearchDialog(),
			"git-stage-all": () => this.gitStageAll(),
			"git-unstage-all": () => this.gitUnstageAll(),
			"git-commit": () => this.gitCommitDialog(),
			"git-pull": () => this.gitPull(),
			"git-push": () => this.gitPush(),
			"git-branch-new": () => this.gitBranchDialog(),
			"git-discard": () => this.discardSelectedDiff(),
			"refresh-activity": () => this.refreshActivity(),
			"refresh-security": () => this.refreshSecurity(),
			"enable-notifications": () => this.enableNotifications(),
			settings: () => this.setScreen("more"),
			"project-switch": () => this.projectSwitchDialog(),
			"agent-show-chat": () => this.setAgentPanel("chat"),
			"agent-show-details": () => this.setAgentPanel("details"),
			"file-inspect": () => this.inspectFile(),
			"file-blame": () => this.blameFile(),
			"file-history": () => this.loadFileHistory(),
			"toggle-bookmark": () => this.toggleBookmark(),
			"open-vscode": () => this.openInVsCode(),
			"refresh-diagnostics": () => this.refreshDiagnostics(),
			"show-problems": () => {
				this.setScreen("files");
				this.setCodeContext("problems");
			},
			"git-compare": () => this.compareGitRefs(),
			"git-stash": () => this.gitStashDialog()
		};
		const handler = /** @type {Record<string, () => unknown>} */ (actions)[ /** @type {string} */ (action)];
		if (handler) handler();
	}
	/**
	 * On key down.
	 * @param {*} event Event.
	 */
	onKeyDown(event) {
		if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
			event.preventDefault();
			this.commandPalette();
			return;
		}
		if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
			if (this.activeScreen === "editor" && this.selectedFile) {
				event.preventDefault();
				this.saveFile();
			}
			return;
		}
		if (event.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/u.test(document.activeElement?.tagName ?? "")) {
			event.preventDefault();
			if (this.activeScreen === "agents") this.slot("agent-search")?.focus();
			else if (this.activeScreen === "files") this.slot("file-search")?.focus();
			else this.workspaceSearchDialog();
		}
	}
	/**
	 * On submit.
	 * @param {*} event Event.
	 */
	onSubmit(event) {
		const form = event.target.closest("form[data-form]");
		if (!form) return;
		event.preventDefault();
		const action = form.getAttribute("data-form");
		if (action === "pair") this.pair(new FormData(form));
		if (action === "agent-message") this.sendAgentMessage(new FormData(form), form);
		if (action === "terminal-input") this.sendTerminal(new FormData(form), form);
		if (action === "llm") this.sendLlm(new FormData(form), form);
		if (action === "workspace-search") this.workspaceSearch(new FormData(form));
	}
	/**
	 * On input.
	 * @param {*} event Event.
	 */
	onInput(event) {
		if (event.target.matches('[data-slot="file-search"]')) this.renderTree();
		if (event.target.matches('[data-slot="agent-search"]')) {
			this.agentQuery = event.target.value.trim().toLowerCase();
			this.renderAgents();
		}
		if (event.target.matches('[data-slot="editor-input"] textarea')) this.slot("editor-title").classList.add("dirty");
		if (event.target.matches('[data-form="agent-message"] textarea')) event.target.rows = Math.min(5, Math.max(1, event.target.value.split("\n").length));
	}
	/**
	 * Pairs.
	 * @param {*} formData FormData.
	 */
	async pair(formData) {
		try {
			this.slot("pair-status").textContent = "Connecting…";
			const result = await this.request("api/pair", {
				method: "POST",
				csrf: false,
				body: {
					code: String(formData.get("code") ?? "").replace(/\D/gu, "")
				}
			});
			this.csrf = result.csrf;
			sessionStorage.setItem("devin-remote-csrf", this.csrf);
			await this.loadWorkspace();
		} catch (error) {
			this.showPair(DevinRemoteApp.errorText(error));
		}
	}
	/**
	 * Renders agents.
	 * @returns {*} The resulting value.
	 */
	renderAgents() {
		const projectTag = `vscode-project:${this.projectId}`;
		const sessions = this.sessions.filter(session => {
			const status = getAttribute(DevinRemoteApp, "status")(session);
			const title = DevinRemoteApp.title(session).toLowerCase();
			const id = DevinRemoteApp.sessionId(session).toLowerCase();
			const matchesQuery = !this.agentQuery || title.includes(this.agentQuery) || id.includes(this.agentQuery);
			if (!matchesQuery) return false;
			if (this.agentFilter === "running") return /running|working|active/u.test(status);
			if (this.agentFilter === "waiting") return /wait|suspend|pause|idle/u.test(status);
			if (this.agentFilter === "project") return DevinRemoteApp.tags(session).includes(projectTag) || this.matchesProjectRepo(session);
			return true;
		});
		const running = this.sessions.filter(session => /running|working|active/u.test(getAttribute(DevinRemoteApp, "status")(session))).length;
		this.slot("agent-summary").textContent = `${running} active · ${this.sessions.length} total`;
		DevinRemoteApp.badge("agents", running);
		DevinRemoteApp.replace(this.slot("agents"), sessions.length ? sessions.map(session => DevinRemoteApp.agentCard(session)) : [DevinRemoteApp.empty("i-agent", "No matching Devin sessions", "Start a new session from this project.")]);
	}
	/**
	 * Matches project repo.
	 * @param {*} session Session.
	 * @returns {*} The resulting value.
	 */
	matchesProjectRepo(session) {
		const remote = DevinRemoteApp.text(this.bootstrap?.project?.gitRemote);
		if (!remote) return false;
		const repos = session.repos ?? session.repositories ?? [];
		return Array.isArray(repos) && repos.some(repo => DevinRemoteApp.text(repo?.url ?? repo) === remote);
	}
	/** @type {(session: any) => HTMLElement} */
	static agentCard = session => {
		const id = DevinRemoteApp.sessionId(session);
		const card = DevinRemoteApp.element("article", "agent-card");
		card.setAttribute("data-agent-id", String(id));
		card.tabIndex = 0;
		card.setAttribute("role", "button");
		const head = DevinRemoteApp.element("div", "agent-card-head");
		const name = DevinRemoteApp.element("div", "agent-name");
		name.append(DevinRemoteApp.element("strong", "", DevinRemoteApp.title(session)));
		name.append(DevinRemoteApp.element("span", "", id));
		const status = getAttribute(DevinRemoteApp, "status")(session);
		const pill = DevinRemoteApp.element("span", `status-pill ${DevinRemoteApp.statusClass(status)}`, status || "unknown");
		head.append(name, pill);
		const summary = DevinRemoteApp.element("p", "", DevinRemoteApp.text(session.current_message ?? session.last_message ?? session.status_message ?? session.prompt ?? "Open session to inspect the conversation."));
		const meta = DevinRemoteApp.element("div", "agent-meta");
		const acu = session.acu_usage ?? session.acu_used ?? session.consumed_acu;
		[
			DevinRemoteApp.relativeTime(session.updated_at ?? session.created_at),
			acu === undefined ? "" : `${Number(acu).toFixed(2)} ACU`,
			session.pull_request?.url || session.pr_url ? "PR linked" : ""
		].filter(Boolean).forEach(text => {
			meta.append(DevinRemoteApp.element("span", "", text));
		});
		card.append(head, summary, meta);
		return card;
	};
	/**
	 * Opens agent.
	 * @param {*} id Id.
	 */
	async openAgent(id) {
		this.selectedAgent = this.sessions.find(session => DevinRemoteApp.sessionId(session) === id) ?? {
			id
		};
		this.slot("chat-title").textContent = DevinRemoteApp.title(this.selectedAgent);
		this.slot("chat-state").textContent = getAttribute(DevinRemoteApp, "status")(this.selectedAgent);
		this.setScreen("chat");
		this.setAgentPanel("chat");
		await Promise.all([this.loadMessages(true), this.loadAgentDetails()]);
		clearInterval(this.messageTimer);
		this.messageTimer = setInterval(() => this.loadMessages(false), DevinRemoteApp.messageRefreshMs);
	};
	/**
	 * Loads messages.
	 * @param {*} scroll Scroll.
	 */
	async loadMessages(scroll) {
		if (!this.selectedAgent || this.messageLoading) return;
		this.messageLoading = true;
		try {
			const id = DevinRemoteApp.sessionId(this.selectedAgent);
			const data = await this.request(`api/agents/${encodeURIComponent(id)}/messages`, {
				method: "GET"
			});
			const nodes = /** @type {any[]} */ (data.items ?? []).map(item => DevinRemoteApp.messageNode(item));
			DevinRemoteApp.replace(this.slot("messages"), nodes.length ? nodes : [DevinRemoteApp.empty("i-agent", "No messages yet", "The conversation will appear here as Devin works.")]);
			if (scroll) this.slot("messages").scrollTop = this.slot("messages").scrollHeight;
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		} finally {
			this.messageLoading = false;
		}
	}
	/** Loads agent details. */
	async loadAgentDetails() {
		if (!this.selectedAgent) return;
		const id = encodeURIComponent(DevinRemoteApp.sessionId(this.selectedAgent));
		try {
			this.agentDetails = await this.request(`api/agents/${id}/details`, {
				method: "GET"
			});
			this.renderAgentDetails();
		} catch (error) {
			this.agentDetails = null;
			this.slot("agent-details").hidden = true;
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders agent details.
	 * @throws {Error} When the operation cannot complete.
	 */
	renderAgentDetails() {
		const host = this.slot("agent-details");
		const details = this.agentDetails;
		if (!details || !this.selectedAgent) {
			host.hidden = true;
			return;
		}
		const insight = details.insights ?? {};
		const analysis = insight.analysis ?? {};
		const disclosure = DevinRemoteApp.element("details", "agent-context");
		const summary = DevinRemoteApp.element("summary", "agent-context-summary");
		const statusDetail = DevinRemoteApp.text(insight.status_detail || this.selectedAgent.status_detail);
		summary.append(DevinRemoteApp.icon("i-spark"), DevinRemoteApp.element("strong", "", "Session context"), DevinRemoteApp.element("span", "", statusDetail || "Insights, files and tags"));
		disclosure.append(summary);
		const metrics = DevinRemoteApp.element("div", "agent-context-metrics");
		[
			["ACU", insight.acus_consumed ?? this.selectedAgent.acu_usage ?? "—"],
			["Messages", (insight.num_devin_messages ?? 0) + (insight.num_user_messages ?? 0) || "—"],
			["Size", DevinRemoteApp.text(insight.session_size || "—").toUpperCase()],
			["Mode", DevinRemoteApp.text(insight.devin_mode || this.selectedAgent.devin_mode || "—")]
		].forEach(([label, value]) => {
			const metric = DevinRemoteApp.element("div");
			metric.append(DevinRemoteApp.element("strong", "", DevinRemoteApp.text(value)), DevinRemoteApp.element("span", "", label));
			metrics.append(metric);
		});
		disclosure.append(metrics);
		const classification = analysis.classification ?? {};
		const category = DevinRemoteApp.text(classification.category || insight.category || insight.subcategory);
		if (category) {
			const context = DevinRemoteApp.element("div", "agent-context-row");
			context.append(DevinRemoteApp.icon("i-spark"));
			const copy = DevinRemoteApp.element("div");
			copy.append(DevinRemoteApp.element("strong", "", "Classification"));
			copy.append(DevinRemoteApp.element("span", "", [
				category.replaceAll("_", " "), ...(classification.programming_languages ?? []), ...(classification.tools_and_frameworks ?? [])
			].filter(Boolean).join(" · ")));
			context.append(copy);
			disclosure.append(context);
		}
		const tags = Array.isArray(details.tags) ? details.tags : [];
		const tagRow = DevinRemoteApp.element("div", "agent-context-section");
		const tagHead = DevinRemoteApp.element("div", "agent-context-head");
		tagHead.append(DevinRemoteApp.element("strong", "", "Tags"));
		const editTags = DevinRemoteApp.element("button", "link-button", "Edit");
		Object.assign(editTags, {
			type: "button"
		});
		editTags.setAttribute("data-action", String("agent-edit-tags"));
		tagHead.append(editTags);
		tagRow.append(tagHead);
		const tagList = DevinRemoteApp.element("div", "tag-list");
		/** @type {any[]} */
		(tags.length ? tags : ["No tags"]).forEach(tag => {
			const chip = DevinRemoteApp.element("span", `tag-chip${tags.length ? "" : " muted"}`, tag);
			tagList.append(chip);
		});
		tagRow.append(tagList);
		disclosure.append(tagRow);
		const attachments = Array.isArray(details.attachments) ? details.attachments : [];
		if (attachments.length) {
			const attachmentSection = DevinRemoteApp.element("div", "agent-context-section");
			attachmentSection.append(DevinRemoteApp.element("strong", "", `Attachments · ${attachments.length}`));
			const list = DevinRemoteApp.element("div", "attachment-list");
			/** @type {any[]} */
			(attachments).forEach(attachment => {
				const label = DevinRemoteApp.text(attachment.name || attachment.attachment_id || "Attachment");
				try {
					const url = new URL(DevinRemoteApp.text(attachment.url), location.href);
					if (!/^https?:$/u.test(url.protocol)) throw new Error("Unsupported attachment URL.");
					const link = DevinRemoteApp.element("a", "attachment-row");
					Object.assign(link, {
						href: url.href,
						target: "_blank",
						rel: "noopener noreferrer"
					});
					link.append(DevinRemoteApp.icon("i-attach"));
					const copy = DevinRemoteApp.element("span");
					copy.append(DevinRemoteApp.element("strong", "", label));
					copy.append(DevinRemoteApp.element("small", "", DevinRemoteApp.text(attachment.content_type || attachment.source)));
					link.append(copy);
					list.append(link);
				} catch {
					list.append(DevinRemoteApp.element("span", "attachment-row", label));
				}
			});
			attachmentSection.append(list);
			disclosure.append(attachmentSection);
		}
		const actionItems = Array.isArray(analysis.action_items) ? analysis.action_items : [];
		const issues = Array.isArray(analysis.issues) ? analysis.issues : [];
		if (actionItems.length || issues.length) {
			const analysisSection = DevinRemoteApp.element("div", "agent-context-section");
			analysisSection.append(DevinRemoteApp.element("strong", "", "AI analysis"));
			const list = DevinRemoteApp.element("ul", "insight-list");
			[...actionItems, ...issues].slice(0, 8).forEach(item => {
				list.append(DevinRemoteApp.element("li", "", DevinRemoteApp.insightText(item)));
			});
			analysisSection.append(list);
			disclosure.append(analysisSection);
		}
		const prs = Array.isArray(insight.pull_requests) ? insight.pull_requests : [];
		const hierarchy = [
			insight.parent_session_id ? `Parent · ${insight.parent_session_id}` : "", ...( /** @type {any[]} */ (insight.child_session_ids ?? [])).map(id => `Child · ${id}`)
		].filter(Boolean);
		if (prs.length || hierarchy.length) {
			const relations = DevinRemoteApp.element("div", "agent-context-section");
			relations.append(DevinRemoteApp.element("strong", "", "Relations"));
			const rows = DevinRemoteApp.element("div", "relation-list");
			hierarchy.forEach(text => {
				rows.append(DevinRemoteApp.element("span", "relation-row", text));
			});
			/** @type {any[]} */
			(prs).forEach(pr => {
				try {
					const url = new URL(DevinRemoteApp.text(pr.pr_url), location.href);
					if (!/^https?:$/u.test(url.protocol)) throw new Error("Unsupported PR URL.");
					const link = DevinRemoteApp.element("a", "relation-row", `${pr.pr_state || "PR"} · ${url.hostname}`);
					Object.assign(link, {
						href: url.href,
						target: "_blank",
						rel: "noopener noreferrer"
					});
					rows.append(link);
				} catch {
					rows.append(DevinRemoteApp.element("span", "relation-row", "Pull request"));
				}
			});
			relations.append(rows);
			disclosure.append(relations);
		}
		const controls = DevinRemoteApp.element("div", "agent-context-actions");
		const generate = DevinRemoteApp.element("button", "button compact", insight.analysis ? "Refresh insight request" : "Generate insights");
		generate.type = "button";
		generate.setAttribute("data-action", String("agent-generate-insights"));
		const refresh = DevinRemoteApp.element("button", "button compact secondary", "Refresh context");
		refresh.type = "button";
		refresh.setAttribute("data-action", String("agent-refresh-details"));
		controls.append(generate, refresh);
		disclosure.append(controls);
		DevinRemoteApp.replace(host, [disclosure]);
		host.hidden = this.agentPanelMode !== "details";
	}
	/** Edits agent tags. */
	async editAgentTags() {
		if (!this.selectedAgent) return;
		const current = Array.isArray(this.agentDetails?.tags) ? this.agentDetails.tags.join(", ") : "";
		const values = await this.formDialog("Edit Devin tags", [{
			name: "tags",
			label: "Comma-separated tags",
			value: current,
			placeholder: "frontend, urgent, review"
		}], "Save tags");
		if (!values) return;
		const tags = String(values.tags ?? "").split(",").map(tag => tag.trim()).filter(Boolean);
		try {
			const id = encodeURIComponent(DevinRemoteApp.sessionId(this.selectedAgent));
			await this.request(`api/agents/${id}/tags`, {
				method: "PUT",
				body: {
					tags
				}
			});
			this.toast("Session tags updated.");
			await Promise.all([this.loadAgentDetails(), this.refreshAgents()]);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Generates agent insights. */
	async generateAgentInsights() {
		if (!this.selectedAgent) return;
		try {
			const id = encodeURIComponent(DevinRemoteApp.sessionId(this.selectedAgent));
			const result = await this.request(`api/agents/${id}/insights/generate`, {
				method: "POST",
				body: {}
			});
			this.toast(getAttribute(result, "status") === "started" ? "Insight generation started." : "Insights already exist or are being generated.");
			await this.loadAgentDetails();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** @type {(item: any) => HTMLElement} */
	static messageNode = item => {
		const agent = DevinRemoteApp.messageIsAgent(item);
		const article = DevinRemoteApp.element("article", `message ${agent ? "agent" : "user"}`);
		const meta = DevinRemoteApp.element("div", "message-meta");
		meta.append(DevinRemoteApp.element("span", "", agent ? "Devin" : "You"), DevinRemoteApp.element("time", "", DevinRemoteApp.relativeTime(item.timestamp ?? item.created_at)));
		const eventType = DevinRemoteApp.text(item.event_type ?? item.type ?? getAttribute(item, "status"));
		if (eventType && !["message", "text"].includes(eventType.toLowerCase())) meta.append(DevinRemoteApp.element("span", "message-state", eventType));
		const body = DevinRemoteApp.element("div", "message-body");
		body.append(...DevinRemoteApp.markdownNodes(DevinRemoteApp.messageText(item)));
		const attachments = Array.isArray(item.attachments) ? item.attachments : [];
		if (attachments.length) {
			const list = DevinRemoteApp.element("div", "message-attachments");
			/** @type {any[]} */
			(attachments).forEach((attachment, index) => {
				const label = DevinRemoteApp.text(attachment.name ?? attachment.filename ?? `Attachment ${index + 1}`);
				const url = DevinRemoteApp.text(attachment.url ?? attachment.download_url);
				if (url) try {
					const parsed = new URL(url);
					if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("Unsupported attachment URL.");
					const link = DevinRemoteApp.element("a", "attachment", label);
					link.href = parsed.href;
					link.target = "_blank";
					link.rel = "noopener noreferrer";
					list.append(link);
				} catch {
					list.append(DevinRemoteApp.element("span", "attachment", label));
				}
				else list.append(DevinRemoteApp.element("span", "attachment", label));
			});
			body.append(list);
		}
		article.append(meta, body);
		return article;
	}
	/** @type {(text: string) => ChildNode[]} */
	static markdownNodes = text => {
		const fragment = document.createDocumentFragment();
		const lines = text.replace(/\r\n?/gu, "\n").split("\n");
		/** @type {HTMLElement | null} */
		let code = null;
		/** @type {HTMLElement | null} */
		let list = null;
		const flushList = () => {
			if (list) {
				fragment.append(list);
				list = null;
			}
		};
		lines.forEach(line => {
			if (line.startsWith("```")) {
				flushList();
				if (code) {
					fragment.append(code);
					code = null;
				} else {
					code = DevinRemoteApp.element("pre");
					code.append(DevinRemoteApp.element("code"));
				}
				return;
			}
			if (code) {
				code.firstElementChild?.append(document.createTextNode(`${line}\n`));
				return;
			}
			if (/^[-*]\s+/u.test(line)) {
				list ??= DevinRemoteApp.element("ul");
				const item = DevinRemoteApp.element("li");
				item.textContent = line.replace(/^[-*]\s+/u, "");
				list.append(item);
				return;
			}
			flushList();
			if (!line.trim()) return;
			const node = DevinRemoteApp.element(/^#{1,3}\s/u.test(line) ? "strong" : "p");
			node.textContent = line.replace(/^#{1,3}\s/u, "");
			fragment.append(node);
		});
		flushList();
		if (code) fragment.append(code);
		return [...fragment.childNodes];
	};
	/**
	 * Sends agent message.
	 * @param {*} formData FormData.
	 * @param {*} form Form.
	 */
	async sendAgentMessage(formData, form) {
		const message = String(formData.get("message") ?? "").trim();
		if (!message || !this.selectedAgent) return;
		const textarea = form.querySelector("textarea");
		textarea.disabled = true;
		try {
			await this.request(`api/agents/${encodeURIComponent(DevinRemoteApp.sessionId(this.selectedAgent))}/messages`, {
				method: "POST",
				body: {
					message
				}
			});
			textarea.value = "";
			textarea.rows = 1;
			await this.loadMessages(true);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		} finally {
			textarea.disabled = false;
			textarea.focus();
		}
	}
	/** New agent. */
	async newAgent() {
		const values = await this.formDialog("New Devin session", [{
			name: "title",
			label: "Title",
			placeholder: "Implement feature…"
		}, {
			name: "prompt",
			label: "Task",
			type: "textarea",
			required: true,
			placeholder: "Describe what Devin should do…"
		}, {
			name: "maxAcuLimit",
			label: "Max ACU (optional)",
			type: "number",
			placeholder: "10"
		}], "Start session");
		if (!values) return;
		try {
			const session = await this.request("api/agents", {
				method: "POST",
				body: {
					...values,
					maxAcuLimit: Number(values.maxAcuLimit) || null
				}
			});
			this.toast("Devin session started.");
			await this.refreshAgents();
			const id = DevinRemoteApp.sessionId(session);
			if (id) await this.openAgent(id);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Agent menu.
	 * @returns {Promise<void>} The resulting value.
	 */
	async agentMenu() {
		if (!this.selectedAgent) return;
		const action = await this.actionDialog("Agent actions", [{
			id: "details",
			label: "Refresh session context",
			icon: "i-spark"
		}, {
			id: "tags",
			label: "Edit session tags",
			icon: "i-tag"
		}, {
			id: "insights",
			label: "Generate AI insights",
			icon: "i-spark"
		}, {
			id: "archive",
			label: "Archive session",
			icon: "i-archive"
		}, {
			id: "terminate",
			label: "Terminate session",
			icon: "i-stop",
			danger: true
		}]);
		if (!action) return;
		const id = encodeURIComponent(DevinRemoteApp.sessionId(this.selectedAgent));
		try {
			if (action === "details") {
				await this.loadAgentDetails();
				return;
			}
			if (action === "tags") {
				await this.editAgentTags();
				return;
			}
			if (action === "insights") {
				await this.generateAgentInsights();
				return;
			}
			if (action === "archive") await this.request(`api/agents/${id}/archive`, {
				method: "POST",
				body: {}
			});
			if (action === "terminate") await this.request(`api/agents/${id}/terminate`, {
				method: "POST",
				body: {
					archive: true
				}
			});
			this.toast(action === "archive" ? "Session archived." : "Session terminated.");
			this.selectedAgent = null;
			this.agentDetails = null;
			this.setScreen("agents");
			await this.refreshAgents();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Refreshes agents. */
	async refreshAgents() {
		const data = await this.request("api/agents", {
			method: "GET"
		});
		this.sessions = data.sessions ?? [];
		this.renderAgents();
	}
	/**
	 * Loads tree.
	 * @param {*} path Path.
	 */
	async loadTree(path) {
		try {
			const data = await this.request(`api/tree?path=${encodeURIComponent(path)}`, {
				method: "GET"
			});
			this.tree.set(path, data.items ?? []);
			this.expanded.add(path);
			this.renderTree();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Renders tree. */
	renderTree() {
		const query = this.slot("file-search").value.trim().toLowerCase();
		const root = document.createDocumentFragment();
		/** @type {(path: string, container: Element | DocumentFragment) => void} */
		const renderDirectory = (path, container) => {
			const items = /** @type {any[]} */ (this.tree.get(path) ?? []);
			items.forEach(item => {
				if (query && !item.name.toLowerCase().includes(query) && getAttribute(item, "kind") !== "directory") return;
				const node = DevinRemoteApp.element("div", `tree-node${this.expanded.has(item.path) ? " expanded" : ""}`);
				const row = DevinRemoteApp.element("div", "file-row");
				row.setAttribute("data-file-path", String(item.path));
				row.setAttribute("data-kind", String(getAttribute(item, "kind")));
				if (getAttribute(item, "kind") === "directory") {
					const toggle = DevinRemoteApp.element("button", "folder-toggle");
					toggle.type = "button";
					toggle.setAttribute("data-file-path", String(item.path));
					toggle.setAttribute("data-kind", String(getAttribute(item, "kind")));
					toggle.setAttribute("aria-label", `${this.expanded.has(item.path) ? "Collapse" : "Expand"} ${item.name}`);
					toggle.append(DevinRemoteApp.icon("i-chevron"));
					row.append(toggle);
				} else row.append(DevinRemoteApp.icon("i-file"));
				row.append(DevinRemoteApp.element("strong", "", item.name));
				row.append(DevinRemoteApp.element("small", "", getAttribute(item, "kind") === "file" ? DevinRemoteApp.formatBytes(item.size) : ""));
				node.append(row);
				if (getAttribute(item, "kind") === "directory" && this.expanded.has(item.path)) {
					const children = DevinRemoteApp.element("div", "tree-children");
					renderDirectory(item.path, children);
					node.append(children);
				}
				container.append(node);
			});
		};
		renderDirectory("", root);
		const target = this.slot("file-tree");
		target.replaceChildren(root);
		const visibleCount = target.querySelectorAll(".file-row").length;
		if (this.slot("explorer-count")) this.slot("explorer-count").textContent = `${visibleCount} visible`;
		if (!target.childNodes.length) target.append(DevinRemoteApp.empty("i-files", "No files found", query ? "Try another filter." : "This directory is empty."));
		this.renderBreadcrumbs();
	}
	/** Renders breadcrumbs. */
	renderBreadcrumbs() {
		const nav = this.slot("breadcrumbs");
		const fragment = document.createDocumentFragment();
		const root = DevinRemoteApp.element("button", "", this.bootstrap?.project?.name ?? "root");
		root.type = "button";
		root.setAttribute("data-file-path", String(""));
		root.setAttribute("data-kind", String("directory"));
		fragment.append(root);
		nav.replaceChildren(fragment);
	}
	/**
	 * Opens tree item.
	 * @param {*} path Path.
	 * @param {*} kind Kind.
	 */
	async openTreeItem(path, kind) {
		if (kind === "directory") {
			if (this.expanded.has(path)) {
				this.expanded.delete(path);
				this.renderTree();
			} else if (this.tree.has(path)) {
				this.expanded.add(path);
				this.renderTree();
			} else await this.loadTree(path);
			return;
		}
		await this.openFile(path);
	}
	/** Uploads file. */
	async uploadFile() {
		const input = DevinRemoteApp.element("input");
		input.type = "file";
		const file = await new Promise(resolve => {
			input.addEventListener("change", () => resolve(input.files?.[0] ?? null), {
				once: true
			});
			input.click();
		});
		if (!file) return;
		const values = await this.formDialog("Upload file", [{
			name: "path",
			label: "Project path",
			value: file.name,
			placeholder: "assets/example.bin"
		}], "Upload");
		if (!values) return;
		const target = String(values.path ?? "").trim();
		if (!target) return;
		try {
			const base64 = DevinRemoteApp.bytesToBase64(new Uint8Array(await file.arrayBuffer()));
			await this.request("api/upload", {
				method: "POST",
				body: {
					path: target,
					base64,
					overwrite: false
				}
			});
			await Promise.all([this.refreshTree(), this.refreshStats()]);
			this.toast(`Uploaded ${target}.`);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Downloads file. */
	downloadFile() {
		if (!this.selectedFile?.path) return;
		const link = DevinRemoteApp.element("a");
		link.href = `${this.base}api/file/raw?path=${encodeURIComponent(this.selectedFile.path)}`;
		link.download = this.selectedFile.path.split("/").at(-1) ?? "download";
		document.body.append(link);
		link.click();
		link.remove();
	}
	/**
	 * Opens file.
	 * @param {*} path Path.
	 */
	async openFile(path) {
		try {
			this.selectedFile = await this.request(`api/file?path=${encodeURIComponent(path)}`, {
				method: "GET"
			});
			this.slot("editor-title").textContent = this.selectedFile.path.split("/").at(-1);
			this.slot("editor-title").classList.remove("dirty");
			this.slot("editor-meta").textContent = `${this.selectedFile.language} · ${DevinRemoteApp.formatBytes(this.selectedFile.size)}`;
			this.slot("editor-input").querySelector("textarea").value = this.selectedFile.content;
			this.renderCode(this.selectedFile.content);
			this.setEditorMode("preview");
			this.rememberFile(this.selectedFile.path);
			this.renderEditorTabs();
			this.renderEditorStatus();
			this.setScreen("editor");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders editor tabs.
	 * @returns {*} The resulting value.
	 */
	renderEditorTabs() {
		const slot = this.slot("open-file-tabs");
		if (!slot) return;
		const paths = [...new Set([this.selectedFile?.path, ...this.recentFiles].filter(Boolean))].slice(0, 8);
		slot.replaceChildren(...paths.map(path => {
			const button = DevinRemoteApp.element("button", path === this.selectedFile?.path ? "selected" : "");
			button.type = "button";
			button.setAttribute("data-open-path", String(path));
			button.append(DevinRemoteApp.icon("i-file"), DevinRemoteApp.element("span", "", path.split("/").at(-1)));
			button.title = path;
			return button;
		}));
	}
	/** Renders editor status. */
	renderEditorStatus() {
		const slot = this.slot("editor-statusbar");
		if (!slot || !this.selectedFile) return;
		const lines = this.selectedFile.content.replace(/\r\n?/gu, "\n").split("\n").length;
		slot.replaceChildren(DevinRemoteApp.element("span", "", this.selectedFile.language || "text"), DevinRemoteApp.element("span", "", `${lines} lines`), DevinRemoteApp.element("span", "", DevinRemoteApp.formatBytes(this.selectedFile.size)), DevinRemoteApp.element("span", "", "UTF-8"), DevinRemoteApp.element("span", "", "Remote workspace"));
	}
	/**
	 * Renders code.
	 * @param {string} content Content.
	 */
	renderCode(content) {
		const fragment = document.createDocumentFragment();
		content.replace(/\r\n?/gu, "\n").split("\n").forEach((line, index) => {
			const row = DevinRemoteApp.element("div", "code-line");
			row.append(DevinRemoteApp.element("span", "line-number", String(index + 1)));
			const code = DevinRemoteApp.element("span", "line-code");
			code.append(...DevinRemoteApp.highlight(line));
			row.append(code);
			fragment.append(row);
		});
		this.slot("code-preview").replaceChildren(fragment);
	}
	/** @type {(line: string) => Node[]} */
	static highlight = line => {
		const nodes = [];
		const pattern = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b(?:class|function|return|const|let|var|if|else|for|while|async|await|import|from|export|def|try|except|raise|with|as|True|False|None|new|this|static|extends|switch|case|break|continue|yield)\b|\b\d+(?:\.\d+)?\b|\/\/.*$|#.*$)/gu;
		let cursor = 0;
		for (const match of line.matchAll(pattern)) {
			if (match.index > cursor) nodes.push(document.createTextNode(line.slice(cursor, match.index)));
			const token = match[0];
			const cls = /^\/\/|^#/u.test(token) ? "tok-comment" : /^["'`]/u.test(token) ? "tok-string" : /^\d/u.test(token) ? "tok-number" : "tok-keyword";
			nodes.push(DevinRemoteApp.element("span", cls, token));
			cursor = match.index + token.length;
		}
		if (cursor < line.length) nodes.push(document.createTextNode(line.slice(cursor)));
		if (!nodes.length) nodes.push(document.createTextNode(line));
		return nodes;
	};
	/**
	 * Sets editor mode.
	 * @param {*} mode Mode.
	 */
	setEditorMode(mode) {
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-editor-mode]")).forEach(button => {
			button.classList.toggle("selected", button.getAttribute("data-editor-mode") === mode);
		});
		this.slot("code-preview").hidden = mode !== "preview";
		this.slot("editor-input").hidden = mode !== "edit";
		if (mode === "edit") this.slot("editor-input").querySelector("textarea").focus();
	}
	/** Saves file. */
	async saveFile() {
		if (!this.selectedFile) return;
		const content = this.slot("editor-input").querySelector("textarea").value;
		try {
			const result = await this.request("api/file", {
				method: "PUT",
				body: {
					path: this.selectedFile.path,
					content,
					expectedMtimeMs: this.selectedFile.mtimeMs
				}
			});
			Object.assign(this.selectedFile, result, {
				content,
				size: new TextEncoder().encode(content).length
			});
			this.slot("editor-title").classList.remove("dirty");
			this.renderCode(content);
			this.renderEditorStatus();
			this.toast("File saved.");
			await this.refreshGit();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** File create menu. */
	async fileCreateMenu() {
		const type = await this.actionDialog("Create", [{
			id: "file",
			label: "New file",
			icon: "i-file"
		}, {
			id: "directory",
			label: "New folder",
			icon: "i-folder"
		}]);
		if (!type) return;
		const values = await this.formDialog(type === "file" ? "New file" : "New folder", [{
			name: "path",
			label: "Relative path",
			required: true,
			placeholder: type === "file" ? "src/new-file.js" : "src/new-folder"
		}], "Create");
		if (!values) return;
		try {
			if (type === "file") await this.request("api/file", {
				method: "PUT",
				body: {
					path: values.path,
					content: "",
					expectedMtimeMs: null
				}
			});
			else await this.request("api/directory", {
				method: "POST",
				body: {
					path: values.path
				}
			});
			this.toast(`${type === "file" ? "File" : "Folder"} created.`);
			await this.refreshTree();
			if (type === "file") await this.openFile(values.path);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Rename file. */
	async renameFile() {
		if (!this.selectedFile) return;
		const values = await this.formDialog("Rename file", [{
			name: "path",
			label: "New relative path",
			value: this.selectedFile.path,
			required: true
		}], "Rename");
		if (!values) return;
		try {
			await this.request("api/rename", {
				method: "POST",
				body: {
					path: this.selectedFile.path,
					nextPath: values.path
				}
			});
			this.toast("File renamed.");
			this.selectedFile = null;
			this.setScreen("files");
			await this.refreshTree();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Duplicate file. */
	async duplicateFile() {
		if (!this.selectedFile) return;
		const current = this.selectedFile.path;
		const dot = current.lastIndexOf(".");
		const suggested = dot > current.lastIndexOf("/") ? `${current.slice(0, dot)}-copy${current.slice(dot)}` : `${current}-copy`;
		const values = await this.formDialog("Duplicate file",
			[{
				name: "path",
				label: "New relative path",
				value: suggested,
				required: true
			}], "Duplicate");
		if (!values) return;
		try {
			await this.request("api/copy", {
				method: "POST",
				body: {
					path: current,
					nextPath: values.path
				}
			});
			this.toast("File duplicated.");
			await this.refreshTree();
			await this.openFile(values.path);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Deletes file. */
	async deleteFile() {
		if (!this.selectedFile || !await this.confirmDialog("Delete file?", this.selectedFile.path, "Delete")) return;
		try {
			await this.request(`api/file?path=${encodeURIComponent(this.selectedFile.path)}`, {
				method: "DELETE"
			});
			this.toast("File deleted.");
			this.selectedFile = null;
			this.setScreen("files");
			await this.refreshTree();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Refreshes tree. */
	async refreshTree() {
		const paths = [...this.expanded];
		await Promise.all(paths.map(async path => {
			const data = await this.request(`api/tree?path=${encodeURIComponent(path)}`, {
				method: "GET"
			});
			this.tree.set(path, data.items ?? []);
		}).map(promise => promise.catch(() => null)));
		this.renderTree();
	}
	/** Renders git. */
	renderGit() {
		const files = this.git.files ?? [];
		this.slot("branch").textContent = this.git.branch ?? "not a repo";
		const tracking = this.git.ahead || this.git.behind ? ` · ↑${this.git.ahead ?? 0} ↓${this.git.behind ?? 0}` : "";
		this.slot("git-summary").textContent = this.git.repository ? `${files.length} changed file${files.length === 1 ? "" : "s"}${tracking}` : "No Git repository detected";
		DevinRemoteApp.badge("changes", files.length);
		const stagedCount = files.filter(file => getAttribute(file, "status")[0] && ![" ", "?"].includes(getAttribute(file, "status")[0])).length;
		const review = this.slot("git-review-summary");
		if (review) review.replaceChildren(DevinRemoteApp.reviewMetric(String(files.length), "Changed"), DevinRemoteApp.reviewMetric(String(stagedCount), "Staged"), DevinRemoteApp.reviewMetric(`↑${this.git.ahead ?? 0} ↓${this.git.behind ?? 0}`, "Remote"));
		if (files.length) {
			const staged = files.filter(file => getAttribute(file, "status")[0] && ![" ", "?"].includes(getAttribute(file, "status")[0]));
			const working = files.filter(file => getAttribute(file, "status")[1] && getAttribute(file, "status")[1] !== " " || getAttribute(file, "status").startsWith("??"));
			const nodes = [];
			if (staged.length) {
				nodes.push(DevinRemoteApp.element("div", "change-group-title", `Staged · ${staged.length}`));
				nodes.push(...staged.map(file => DevinRemoteApp.changeCard(file)));
			}
			if (working.length) {
				nodes.push(DevinRemoteApp.element("div", "change-group-title", `Working tree · ${working.length}`));
				nodes.push(...working.map(file => DevinRemoteApp.changeCard(file)));
			}
			DevinRemoteApp.replace(this.slot("changes"), nodes);
		} else DevinRemoteApp.replace(this.slot("changes"), [
			DevinRemoteApp.empty("i-git", "Working tree clean", "No local changes to review.")
		]);
		this.slot("project-meta").textContent = this.git.repository ? `${this.git.branch ?? "HEAD"} · ${files.length} change${files.length === 1 ? "" : "s"}` : "Local workspace";
		this.renderGitHistory();
		this.renderBranches();
		this.renderOverview();
	}
	/** @type {(value: string, label: string) => HTMLElement} */
	static reviewMetric = (value, label) => {
		const node = DevinRemoteApp.element("div", "review-metric");
		node.append(DevinRemoteApp.element("strong", "", value), DevinRemoteApp.element("span", "", label));
		return node;
	};
	/** @type {(file: any) => HTMLElement} */
	static changeCard = file => {
		const card = DevinRemoteApp.element("article", "change-card");
		card.setAttribute("data-change-path", String(file.path));
		card.tabIndex = 0;
		card.setAttribute("role", "button");
		card.setAttribute("data-status", String(getAttribute(file, "status")));
		const head = DevinRemoteApp.element("div", "change-card-head");
		const pathNode = DevinRemoteApp.element("div", "change-path");
		pathNode.append(DevinRemoteApp.element("strong", "", file.path.split("/").at(-1)), DevinRemoteApp.element("span", "", file.path));
		head.append(pathNode, DevinRemoteApp.element("span", "status-code", getAttribute(file, "status").trim() || "M"));
		const meta = DevinRemoteApp.element("div", "change-meta");
		meta.append(DevinRemoteApp.element("span", "", getAttribute(file, "status")[0] && getAttribute(file, "status")[0] !== " " && getAttribute(file, "status")[0] !== "?" ? "Staged" : "Working tree"));
		card.append(head, meta);
		return card;
	};
	/** Refreshes git. */
	async refreshGit() {
		try {
			this.git = await this.request("api/git/status", {
				method: "GET"
			});
			this.renderGit();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Opens diff.
	 * @param {*} path Path.
	 * @param {*} status Status.
	 */
	async openDiff(path, status) {
		try {
			const data = await this.request(`api/git/diff?path=${encodeURIComponent(path)}`, {
				method: "GET"
			});
			this.selectedDiff = {
				path,
				status,
				staged: Boolean(data.staged) || (status[0] !== " " && status[0] !== "?"),
				...data
			};
			this.slot("diff-title").textContent = path.split("/").at(-1);
			const additions = /** @type {string} */ (data.diff).split("\n").filter(line => line.startsWith("+") && !line.startsWith("+++")).length;
			const removals = /** @type {string} */ (data.diff).split("\n").filter(line => line.startsWith("-") && !line.startsWith("---")).length;
			this.slot("diff-meta").textContent = `${path} · +${additions} −${removals}`;
			/** @type {HTMLElement} */
			(document.querySelector('[data-action="toggle-stage"]')).textContent = this.selectedDiff.staged ? "Unstage" : "Stage";
			this.renderDiff(data.diff);
			this.setScreen("diff-detail");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders diff.
	 * @param {*} diff Diff.
	 */
	/**
	 * Renders diff.
	 * @param {string} diff Diff.
	 */
	renderDiff(diff) {
		if (this.diffLayout === "split") {
			this.renderSplitDiff(diff);
			return;
		}
		const fragment = document.createDocumentFragment();
		diff.replace(/\r\n?/gu, "\n").split("\n").forEach((line, index) => {
			const cls = line.startsWith("+") && !line.startsWith("+++") ? "added" : line.startsWith("-") && !line.startsWith("---") ? "removed" : line.startsWith("@@") ? "hunk" : /^(diff |index |--- |\+\+\+)/u.test(line) ? "header" : "";
			const row = DevinRemoteApp.element("div", `diff-line ${cls}`.trim());
			row.append(DevinRemoteApp.element("span", "line-number", String(index + 1)), DevinRemoteApp.element("span", "line-code", line));
			fragment.append(row);
		});
		this.slot("diff-view").replaceChildren(fragment);
	}
	/**
	 * Sets diff layout.
	 * @param {*} layout Layout.
	 */
	setDiffLayout(layout) {
		this.diffLayout = layout === "split" ? "split" : "unified";
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-diff-layout]")).forEach(button => {
			button.classList.toggle("selected", button.getAttribute("data-diff-layout") === this.diffLayout);
		});
		if (this.selectedDiff) this.renderDiff(this.selectedDiff.diff ?? "");
	}
	/**
	 * Renders split diff.
	 * @param {*} diff Diff.
	 */
	renderSplitDiff(diff) {
		const fragment = document.createDocumentFragment();
		let leftLine = 0;
		let rightLine = 0;
		for (const line of diff.replace(/\r\n?/gu, "\n").split("\n")) {
			if (/^(diff |index |--- |\+\+\+|@@)/u.test(line)) {
				const header = DevinRemoteApp.element("div", "diff-line header");
				header.append(DevinRemoteApp.element("span", "line-number", ""), DevinRemoteApp.element("span", "line-code", line));
				fragment.append(header);
				const hunk = /@@ -(\d+)(?:,\d+)? \+(\d+)/u.exec(line);
				if (hunk) {
					leftLine = Number(hunk[1]) - 1;
					rightLine = Number(hunk[2]) - 1;
				}
				continue;
			}
			const row = DevinRemoteApp.element("div", "diff-split-row");
			const left = DevinRemoteApp.element("div", `diff-side${line.startsWith("-") ? " removed" : ""}`);
			const right = DevinRemoteApp.element("div", `diff-side${line.startsWith("+") ? " added" : ""}`);
			if (line.startsWith("-") && !line.startsWith("---")) {
				leftLine += 1;
				left.append(DevinRemoteApp.element("span", "line-number", String(leftLine)), DevinRemoteApp.element("span", "line-code", line.slice(1)));
				right.append(DevinRemoteApp.element("span", "line-number", ""), DevinRemoteApp.element("span", "line-code", ""));
			} else if (line.startsWith("+") && !line.startsWith("+++")) {
				rightLine += 1;
				left.append(DevinRemoteApp.element("span", "line-number", ""), DevinRemoteApp.element("span", "line-code", ""));
				right.append(DevinRemoteApp.element("span", "line-number", String(rightLine)), DevinRemoteApp.element("span", "line-code", line.slice(1)));
			} else {
				leftLine += 1;
				rightLine += 1;
				left.append(DevinRemoteApp.element("span", "line-number", String(leftLine)), DevinRemoteApp.element("span", "line-code", line.startsWith(" ") ? line.slice(1) : line));
				right.append(DevinRemoteApp.element("span", "line-number", String(rightLine)), DevinRemoteApp.element("span", "line-code", line.startsWith(" ") ? line.slice(1) : line));
			}
			row.append(left, right);
			fragment.append(row);
		}
		this.slot("diff-view").replaceChildren(fragment);
	}
	/** Toggles stage. */
	async toggleStage() {
		if (!this.selectedDiff) return;
		try {
			const action = this.selectedDiff.staged ? "unstage" : "stage";
			this.git = await this.request(`api/git/${action}`, {
				method: "POST",
				body: {
					path: this.selectedDiff.path
				}
			});
			this.toast(`${action === "stage" ? "Staged" : "Unstaged"} ${this.selectedDiff.path}.`);
			this.renderGit();
			this.setScreen("diff");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Attaches terminal source.
	 * @param {*} id Id.
	 */
	attachTerminalSource(id) {
		if (!id || this.terminalSources.has(id)) return;
		const source = new EventSource(`${this.base}api/terminal/${encodeURIComponent(id)}/events`);
		this.terminalSources.set(id, source);
		source.addEventListener("terminal", event => {
			const data = JSON.parse(event.data);
			const session = this.terminalSessions.get(id);
			if (!session) return;
			if (data.type === "snapshot") Object.assign(session, data, {
				buffer: data.buffer ?? ""
			});
			if (data.type === "output") session.buffer = `${session.buffer ?? ""}${data.text}`.slice(-524288);
			if (data.type === "exit") {
				session.buffer = `${session.buffer ?? ""}\n[process exited ${data.code}]\n`;
				session.closed = true;
			}
			if (this.terminalId === id) {
				this.terminalBuffer = session.buffer;
				this.renderTerminal();
			}
			this.renderTerminalTabs();
		});
	}
	/** New terminal. */
	async newTerminal() {
		try {
			const terminal = await this.request("api/terminal", {
				method: "POST",
				body: {}
			});
			this.terminalSessions.set(terminal.id, {
				...terminal,
				buffer: terminal.buffer ?? ""
			});
			this.attachTerminalSource(terminal.id);
			this.selectTerminal(terminal.id);
			this.renderTerminalTabs();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Remembers terminal command.
	 * @param {*} command Command.
	 */
	rememberTerminalCommand(command) {
		const clean = String(command).trim();
		if (!clean) return;
		this.terminalHistory = [
			clean, ...this.terminalHistory.filter(item => item !== clean)
		].slice(0, 8);
		localStorage.setItem("devin-remote-terminal-history", JSON.stringify(this.terminalHistory));
		this.renderTerminalQuickbar();
	}
	/**
	 * Renders terminal quickbar.
	 * @returns {*} The resulting value.
	 */
	renderTerminalQuickbar() {
		const target = this.slot("terminal-quickbar");
		if (!target) return;
		const defaults = ["git status --short --branch", "git diff --stat", "pwd", "ls -la"];
		const commands = [...new Set([...this.terminalHistory, ...defaults])].slice(0, 8);
		target.replaceChildren(...commands.map(command => {
			const button = DevinRemoteApp.element("button", "", command);
			button.type = "button";
			button.setAttribute("data-terminal-command", String(command));
			button.title = command;
			return button;
		}));
	}
	/**
	 * Renders terminal.
	 * @returns {*} The resulting value.
	 */
	renderTerminal() {
		const output = this.slot("terminal-output");
		output.textContent = this.terminalBuffer.replace(DevinRemoteApp.terminalAnsi, "");
		output.scrollTop = output.scrollHeight;
		const summary = this.slot("runtime-summary");
		if (summary) {
			const open = [...this.terminalSessions.values()].filter(session => !session.closed).length;
			const values = [
				[open, "Shells"],
				[this.tasks.length, "Tasks"],
				[this.systemInfo?.platform ?? "—", "Host"]
			];
			summary.replaceChildren(...values.map(([value, label]) => {
				const card = DevinRemoteApp.element("div");
				card.append(DevinRemoteApp.element("strong", "", DevinRemoteApp.text(value)), DevinRemoteApp.element("span", "", label));
				return card;
			}));
		}
	}
	/**
	 * Sends terminal.
	 * @param {*} formData FormData.
	 * @param {*} form Form.
	 */
	async sendTerminal(formData, form) {
		const input = String(formData.get("input") ?? "");
		if (!input || !this.terminalId) return;
		try {
			await this.request(`api/terminal/${encodeURIComponent(this.terminalId)}/input`, {
				method: "POST",
				body: {
					input: `${input}\n`
				}
			});
			this.rememberTerminalCommand(input);
			form.reset();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Sends terminal command.
	 * @param {*} command Command.
	 */
	async sendTerminalCommand(command) {
		if (!this.terminalId) await this.newTerminal();
		if (!this.terminalId) return;
		try {
			await this.request(`api/terminal/${encodeURIComponent(this.terminalId)}/input`, {
				method: "POST",
				body: {
					input: `${command}\n`
				}
			});
			this.rememberTerminalCommand(command);
			this.setScreen("terminal");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Renders overview. */
	renderOverview() {
		const running = this.sessions.filter(session => /running|working|active/u.test(getAttribute(DevinRemoteApp, "status")(session))).length;
		const acu = this.sessions.reduce((total, session) => total + Number(session.acu_usage ?? session.acu_used ?? session.consumed_acu ?? 0), 0);
		const metrics = {
			"metric-active": String(running),
			"metric-acu": acu.toFixed(2),
			"metric-changes": String(this.git.files?.length ?? 0),
			"metric-problems": String(this.diagnostics.length)
		};
		Object.entries(metrics).forEach(([slot, value]) => {
			document.querySelectorAll(`[data-slot="${slot}"]`).forEach(target => {
				target.textContent = value;
			});
		});
		const branch = document.querySelector('[data-slot="dashboard-branch"]');
		if (branch) branch.textContent = this.git.branch ?? "working tree";
		const statusBranch = this.slot("status-branch");
		const statusAgents = this.slot("status-agents");
		const statusProblems = this.slot("status-problems");
		if (statusBranch) statusBranch.textContent = `${this.git.branch ?? "HEAD"} · ${this.git.files?.length ?? 0} changes`;
		if (statusAgents) statusAgents.textContent = `${running} active`;
		if (statusProblems) statusProblems.textContent = `${this.diagnostics.length} problem${this.diagnostics.length === 1 ? "" : "s"}`;
		this.renderDashboard();
	}
	/**
	 * Renders dashboard.
	 * @returns {*} The resulting value.
	 */
	renderDashboard() {
		const agentsTarget = this.slot("dashboard-agents");
		if (agentsTarget) {
			const active = this.sessions.filter(session => /running|working|active|wait/u.test(getAttribute(DevinRemoteApp, "status")(session))).slice(0, 4).map(session => DevinRemoteApp.agentCard(session));
			DevinRemoteApp.replace(agentsTarget, active.length ? active : [DevinRemoteApp.empty("i-agent", "No active agents", "Start a Devin session from this project.")]);
		}
		const changesTarget = this.slot("dashboard-changes");
		if (changesTarget) {
			const changes = (this.git.files ?? []).slice(0, 5).map(file => {
				const button = DevinRemoteApp.element("button", "change-row compact-row");
				button.type = "button";
				button.setAttribute("data-change-path", String(file.path));
				button.setAttribute("data-status", String(getAttribute(file, "status")));
				const copy = DevinRemoteApp.element("div", "change-copy");
				copy.append(DevinRemoteApp.element("strong", "", file.path), DevinRemoteApp.element("span", "", getAttribute(file, "status").trim() || "modified"));
				button.append(DevinRemoteApp.icon("i-diff"), copy);
				return button;
			});
			DevinRemoteApp.replace(changesTarget, changes.length ? changes : [DevinRemoteApp.empty("i-git", "Working tree clean", "No local changes to review.")]);
		}
		const tasksTarget = this.slot("dashboard-tasks");
		if (tasksTarget) {
			const tasks = this.tasks.slice(0, 5).map(task => {
				const button = DevinRemoteApp.element("button", "task-row");
				button.type = "button";
				button.setAttribute("data-task-command", String(task.command));
				const copy = DevinRemoteApp.element("div", "task-copy");
				copy.append(DevinRemoteApp.element("strong", "", task.label), DevinRemoteApp.element("span", "", task.source));
				button.append(DevinRemoteApp.icon("i-play"), copy);
				return button;
			});
			DevinRemoteApp.replace(tasksTarget, tasks.length ? tasks : [DevinRemoteApp.empty("i-terminal", "No tasks detected", "Use the Runtime terminal for custom commands.")]);
		}
		const activityTarget = this.slot("dashboard-activity");
		if (activityTarget) {
			const items = this.activity.slice(0, 5).map(item => {
				const row = DevinRemoteApp.element("article", "activity-row");
				const copy = DevinRemoteApp.element("div", "activity-copy");
				copy.append(DevinRemoteApp.element("strong", "", item.title ?? item.type ?? "Activity"), DevinRemoteApp.element("span", "", [item.detail, DevinRemoteApp.relativeTime(item.createdAt)].filter(Boolean).join(" · ")));
				row.append(DevinRemoteApp.icon(item.type === "agent" ? "i-agent" : item.type === "git" ? "i-git" : "i-clock"), copy);
				return row;
			});
			DevinRemoteApp.replace(activityTarget, items.length ? items : [DevinRemoteApp.empty("i-clock", "No remote activity", "Actions from this project will appear here.")]);
		}
		const gitSubtitle = this.slot("dashboard-git-subtitle");
		if (gitSubtitle) gitSubtitle.textContent = this.git.repository ? `${this.git.branch ?? "HEAD"}${this.git.ahead ? ` · ${this.git.ahead} ahead` : ""}${this.git.behind ? ` · ${this.git.behind} behind` : ""}` : "Not a Git repository";
	}
	/**
	 * Sets agent panel.
	 * @param {*} mode Mode.
	 */
	setAgentPanel(mode) {
		this.agentPanelMode = mode;
		const details = this.slot("agent-details");
		const messages = this.slot("messages");
		const composer = /** @type {HTMLElement | null} */ (document.querySelector('[data-form="agent-message"]'));
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll(".agent-toolbar [data-action]")).forEach(button => {
			button.classList.toggle("selected", button.getAttribute("data-action") === `agent-show-${mode}`);
		});
		if (mode === "details") {
			details.hidden = false;
			messages.hidden = true;
			if (composer) composer.hidden = true;
			details.querySelector("details")?.setAttribute("open", "");
			return;
		}
		details.hidden = true;
		messages.hidden = false;
		if (composer) composer.hidden = false;
	}
	/** Inspects file. */
	async inspectFile() {
		if (!this.selectedFile) return;
		const target = this.slot("file-inspector");
		try {
			const data = await this.request(`api/file/inspect?path=${encodeURIComponent(this.selectedFile.path)}`, {
				method: "GET"
			});
			const metrics = DevinRemoteApp.element("div", "inspector-metrics");
			[
				[data.lines, "Lines"],
				[data.words, "Words"],
				[DevinRemoteApp.formatBytes(data.size), "Size"],
				[data.symbols?.length ?? 0, "Symbols"],
				[data.todo ?? 0, "TODOs"],
				[data.language, "Language"]
			].forEach(([value, label]) => {
				const metric = DevinRemoteApp.element("div");
				metric.append(DevinRemoteApp.element("strong", "", DevinRemoteApp.text(value)), DevinRemoteApp.element("span", "", label));
				metrics.append(metric);
			});
			const symbols = DevinRemoteApp.element("div", "symbol-list");
			/** @type {any[]} */
			(data.symbols ?? []).slice(0, 80).forEach(symbol => {
				const row = DevinRemoteApp.element("div", "symbol-row");
				row.append(DevinRemoteApp.element("span", "", `${getAttribute(symbol, "kind")} ${symbol.name}`), DevinRemoteApp.element("span", "", `L${getAttribute(symbol, "line")}`));
				symbols.append(row);
			});
			target.replaceChildren(metrics, symbols);
			target.hidden = false;
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Shows blame for file. */
	async blameFile() {
		if (!this.selectedFile || !this.git.repository) return;
		const target = this.slot("file-inspector");
		try {
			const data = await this.request(`api/git/blame?path=${encodeURIComponent(this.selectedFile.path)}`, {
				method: "GET"
			});
			const host = DevinRemoteApp.element("div", "blame-list");
			/** @type {any[]} */
			(data.items ?? []).slice(0, 500).forEach(item => {
				const row = DevinRemoteApp.element("div", "symbol-row");
				row.append(DevinRemoteApp.element("span", "", `${String(item.hash).slice(0, 8)} · ${item.author}`), DevinRemoteApp.element("span", "", `L${getAttribute(item, "line")}`));
				row.title = item.text;
				host.append(row);
			});
			target.replaceChildren(host);
			target.hidden = false;
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Returns the project for switch dialog. */
	async projectSwitchDialog() {
		const projects = this.bootstrap?.projects ?? [];
		const selected = await this.actionDialog("Switch project",
			/** @type {any[]} */
			(projects).map(project => ({
				id: project.path,
				label: project.name,
				icon: "i-files"
			})));
		if (selected) location.href = selected;
	}
	/**
	 * Renders stashes.
	 * @returns {*} The resulting value.
	 */
	renderStashes() {
		const target = this.slot("git-stashes");
		if (!target) return;
		const nodes = this.stashes.map(stash => {
			const button = DevinRemoteApp.element("button", "stash-row");
			button.type = "button";
			button.setAttribute("data-stash-ref", String(stash.ref));
			const copy = DevinRemoteApp.element("div", "branch-copy");
			copy.append(DevinRemoteApp.element("strong", "", stash.subject || stash.ref), DevinRemoteApp.element("span", "", `${stash.ref} · ${DevinRemoteApp.relativeTime(stash.date)}`));
			button.append(DevinRemoteApp.icon("i-archive"), copy);
			return button;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-archive", "No stashes", "Stash the current working tree to save it for later.")]);
	}
	/** Git stash dialog. */
	async gitStashDialog() {
		if (!(this.git.files ?? []).length) {
			this.toast("Working tree is already clean.");
			return;
		}
		const values = await this.formDialog("Stash working tree",
			[{
				name: "message",
				label: "Stash message",
				value: "Remote workspace stash",
				required: true
			}], "Stash");
		if (!values) return;
		try {
			const result = await this.request("api/git/stash", {
				method: "POST",
				body: {
					message: values.message
				}
			});
			this.git = getAttribute(result, "status");
			this.stashes = result.stashes ?? [];
			this.renderGit();
			this.renderStashes();
			this.toast(result.output || "Changes stashed.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Stashes menu.
	 * @param {*} ref Ref.
	 */
	async stashMenu(ref) {
		const action = await this.actionDialog(ref, [{
			id: "apply",
			label: "Apply stash",
			icon: "i-archive"
		}, {
			id: "pop",
			label: "Apply and drop",
			icon: "i-git"
		}]);
		if (!action) return;
		try {
			const result = await this.request("api/git/stash/apply", {
				method: "POST",
				body: {
					ref,
					drop: action === "pop"
				}
			});
			this.git = getAttribute(result, "status");
			this.stashes = result.stashes ?? [];
			this.renderGit();
			this.renderStashes();
			await this.refreshTree();
			this.toast(result.output || `${action === "pop" ? "Popped" : "Applied"} ${ref}.`);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Shows commit.
	 * @param {*} hash Hash.
	 */
	async showCommit(hash) {
		try {
			const commit = await this.request(`api/git/commit?hash=${encodeURIComponent(hash)}`, {
				method: "GET"
			});
			const body = this.slot("modal-body");
			const actions = this.slot("modal-actions");
			const modal = this.slot("modal");
			this.slot("modal-title").textContent = `${commit.shortHash} · ${commit.author}`;
			body.replaceChildren(DevinRemoteApp.element("p", "commit-message", commit.message || "Untitled commit"), DevinRemoteApp.element("pre", "commit-summary", commit.summary || "No file summary."), DevinRemoteApp.element("small", "muted", commit.date));
			const close = DevinRemoteApp.element("button", "button secondary", "Close");
			close.type = "button";
			actions.replaceChildren(close);
			close.addEventListener("click", () => modal.close(), {
				once: true
			});
			modal.showModal();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Notifies agent transitions.
	 * @param {any[]} sessions Sessions.
	 */
	notifyAgentTransitions(sessions) {
		sessions.forEach(session => {
			const id = DevinRemoteApp.sessionId(session);
			const next = getAttribute(DevinRemoteApp, "status")(session);
			const previous = this.previousAgentStates.get(id);
			this.previousAgentStates.set(id, next);
			if (!previous || previous === next || !("Notification" in window) || Notification.permission !== "granted") return;
			const wasRunning = /running|working|active/u.test(previous);
			const nowRunning = /running|working|active/u.test(next);
			if (wasRunning && !nowRunning) Reflect.construct(Notification, [DevinRemoteApp.title(session), {
				body: `Devin is now ${next || "idle"}.`
			}]);
		});
	}
	/** Enables notifications. */
	async enableNotifications() {
		if (!("Notification" in window)) {
			this.toast("Notifications are not supported by this browser.", "error");
			return;
		}
		const permission = await Notification.requestPermission();
		this.toast(permission === "granted" ? "Agent notifications enabled." : "Notifications were not enabled.");
	}
	/**
	 * Remembers file.
	 * @param {*} path Path.
	 */
	rememberFile(path) {
		this.recentFiles = [path, ...this.recentFiles.filter(item => item !== path)].slice(0, 10);
		localStorage.setItem("devin-remote-recent-files", JSON.stringify(this.recentFiles));
		this.renderRecentFiles();
	}
	/**
	 * Renders recent files.
	 * @returns {*} The resulting value.
	 */
	renderRecentFiles() {
		const target = this.slot("recent-files");
		if (!target) return;
		const nodes = this.recentFiles.map(path => {
			const button = DevinRemoteApp.element("button", "mini-chip", path.split("/").at(-1));
			button.type = "button";
			button.setAttribute("data-file-path", String(path));
			button.setAttribute("data-kind", String("file"));
			button.title = path;
			return button;
		});
		DevinRemoteApp.replace(target, nodes);
		target.hidden = !nodes.length;
	}
	/**
	 * Sets code context.
	 * @param {*} mode Mode.
	 */
	setCodeContext(mode) {
		const selected = ["recent", "bookmarks", "problems"].includes(mode) ? mode : "recent";
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-code-context]")).forEach(button => {
			button.classList.toggle("selected", button.getAttribute("data-code-context") === selected);
		});
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-context-panel]")).forEach(panel => {
			panel.hidden = panel.getAttribute("data-context-panel") !== selected;
		});
		if (selected === "problems") this.refreshDiagnostics();
	}
	/**
	 * Renders bookmarks.
	 * @returns {*} The resulting value.
	 */
	renderBookmarks() {
		const target = this.slot("bookmarks");
		const count = this.slot("bookmark-count");
		if (count) count.textContent = String(this.bookmarks.length);
		if (!target) return;
		const nodes = this.bookmarks.map(path => {
			const button = DevinRemoteApp.element("button", "context-file-row");
			button.type = "button";
			button.setAttribute("data-bookmark-path", String(path));
			const copy = DevinRemoteApp.element("div", "context-file-copy");
			copy.append(DevinRemoteApp.element("strong", "", path.split("/").at(-1)), DevinRemoteApp.element("span", "", path));
			button.append(DevinRemoteApp.icon("i-bookmark"), copy);
			return button;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-bookmark", "No bookmarks", "Bookmark important files from the editor.")]);
	}
	/** Toggles bookmark. */
	toggleBookmark() {
		if (!this.selectedFile?.path) return;
		const path = this.selectedFile.path;
		const exists = this.bookmarks.includes(path);
		this.bookmarks = exists ? this.bookmarks.filter(item => item !== path) : [path, ...this.bookmarks].slice(0, 40);
		localStorage.setItem("devin-remote-bookmarks", JSON.stringify(this.bookmarks));
		this.renderBookmarks();
		this.toast(exists ? "Bookmark removed." : "File bookmarked.");
	}
	/**
	 * Renders diagnostics.
	 * @returns {*} The resulting value.
	 */
	renderDiagnostics() {
		const target = this.slot("problems");
		const count = this.slot("problem-count");
		const summary = this.slot("problem-summary");
		const cards = this.slot("problem-summary-cards");
		/** @type {Record<string, number>} */
		const totals = {
			error: 0,
			warning: 0,
			information: 0,
			hint: 0
		};
		this.diagnostics.forEach(item => {
			const key = String(item.severity);
			if (totals[key] !== undefined) totals[key] += 1;
		});
		if (count) count.textContent = String(this.diagnostics.length);
		if (summary) summary.textContent = this.diagnostics.length ? `${getAttribute(totals, "error") ?? 0} errors · ${getAttribute(totals, "warning") ?? 0} warnings · ${this.diagnostics.length} total` : "No diagnostics";
		if (cards) cards.replaceChildren(DevinRemoteApp.reviewMetric(String(getAttribute(totals, "error") ?? 0), "Errors"), DevinRemoteApp.reviewMetric(String(getAttribute(totals, "warning") ?? 0), "Warnings"), DevinRemoteApp.reviewMetric(String((getAttribute(totals, "information") ?? 0) + (getAttribute(totals, "hint") ?? 0)), "Info"));
		const statusProblems = this.slot("status-problems");
		if (statusProblems) statusProblems.textContent = `${this.diagnostics.length} problem${this.diagnostics.length === 1 ? "" : "s"}`;
		const metricProblems = this.slot("metric-problems");
		if (metricProblems) metricProblems.textContent = String(this.diagnostics.length);
		if (!target) return;
		const nodes = this.diagnostics.slice(0, 300).map(item => {
			const button = DevinRemoteApp.element("button", `problem-row ${item.severity}`);
			button.type = "button";
			button.setAttribute("data-diagnostic-path", String(item.path));
			button.setAttribute("data-line", String(String(getAttribute(item, "line") ?? 1)));
			button.setAttribute("data-column", String(String(getAttribute(item, "column") ?? 1)));
			const copy = DevinRemoteApp.element("div", "problem-copy");
			copy.append(DevinRemoteApp.element("strong", "", item.message), DevinRemoteApp.element("span", "", `${item.path}:${getAttribute(item, "line")}:${getAttribute(item, "column")}${item.source ? ` · ${item.source}` : ""}${item.code ? ` · ${item.code}` : ""}`));
			button.append(DevinRemoteApp.icon("i-warning"), copy);
			return button;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-warning", "No problems", "VS Code currently reports no workspace diagnostics.")]);
	}
	/** Refreshes diagnostics. */
	async refreshDiagnostics() {
		try {
			const data = await this.request("api/diagnostics", {
				method: "GET"
			});
			this.diagnostics = data.items ?? [];
			this.renderDiagnostics();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Opens diagnostic.
	 * @param {*} path Path.
	 * @param {number} line Line.
	 * @param {number} column Column.
	 */
	async openDiagnostic(path, line = 1, column = 1) {
		await this.openFile(path);
		const row = this.slot("code-preview")?.children?.[Math.max(0, line - 1)];
		row?.scrollIntoView({
			block: "center",
			behavior: "smooth"
		});
		row?.classList.add("diagnostic-focus");
		window.setTimeout(() => row?.classList.remove("diagnostic-focus"), 1800);
		this.slot("editor-meta").textContent = `${this.slot("editor-meta").textContent} · L${line}:${column}`;
	}
	/** Opens in vs code. */
	async openInVsCode() {
		if (!this.selectedFile?.path) return;
		try {
			await this.request("api/vscode/open", {
				method: "POST",
				body: {
					path: this.selectedFile.path,
					line: 1,
					column: 1
				}
			});
			this.toast("Opened in VS Code.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Loads file history. */
	async loadFileHistory() {
		if (!this.selectedFile?.path || !this.git.repository) return;
		const target = this.slot("file-inspector");
		try {
			const data = await this.request(`api/git/file-history?path=${encodeURIComponent(this.selectedFile.path)}&limit=60`, {
				method: "GET"
			});
			this.fileHistory = data.items ?? [];
			const header = DevinRemoteApp.element("div", "inspector-heading");
			header.append(DevinRemoteApp.element("strong", "", "File history"), DevinRemoteApp.element("span", "", `${this.fileHistory.length} revision${this.fileHistory.length === 1 ? "" : "s"}`));
			const list = DevinRemoteApp.element("div", "file-history-list");
			this.fileHistory.forEach(commit => {
				const button = DevinRemoteApp.element("button", "history-row file-history-row");
				button.type = "button";
				button.setAttribute("data-file-revision", String(commit.hash));
				const copy = DevinRemoteApp.element("div", "history-copy");
				copy.append(DevinRemoteApp.element("strong", "", commit.subject || "Untitled commit"), DevinRemoteApp.element("span", "", `${commit.author} · ${DevinRemoteApp.relativeTime(commit.date)}`));
				button.append(DevinRemoteApp.element("code", "hash-chip", commit.shortHash), copy);
				list.append(button);
			});
			target.replaceChildren(header, this.fileHistory.length ? list : DevinRemoteApp.empty("i-clock", "No file history", "This path has no committed history yet."));
			target.hidden = false;
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Opens file revision.
	 * @param {*} hash Hash.
	 */
	async openFileRevision(hash) {
		if (!this.selectedFile?.path) return;
		try {
			const data = await this.request(`api/git/file-revision?path=${encodeURIComponent(this.selectedFile.path)}&hash=${encodeURIComponent(hash)}`, {
				method: "GET"
			});
			this.renderCode(data.content);
			this.setEditorMode("preview");
			this.slot("editor-meta").textContent = `${this.selectedFile.path} · revision ${String(hash).slice(0, 8)} · read only`;
			this.toast(`Showing ${String(hash).slice(0, 8)}. Reopen the file tab to return to the working tree.`);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Workspace search dialog. */
	async workspaceSearchDialog() {
		const values = await this.formDialog("Search project",
			[{
				name: "query",
				label: "Text or filename",
				required: true,
				placeholder: "Search across the workspace…"
			}], "Search");
		if (!values) return;
		await this.workspaceSearch(null, values.query);
	}
	/**
	 * Workspace search.
	 * @param {*} formData FormData.
	 * @param {string} directQuery DirectQuery.
	 */
	async workspaceSearch(formData, directQuery = "") {
		const query = directQuery || String(formData?.get("query") ?? "").trim();
		if (!query) return;
		try {
			const data = await this.request(`api/search?q=${encodeURIComponent(query)}&limit=80`, {
				method: "GET"
			});
			this.searchResults = data.items ?? [];
			this.renderWorkspaceSearch(query);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders workspace search.
	 * @param {string} query Query.
	 * @returns {*} The resulting value.
	 */
	renderWorkspaceSearch(query = "") {
		const target = this.slot("workspace-search-results");
		if (!target) return;
		const nodes = this.searchResults.map(result => {
			const button = DevinRemoteApp.element("button", "search-result");
			button.type = "button";
			button.setAttribute("data-search-path", String(result.path));
			button.append(DevinRemoteApp.element("strong", "", result.path), DevinRemoteApp.element("span", "", getAttribute(result, "line") ? `Line ${getAttribute(result, "line")}` : getAttribute(result, "kind")), DevinRemoteApp.element("code", "", result.preview));
			return button;
		});
		target.hidden = false;
		target.replaceChildren(DevinRemoteApp.element("div", "result-heading", `${nodes.length} result${nodes.length === 1 ? "" : "s"} for “${query}”`), ...(nodes.length ? nodes : [DevinRemoteApp.empty("i-search", "No matches", "Try another project search.")]));
	}
	/**
	 * Opens search result.
	 * @param {*} path Path.
	 */
	async openSearchResult(path) {
		await this.openFile(path);
	}
	/**
	 * Sets git mode.
	 * @param {*} mode Mode.
	 */
	setGitMode(mode) {
		this.gitMode = mode;
		/** @type {NodeListOf<HTMLElement>} */
		(document.querySelectorAll("[data-git-mode]")).forEach(button => {
			button.classList.toggle("selected", button.getAttribute("data-git-mode") === mode);
		});
		["changes", "history", "branches", "stashes", "compare"].forEach(name => {
			const target = this.slot(`git-${name}`);
			if (target) target.hidden = name !== mode;
		});
		if (mode !== "changes") this.refreshGitMeta();
	}
	/** Compares git refs. */
	async compareGitRefs() {
		const base = this.slot("compare-base")?.value.trim();
		const head = this.slot("compare-head")?.value.trim() || "HEAD";
		if (!base) {
			this.toast("Choose a base branch or commit.", "error");
			return;
		}
		try {
			const data = await this.request(`api/git/compare?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`, {
				method: "GET",
				timeout: 60000
			});
			this.slot("compare-stat").textContent = data.stat || `${data.base}…${data.head}`;
			const target = this.slot("compare-diff");
			const fragment = document.createDocumentFragment();
			String(data.diff ?? "").replace(/\r\n?/gu, "\n").split("\n").forEach((line, index) => {
				const cls = line.startsWith("+") && !line.startsWith("+++") ? "added" : line.startsWith("-") && !line.startsWith("---") ? "removed" : line.startsWith("@@") ? "hunk" : /^(diff |index |--- |\+\+\+)/u.test(line) ? "header" : "";
				const row = DevinRemoteApp.element("div", `diff-line ${cls}`.trim());
				row.append(DevinRemoteApp.element("span", "line-number", String(index + 1)), DevinRemoteApp.element("span", "line-code", line));
				fragment.append(row);
			});
			target.replaceChildren(String(data.diff ?? "").trim() ? fragment : DevinRemoteApp.empty("i-diff", "No differences", `${data.base} and ${data.head} resolve to the same content.`));
			if (data.truncated) this.toast("Large comparison truncated for mobile review.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders git history.
	 * @returns {*} The resulting value.
	 */
	renderGitHistory() {
		const target = this.slot("git-history");
		if (!target) return;
		const nodes = this.gitLog.map(commit => {
			const article = DevinRemoteApp.element("article", "history-row");
			article.setAttribute("data-commit-hash", String(commit.hash));
			article.tabIndex = 0;
			article.setAttribute("role", "button");
			const copy = DevinRemoteApp.element("div", "history-copy");
			copy.append(DevinRemoteApp.element("strong", "", commit.subject || "Untitled commit"), DevinRemoteApp.element("span", "", `${commit.author} · ${DevinRemoteApp.relativeTime(commit.date)}`));
			article.append(DevinRemoteApp.element("code", "hash-chip", commit.shortHash), copy);
			return article;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-git", "No commits", "No Git history is available.")]);
	}
	/**
	 * Renders branches.
	 * @returns {*} The resulting value.
	 */
	renderBranches() {
		const target = this.slot("git-branches");
		if (!target) return;
		const nodes = this.branches.map(branch => {
			const button = DevinRemoteApp.element("button", `branch-row${branch.current ? " current" : ""}`);
			button.type = "button";
			button.setAttribute("data-branch-name", String(branch.name));
			const copy = DevinRemoteApp.element("div", "branch-copy");
			copy.append(DevinRemoteApp.element("strong", "", branch.name), DevinRemoteApp.element("span", "", branch.subject || branch.upstream || "Local branch"));
			button.append(copy, DevinRemoteApp.element("span", "branch-state", branch.current ? "Current" : "Switch"));
			return button;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-git", "No branches", "Create the first branch from this project.")]);
	}
	/** Refreshes git meta. */
	async refreshGitMeta() {
		try {
			const [log, branches, stashes] = await Promise.all([
				this.request("api/git/log?limit=40", {
					method: "GET"
				}),
				this.request("api/git/branches", {
					method: "GET"
				}),
				this.request("api/git/stashes", {
					method: "GET"
				})
			]);
			this.gitLog = log.items ?? [];
			this.branches = branches.items ?? [];
			this.stashes = stashes.items ?? [];
			this.renderGitHistory();
			this.renderBranches();
			this.renderStashes();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Git stage all. */
	async gitStageAll() {
		try {
			this.git = await this.request("api/git/stage-all", {
				method: "POST",
				body: {}
			});
			this.renderGit();
			this.toast("All changes staged.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Git unstage all. */
	async gitUnstageAll() {
		try {
			this.git = await this.request("api/git/unstage-all", {
				method: "POST",
				body: {}
			});
			this.renderGit();
			this.toast("All changes unstaged.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Git commit dialog. */
	async gitCommitDialog() {
		const values = await this.formDialog("Commit staged changes",
			[{
				name: "message",
				label: "Commit message",
				required: true,
				placeholder: "Describe this change"
			}], "Commit");
		if (!values) return;
		try {
			const result = await this.request("api/git/commit", {
				method: "POST",
				body: {
					message: values.message
				}
			});
			this.git = getAttribute(result, "status");
			this.gitLog = result.log ?? this.gitLog;
			this.renderGit();
			this.renderGitHistory();
			this.toast("Commit created.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Git pull. */
	async gitPull() {
		if (!await this.confirmDialog("Pull changes?", "Fast-forward the current branch from its upstream.", "Pull")) return;
		try {
			const result = await this.request("api/git/pull", {
				method: "POST",
				body: {}
			});
			this.git = getAttribute(result, "status");
			this.renderGit();
			this.toast(result.output || "Pull complete.");
			await this.refreshGitMeta();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Git push. */
	async gitPush() {
		if (!await this.confirmDialog("Push changes?", "Push the current branch to its configured remote.", "Push")) return;
		try {
			const result = await this.request("api/git/push", {
				method: "POST",
				body: {}
			});
			this.git = getAttribute(result, "status");
			this.renderGit();
			this.toast(result.output || "Push complete.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Git branch dialog. */
	async gitBranchDialog() {
		const values = await this.formDialog("Create branch",
			[{
				name: "name",
				label: "Branch name",
				required: true,
				placeholder: "feature/mobile-control"
			}], "Create & switch");
		if (!values) return;
		try {
			const result = await this.request("api/git/branch", {
				method: "POST",
				body: {
					name: values.name,
					checkout: true
				}
			});
			this.git = getAttribute(result, "status");
			this.branches = result.branches ?? [];
			this.renderGit();
			this.renderBranches();
			this.toast(`Switched to ${values.name}.`);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Checks out branch.
	 * @param {*} name Name.
	 */
	async checkoutBranch(name) {
		if (this.branches.find(branch => branch.name === name)?.current) return;
		if (!await this.confirmDialog("Switch branch?", `Switch the workspace to ${name}.`, "Switch")) return;
		try {
			const result = await this.request("api/git/checkout", {
				method: "POST",
				body: {
					name
				}
			});
			this.git = getAttribute(result, "status");
			this.branches = result.branches ?? [];
			this.renderGit();
			this.renderBranches();
			this.toast(`Switched to ${name}.`);
			await this.refreshTree();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Discards selected diff. */
	async discardSelectedDiff() {
		if (!this.selectedDiff) return;
		const path = this.selectedDiff.path;
		if (!await this.confirmDialog("Discard local changes?", `${path}\n\nThis cannot be undone from Devin Remote.`, "Discard")) return;
		try {
			this.git = await this.request("api/git/discard", {
				method: "POST",
				body: {
					path
				}
			});
			this.selectedDiff = null;
			this.renderGit();
			this.setScreen("diff");
			this.toast(`Discarded ${path}.`);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders tasks.
	 * @returns {*} The resulting value.
	 */
	renderTasks() {
		const target = this.slot("tasks");
		if (!target) return;
		const nodes = this.tasks.slice(0, 24).map(task => {
			const button = DevinRemoteApp.element("button", "task-row");
			button.type = "button";
			button.setAttribute("data-task-command", String(task.command));
			const copy = DevinRemoteApp.element("div", "task-copy");
			copy.append(DevinRemoteApp.element("strong", "", task.label), DevinRemoteApp.element("span", "", task.detail || task.source));
			button.append(copy, DevinRemoteApp.icon("i-terminal"));
			return button;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-terminal", "No detected tasks", "Use the terminal for arbitrary commands.")]);
	}
	/**
	 * Runs task.
	 * @param {*} command Command.
	 */
	async runTask(command) {
		this.setScreen("terminal");
		if (!this.terminalId) await this.newTerminal();
		if (!this.terminalId) return;
		try {
			await this.request(`api/terminal/${encodeURIComponent(this.terminalId)}/input`, {
				method: "POST",
				body: {
					input: `${command}\n`
				}
			});
			this.toast(`Running ${command}.`);
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Select terminal.
	 * @param {*} id Id.
	 */
	selectTerminal(id) {
		const session = this.terminalSessions.get(id);
		if (!session) return;
		this.terminalId = id;
		this.terminalBuffer = session.buffer ?? "";
		this.slot("terminal-meta").textContent = `${session.shell} · ${session.platform}`;
		this.renderTerminalTabs();
		this.renderTerminal();
	}
	/**
	 * Renders terminal tabs.
	 * @returns {*} The resulting value.
	 */
	renderTerminalTabs() {
		const target = this.slot("terminal-tabs");
		if (!target) return;
		const nodes = [...this.terminalSessions.values()].map((session, index) => {
			const button = DevinRemoteApp.element("button", `terminal-tab${session.id === this.terminalId ? " selected" : ""}`, `Shell ${index + 1}`);
			button.type = "button";
			button.setAttribute("data-terminal-id", String(session.id));
			return button;
		});
		DevinRemoteApp.replace(target, nodes);
	}
	/** Clears terminal. */
	clearTerminal() {
		const session = this.terminalSessions.get(this.terminalId);
		if (!session) return;
		session.buffer = "";
		this.terminalBuffer = "";
		this.renderTerminal();
	}
	/** Interrupt terminal. */
	async interruptTerminal() {
		if (!this.terminalId) return;
		try {
			await this.request(`api/terminal/${encodeURIComponent(this.terminalId)}/input`, {
				method: "POST",
				body: {
					input: "\u0003"
				}
			});
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Closes terminal. */
	async closeTerminal() {
		if (!this.terminalId) return;
		const id = this.terminalId;
		try {
			await this.request(`api/terminal/${encodeURIComponent(id)}`, {
				method: "DELETE",
				body: {}
			});
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
		this.terminalSources.get(id)?.close();
		this.terminalSources.delete(id);
		this.terminalSessions.delete(id);
		const next = this.terminalSessions.keys().next().value ?? "";
		this.terminalId = next;
		this.terminalBuffer = next ? this.terminalSessions.get(next)?.buffer ?? "" : "";
		this.renderTerminalTabs();
		this.renderTerminal();
		if (!next) this.slot("terminal-meta").textContent = "No shell open";
	}
	/**
	 * Renders projects.
	 * @returns {*} The resulting value.
	 */
	renderProjects() {
		const target = this.slot("projects");
		if (!target) return;
		const nodes = ( /** @type {any[]} */ (this.bootstrap?.projects ?? [])).map(project => {
			const button = DevinRemoteApp.element("button", `project-row${project.id === this.projectId ? " current" : ""}`);
			button.type = "button";
			button.setAttribute("data-project-path", String(project.path));
			const copy = DevinRemoteApp.element("div", "project-row-copy");
			copy.append(DevinRemoteApp.element("strong", "", project.name), DevinRemoteApp.element("span", "", project.gitRemote || "Local workspace"));
			button.append(copy, DevinRemoteApp.element("span", "project-state", project.id === this.projectId ? "Current" : "Open"));
			return button;
		});
		DevinRemoteApp.replace(target, nodes);
	}
	/**
	 * Renders stats.
	 * @returns {*} The resulting value.
	 */
	renderStats() {
		const target = this.slot("workspace-stats");
		if (!target || !this.workspaceStats) return;
		const stats = [
			["Files", this.workspaceStats.files.toLocaleString()],
			["Folders", this.workspaceStats.directories.toLocaleString()],
			["Size", DevinRemoteApp.formatBytes(this.workspaceStats.bytes)]
		];
		const summary = stats.map(([label, value]) => {
			const node = DevinRemoteApp.element("div", "usage-card");
			node.append(DevinRemoteApp.element("strong", "", value), DevinRemoteApp.element("span", "", label));
			return node;
		});
		const languages = DevinRemoteApp.element("div", "language-list");
		/** @type {any[]} */
		(this.workspaceStats.languages ?? []).forEach(item => {
			const row = DevinRemoteApp.element("div", "language-row");
			row.append(DevinRemoteApp.element("span", "", item.language), DevinRemoteApp.element("strong", "", String(item.files)));
			languages.append(row);
		});
		target.replaceChildren(...summary, languages);
		this.renderOverview();
	}
	/** Refreshes stats. */
	async refreshStats() {
		try {
			this.workspaceStats = await this.request("api/workspace/stats", {
				method: "GET"
			});
			this.renderStats();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders activity.
	 * @returns {*} The resulting value.
	 */
	renderActivity() {
		const target = this.slot("activity");
		if (!target) return;
		const nodes = this.activity.slice(0, 40).map(item => {
			const row = DevinRemoteApp.element("article", "activity-row");
			const icon = DevinRemoteApp.element("div", "activity-icon");
			icon.append(DevinRemoteApp.icon(item.type === "git" ? "i-git" : item.type === "agent" ? "i-agent" : item.type === "terminal" ? "i-terminal" : item.type === "security" ? "i-settings" : "i-file"));
			const copy = DevinRemoteApp.element("div", "activity-copy");
			copy.append(DevinRemoteApp.element("strong", "", item.title), DevinRemoteApp.element("span", "", item.detail || DevinRemoteApp.relativeTime(item.createdAt)));
			const time = DevinRemoteApp.element("time", "", DevinRemoteApp.relativeTime(item.createdAt));
			row.append(icon, copy, time);
			return row;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-more", "No activity yet", "Remote actions will appear here.")]);
	}
	/** Refreshes activity. */
	async refreshActivity() {
		try {
			const data = await this.request("api/activity?limit=80", {
				method: "GET"
			});
			this.activity = data.items ?? [];
			this.renderActivity();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Renders security.
	 * @returns {*} The resulting value.
	 */
	renderSecurity() {
		const target = this.slot("paired-devices");
		if (!target) return;
		const nodes = this.pairedDevices.map(device => {
			const row = DevinRemoteApp.element("article", "device-row");
			const copy = DevinRemoteApp.element("div", "device-copy");
			copy.append(DevinRemoteApp.element("strong", "", DevinRemoteApp.relativeTime(device.lastSeenAt) || "Paired device"), DevinRemoteApp.element("span", "", device.userAgent || device.remoteAddress || "Remote browser"));
			const current = device.id === this.currentDeviceId;
			const button = DevinRemoteApp.element("button", `button compact${current ? " secondary" : " danger"}`, current ? "Current" : "Revoke");
			button.type = "button";
			button.disabled = current;
			if (!current) button.setAttribute("data-device-id", String(device.id));
			row.append(copy, button);
			return row;
		});
		DevinRemoteApp.replace(target, nodes.length ? nodes : [DevinRemoteApp.empty("i-settings", "No paired devices", "Pair this browser again from VS Code.")]);
	}
	/** Refreshes security. */
	async refreshSecurity() {
		try {
			const data = await this.request("api/security/sessions", {
				method: "GET"
			});
			this.pairedDevices = data.items ?? [];
			this.currentDeviceId = data.currentId ?? this.currentDeviceId;
			this.renderSecurity();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Revokes device.
	 * @param {*} id Id.
	 */
	async revokeDevice(id) {
		if (!await this.confirmDialog("Revoke device?", "This device will lose access to this project immediately.", "Revoke")) return;
		try {
			await this.request(`api/security/sessions/${encodeURIComponent(id)}`, {
				method: "DELETE",
				body: {}
			});
			await this.refreshSecurity();
			this.toast("Paired device revoked.");
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/**
	 * Command palette.
	 * @returns {Promise<void>} The resulting value.
	 */
	async commandPalette() {
		const commands = [{
			id: "agent",
			label: "Start Devin agent",
			icon: "i-agent"
		}, {
			id: "search",
			label: "Search workspace",
			icon: "i-search"
		}, {
			id: "problems",
			label: `Open Problems (${this.diagnostics.length})`,
			icon: "i-warning"
		}, {
			id: "upload",
			label: "Upload project file",
			icon: "i-upload"
		}, {
			id: "terminal",
			label: "Open new terminal",
			icon: "i-terminal"
		}, {
			id: "commit",
			label: "Commit staged changes",
			icon: "i-git"
		}, {
			id: "compare",
			label: "Compare Git revisions",
			icon: "i-diff"
		}, {
			id: "branch",
			label: "Create Git branch",
			icon: "i-git"
		}, {
			id: "stash",
			label: "Stash working tree",
			icon: "i-archive"
		}, ...(this.selectedFile ? [{
			id: "bookmark",
			label: "Toggle current file bookmark",
			icon: "i-bookmark"
		}, {
			id: "desktop",
			label: "Open current file in VS Code",
			icon: "i-external"
		}, {
			id: "history",
			label: "Show current file history",
			icon: "i-clock"
		}] : []), {
			id: "settings",
			label: "Project settings",
			icon: "i-settings"
		}, {
			id: "notify",
			label: "Enable agent notifications",
			icon: "i-settings"
		}];
		const action = await this.actionDialog("Project commands", commands);
		const actions = {
			agent: () => this.newAgent(),
			search: () => this.workspaceSearchDialog(),
			problems: () => {
				this.setScreen("files");
				this.setCodeContext("problems");
			},
			upload: () => this.uploadFile(),
			terminal: () => {
				this.setScreen("terminal");
				return this.newTerminal();
			},
			commit: () => this.gitCommitDialog(),
			compare: () => {
				this.setScreen("diff");
				this.setGitMode("compare");
			},
			branch: () => this.gitBranchDialog(),
			stash: () => this.gitStashDialog(),
			bookmark: () => this.toggleBookmark(),
			desktop: () => this.openInVsCode(),
			history: () => this.loadFileHistory(),
			settings: () => this.setScreen("more"),
			notify: () => this.enableNotifications()
		};
		const handler = /** @type {Record<string, () => unknown>} */ (actions)[ /** @type {string} */ (action)];
		if (handler) await handler();
	}
	/**
	 * Renders providers.
	 * @returns {*} The resulting value.
	 */
	renderProviders() {
		const cards = this.providers.map(provider => {
			const card = DevinRemoteApp.element("article", "provider-card");
			const main = DevinRemoteApp.element("div", "provider-main");
			const icon = DevinRemoteApp.element("div", "provider-icon");
			icon.append(DevinRemoteApp.icon("i-provider"));
			const copy = DevinRemoteApp.element("div", "provider-copy");
			copy.append(DevinRemoteApp.element("strong", "", provider.label), DevinRemoteApp.element("span", "", provider.model || "No model configured"));
			main.append(icon, copy);
			card.append(main, DevinRemoteApp.element("span", `status-pill ${provider.configured ? "running" : "waiting"}`, provider.configured ? "Connected" : "Setup in VS Code"));
			return card;
		});
		DevinRemoteApp.replace(this.slot("providers"), cards.length ? cards : [DevinRemoteApp.empty("i-provider", "No providers", "Configure a provider in VS Code.")]);
		const activeSessions = this.sessions.filter(session => /running|working|active/u.test(getAttribute(DevinRemoteApp, "status")(session))).length;
		const totalAcu = this.sessions.reduce((sum, session) => sum + Number(session.acu_usage ?? session.acu_used ?? session.consumed_acu ?? 0), 0);
		const usage = [{
			label: "Devin active",
			value: activeSessions
		}, {
			label: "Devin ACU",
			value: totalAcu.toFixed(2)
		}, ...this.providers.flatMap(provider => [{
			label: `${provider.label} requests`,
			value: provider.usage?.requests ?? 0
		}, {
			label: `${provider.label} tokens`,
			value: (provider.usage?.inputTokens ?? 0) + (provider.usage?.outputTokens ?? 0)
		}])].map(item => {
			const card = DevinRemoteApp.element("div", "usage-card");
			card.append(DevinRemoteApp.element("strong", "", Number(item.value).toLocaleString()), DevinRemoteApp.element("span", "", item.label));
			return card;
		});
		DevinRemoteApp.replace(this.slot("usage"), usage);
		const select = this.slot("llm-provider");
		const options = this.providers.filter(provider => provider.configured).map(provider => {
			const option = DevinRemoteApp.element("option", "", `${provider.label} · ${provider.model}`);
			option.value = provider.id;
			return option;
		});
		select.replaceChildren(...options);
		select.disabled = !options.length;
	}
	/** Refreshes providers. */
	async refreshProviders() {
		try {
			const data = await this.request("api/providers", {
				method: "GET"
			});
			this.providers = data.providers ?? [];
			this.renderProviders();
		} catch (error) {
			this.toast(DevinRemoteApp.errorText(error), "error");
		}
	}
	/** Renders project facts. */
	renderProjectFacts() {
		const facts = [
			["Project", this.bootstrap.project.name],
			["Branch", this.git.branch ?? "—"],
			["Remote", this.bootstrap.project.gitRemote || "No origin remote"],
			["Endpoint", `/p/${this.projectId}/`],
			["Runtime",
				this.systemInfo ? `${this.systemInfo.platform}/${this.systemInfo.arch} · ${this.systemInfo.node}` : "—"
			],
			["Gateway",
				this.systemInfo ? `port ${this.systemInfo.gatewayPort} · ${DevinRemoteApp.duration(this.systemInfo.uptimeSeconds)}` : "—"
			]
		];
		const fragment = document.createDocumentFragment();
		facts.forEach(([term, value]) => {
			fragment.append(DevinRemoteApp.element("dt", "", term), DevinRemoteApp.element("dd", "", value));
		});
		this.slot("project-facts").replaceChildren(fragment);
		this.slot("repo-state").textContent = this.git.repository ? "Git repository" : "Local project";
	}
	/**
	 * Sends llm.
	 * @param {*} formData FormData.
	 * @param {*} form Form.
	 */
	async sendLlm(formData, form) {
		const provider = this.slot("llm-provider").value;
		const prompt = String(formData.get("prompt") ?? "").trim();
		if (!provider || !prompt) return;
		const button = form.querySelector("button[type=submit]");
		button.disabled = true;
		this.slot("llm-response").textContent = "Thinking…";
		try {
			const context = this.selectedFile ? `\n\nCurrently selected file: ${this.selectedFile.path}\n\`\`\`\n${this.selectedFile.content.slice(0, 12000)}\n\`\`\`` : "";
			const model = String(formData.get("model") ?? "").trim();
			const result = await this.request("api/llm/chat", {
				method: "POST",
				body: {
					provider,
					model,
					messages: [{
						role: "system",
						content: `You are assisting with project ${this.bootstrap.project.name}. Be concise and code-focused.`
					}, {
						role: "user",
						content: `${prompt}${context}`
					}]
				}
			});
			this.slot("llm-response").textContent = result.text || "No text returned.";
			await this.refreshProviders();
		} catch (error) {
			this.slot("llm-response").textContent = DevinRemoteApp.errorText(error);
		} finally {
			button.disabled = false;
		}
	}
	/**
	 * Refreshes current.
	 * @returns {Promise<void>} The resulting value.
	 */
	async refreshCurrent() {
		if (this.activeScreen === "dashboard") {
			await Promise.all([this.refreshAgents(), this.refreshGit(), this.refreshActivity()]);
			this.renderDashboard();
			return;
		}
		if (this.activeScreen === "agents") {
			this.refreshAgents();
			return;
		}
		if (this.activeScreen === "chat") {
			await Promise.all([this.loadMessages(false), this.loadAgentDetails()]);
			return;
		}
		if (["files", "editor"].includes(this.activeScreen)) {
			this.refreshTree();
			return;
		}
		if (["diff", "diff-detail"].includes(this.activeScreen)) {
			this.refreshGit();
			return;
		}
		if (this.activeScreen === "more") await Promise.all([
			this.refreshProviders(),
			this.refreshActivity(),
			this.refreshSecurity(),
			this.refreshStats(),
			this.refreshDiagnostics()
		]);
	}
	/** @type {(target: any, nodes: Node[]) => void} */
	static replace = (target, nodes) => {
		const fragment = document.createDocumentFragment();
		nodes.forEach(node => {
			fragment.append(node);
		});
		target.replaceChildren(fragment);
	};
	/** @type {(icon: string, title: string, body: string) => HTMLElement} */
	static empty = (icon, title, body) => {
		const node = DevinRemoteApp.element("div", "empty-state");
		node.append(DevinRemoteApp.icon(icon), DevinRemoteApp.element("strong", "", title), DevinRemoteApp.element("span", "", body));
		return node;
	};
	/** @type {(name: string, value: unknown) => void} */
	static badge = (name, value) => {
		const badge = document.querySelector(`[data-badge="${name}"]`);
		if (badge) badge.textContent = value ? String(value) : "";
	};
	/** @type {(item: any) => string} */
	static insightText = item => {
		if (typeof item === "string") return item;
		if (!item || typeof item !== "object") return DevinRemoteApp.text(item);
		return DevinRemoteApp.text(item.title ?? item.description ?? item.text ?? item.summary ?? JSON.stringify(item));
	};
	/** @type {(seconds: number) => string} */
	static duration = seconds => {
		const value = Math.max(0, Number(seconds ?? 0));
		if (value < 60) return `${Math.round(value)}s uptime`;
		if (value < 3600) return `${Math.floor(value / 60)}m uptime`;
		if (value < 86400) return `${Math.floor(value / 3600)}h uptime`;
		return `${Math.floor(value / 86400)}d uptime`;
	};
	/** @type {(bytes: Uint8Array) => string} */
	static bytesToBase64 = bytes => {
		const parts = [];
		for (let offset = 0; offset < bytes.length; offset += 32768) parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 32768)));
		return btoa(parts.join(""));
	};
	/** @type {(bytes: number) => string} */
	static formatBytes = bytes => {
		const value = Number(bytes ?? 0);
		if (value < 1024) return `${value} B`;
		if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
		return `${(value / 1048576).toFixed(1)} MB`;
	};
	/**
	 * Toast.
	 * @param {*} message Message.
	 * @param {string} type Type.
	 */
	toast(message, type = "info") {
		const toast = DevinRemoteApp.element("div", "toast", message);
		if (type === "error") toast.setAttribute("role", "alert");
		this.slot("toasts").append(toast);
		setTimeout(() => toast.remove(), 4200);
	}
	/**
	 * Form dialog.
	 * @param {string} title Title.
	 * @param {any[]} fields Fields.
	 * @param {string} submitLabel SubmitLabel.
	 * @returns {Promise<any>} The resulting value.
	 */
	async formDialog(title, fields, submitLabel) {
		const body = this.slot("modal-body");
		const actions = this.slot("modal-actions");
		const modal = this.slot("modal");
		this.slot("modal-title").textContent = title;
		const formFields = document.createDocumentFragment();
		fields.forEach(field => {
			const label = DevinRemoteApp.element("label");
			label.append(document.createTextNode(field.label));
			const input = DevinRemoteApp.element(field.type === "textarea" ? "textarea" : "input");
			input.name = field.name;
			if (field.type && field.type !== "textarea") /** @type {HTMLInputElement} */ (input).type = field.type;
			if (field.value !== undefined) input.value = field.value;
			if (field.placeholder) input.placeholder = field.placeholder;
			input.required = Boolean(field.required);
			label.append(input);
			formFields.append(label);
		});
		body.replaceChildren(formFields);
		const cancel = DevinRemoteApp.element("button", "button secondary", "Cancel");
		cancel.type = "button";
		const submit = DevinRemoteApp.element("button", "button primary", submitLabel);
		submit.type = "button";
		actions.replaceChildren(cancel, submit);
		modal.showModal();
		return new Promise(resolve => {
			/** @type {(value: any) => void} */
			const finish = value => {
				modal.close();
				resolve(value);
			};
			cancel.addEventListener("click", () => finish(null), {
				once: true
			});
			submit.addEventListener("click", () => {
				const inputs = [...( /** @type {NodeListOf<HTMLInputElement | HTMLTextAreaElement>} */ (body.querySelectorAll("input,textarea")))];
				if (inputs.some(input => !input.reportValidity())) return;
				finish(Object.fromEntries(inputs.map(input => [input.name, input.value])));
			}, {
				once: true
			});
			modal.addEventListener("cancel", () => finish(null), {
				once: true
			});
		});
	}
	/**
	 * Action dialog.
	 * @param {string} title Title.
	 * @param {any[]} items Items.
	 * @returns {Promise<any>} The resulting value.
	 */
	async actionDialog(title, items) {
		const body = this.slot("modal-body");
		const actions = this.slot("modal-actions");
		const modal = this.slot("modal");
		this.slot("modal-title").textContent = title;
		const fragment = document.createDocumentFragment();
		items.forEach(item => {
			const button = DevinRemoteApp.element("button", `button ${item.danger ? "danger" : "secondary"}`, item.label);
			button.type = "button";
			button.setAttribute("data-modal-value", String(item.id));
			button.prepend(DevinRemoteApp.icon(item.icon));
			fragment.append(button);
		});
		body.replaceChildren(fragment);
		const cancel = DevinRemoteApp.element("button", "button secondary", "Cancel");
		cancel.type = "button";
		actions.replaceChildren(cancel);
		modal.showModal();
		return new Promise(resolve => {
			/** @type {(value: any) => void} */
			const finish = value => {
				modal.close();
				resolve(value);
			};
			/** @type {HTMLElement} */
			(body).addEventListener("click", event => {
				const button = /** @type {HTMLElement} */ (event.target).closest("[data-modal-value]");
				if (button) finish( /** @type {HTMLElement} */ (button).getAttribute("data-modal-value"));
			}, {
				once: true
			});
			cancel.addEventListener("click", () => finish(null), {
				once: true
			});
			modal.addEventListener("cancel", () => finish(null), {
				once: true
			});
		});
	}
	/**
	 * Confirm dialog.
	 * @param {string} title Title.
	 * @param {string} bodyText BodyText.
	 * @param {string} submitLabel SubmitLabel.
	 * @returns {Promise<any>} The resulting value.
	 */
	confirmDialog(title, bodyText, submitLabel) {
		const body = this.slot("modal-body");
		const actions = this.slot("modal-actions");
		const modal = this.slot("modal");
		this.slot("modal-title").textContent = title;
		body.replaceChildren(DevinRemoteApp.element("p", "", bodyText));
		const cancel = DevinRemoteApp.element("button", "button secondary", "Cancel");
		cancel.type = "button";
		const submit = DevinRemoteApp.element("button", "button danger", submitLabel);
		submit.type = "button";
		actions.replaceChildren(cancel, submit);
		modal.showModal();
		return new Promise(resolve => {
			/** @type {(value: any) => void} */
			const finish = value => {
				modal.close();
				resolve(value);
			};
			cancel.addEventListener("click", () => finish(false), {
				once: true
			});
			submit.addEventListener("click", () => finish(true), {
				once: true
			});
			modal.addEventListener("cancel", () => finish(false), {
				once: true
			});
		});
	}
	dispose() {
		clearInterval(this.messageTimer);
		this.eventSource?.close();
		this.terminalSource?.close();
		this.terminalSources.forEach(source => {
			source.close();
		});
		this.terminalSources.clear();
	}
}
if (typeof module === "undefined") {
	const app = new DevinRemoteApp();
	Reflect.set(window, "devinRemoteApp", app);
} else module.exports = {
	DevinRemoteApp
};
