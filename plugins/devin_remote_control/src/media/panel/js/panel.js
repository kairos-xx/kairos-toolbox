"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
/**
 * Mirrors the VS Code theme variables consumed by the token graph.
 * The stylesheet may only read --dynamic-* values, so the host-provided
 * --vscode-* variables are copied onto :root once here and re-synced
 * whenever the webview becomes visible again.
 */
const THEME_VARS = ["--vscode-button-background", "--vscode-button-foreground", "--vscode-button-hoverBackground", "--vscode-button-secondaryBackground", "--vscode-button-secondaryForeground", "--vscode-button-secondaryHoverBackground", "--vscode-charts-green", "--vscode-charts-red", "--vscode-charts-yellow", "--vscode-descriptionForeground", "--vscode-editor-background", "--vscode-editor-font-family", "--vscode-editorWarning-foreground", "--vscode-errorForeground", "--vscode-focusBorder", "--vscode-font-family", "--vscode-foreground", "--vscode-input-background", "--vscode-panel-border", "--vscode-sideBar-background", "--vscode-testing-iconPassed", "--vscode-widget-border", ];
/** Copies host theme variables into the dynamic token namespace. */
const syncThemeVariables = () => {
	const styles = getComputedStyle(document.documentElement);
	for (const name of THEME_VARS) {
		const value = styles.getPropertyValue(name).trim();
		if (value) document.documentElement.style.setProperty(`--dynamic-${name.slice(2)}`, value);
	}
};
syncThemeVariables();
document.addEventListener("visibilitychange", () => {
	if (!document.hidden) syncThemeVariables();
});
/** Webview panel controller for the project UI. */
class ProjectPanel {
	/**
	 * Creates an instance of.
	 * @param {Document | HTMLElement} root Root.
	 */
	constructor(root) {
		/** @type {Document | HTMLElement} */
		this.root = root;
		/** @type {{ postMessage(message: unknown): void }} */
		this.vscode = acquireVsCodeApi();
		/** @type {any} */
		this.state = null;
		/** @type {string} */
		this.selectedProjectId = "";
		/** @type {Record<string, HTMLElement>} */
		this.elements = Object.fromEntries(
			[...root.querySelectorAll("[data-slot]")].map(element => [ /** @type {HTMLElement} */ (element).getAttribute("data-slot") ?? "", /** @type {HTMLElement} */ (element)]));
	}
	/** @type {(timestamp: number) => string} */
	static formatExpiry = timestamp => {
		if (!timestamp) return "Generate a code to pair a phone.";
		const seconds = Math.max(0, Math.ceil((timestamp - Date.now()) / 1000));
		return seconds ? `Expires in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "Expired — generate a new code.";
	};
	/**
	 * Returns the named slot for.
	 * @param {string} name Name.
	 * @returns {HTMLElement} The resulting value.
	 */
	slot(name) {
		return /** @type {HTMLElement} */ (this.elements[name]);
	}
	/** Starts. */
	start() {
		this.root.addEventListener("click", event => this.onClick(event));
		this.root.addEventListener("change", event => this.onChange(event));
		window.addEventListener("message", event => this.onMessage(event));
		setInterval(() => this.renderExpiry(), 1000);
	}
	/**
	 * On message.
	 * @param {MessageEvent} event Event.
	 */
	onMessage(event) {
		if (event.data?.type !== "state") return;
		this.state = event.data.state;
		this.selectedProjectId ||= this.state.activeProjectId || this.state.projects?.[0]?.id || "";
		if (! /** @type {any[]} */ (this.state.projects ?? []).some(project => project.id === this.selectedProjectId)) this.selectedProjectId = this.state.projects?.[0]?.id ?? "";
		this.render();
	}
	/**
	 * On click.
	 * @param {Event} event Event.
	 */
	onClick(event) {
		const target = event.target instanceof Element ? event.target.closest("[data-action]") : null;
		const action = /** @type {HTMLElement | null} */ (target)?.getAttribute("data-action");
		if (!action) return;
		this.vscode.postMessage({
			action,
			projectId: this.selectedProjectId
		});
	}
	/**
	 * On change.
	 * @param {Event} event Event.
	 */
	onChange(event) {
		const select = this.elements["project-select"];
		if (event.target !== select) return;
		this.selectedProjectId = /** @type {HTMLSelectElement} */ (event.target).value;
		this.render();
	}
	/**
	 * Returns the project for.
	 * @returns {*} The resulting value.
	 */
	project() {
		return ( /** @type {any[]} */ (this.state?.projects ?? [])).find(project => project.id === this.selectedProjectId) ?? null;
	}
	render() {
		if (!this.state) return;
		const {
			gateway,
			charter,
			projects,
			connections
		} = this.state;
		this.slot("gateway-state").textContent = gateway.running ? `Gateway ${gateway.port} · ${getAttribute(charter, "state")}` : "Gateway stopped";
		if (this.slot("metric-active")) {
			this.slot("metric-active").textContent = String(this.state.metrics?.activeAgents ?? 0);
			this.slot("metric-acu").textContent = Number(this.state.metrics?.acu ?? 0).toFixed(2);
			this.slot("metric-devices").textContent = String(this.state.metrics?.pairedDevices ?? 0);
		}
		if (this.slot("charter-summary")) this.slot("charter-summary").textContent = charter.publicUrl || charter.error || getAttribute(charter, "state");
		this.slot("charter-dot").setAttribute("data-state", getAttribute(charter, "state"));
		this.slot("ngrok-state").textContent = charter.error || (charter.publicUrl ? "Online · one shared charter" : getAttribute(charter, "state"));
		this.slot("devin-state").textContent = connections.devin ? "Service credential stored" : "Not configured";
		this.slot("openai-state").textContent = connections.openAI ? "API key + model configured" : "Optional project chat";
		this.slot("anthropic-state").textContent = connections.anthropic ? "API key + model configured" : "Optional project chat";
		this.slot("local-url").textContent = gateway.localUrl || "Local gateway stopped";
		this.slot("toggle-gateway").textContent = gateway.running ? "Stop gateway" : "Start gateway";
		this.slot("toggle-charter").textContent = getAttribute(charter, "state") === "online" ? "Restart charter" : "Start charter";
		this.renderProjects(projects);
		const project = this.project();
		/** @type {HTMLInputElement} */
		(this.slot("project-url")).value = project?.url || "Charter is not online.";
		this.renderQr(project?.url ?? "");
		this.slot("pair-code").textContent = project?.pairing?.code ?? "— — — — — —";
		this.renderExpiry();
		this.renderRoutes(projects);
	}
	/**
	 * Renders projects.
	 * @param {any[]} projects Projects.
	 */
	renderProjects(projects) {
		const fragment = document.createDocumentFragment();
		projects.forEach(project => {
			const option = document.createElement("option");
			option.value = project.id;
			option.textContent = project.name;
			option.selected = project.id === this.selectedProjectId;
			fragment.append(option);
		});
		this.elements["project-select"]?.replaceChildren(fragment);
	}
	/**
	 * Renders qr.
	 * @param {string} projectUrl ProjectUrl.
	 */
	renderQr(projectUrl) {
		const container = this.slot("qr");
		if (!projectUrl) {
			const message = document.createElement("span");
			message.textContent = "Start ngrok to generate the project QR code.";
			container.replaceChildren(message);
			return;
		}
		const documentNode = new DOMParser().parseFromString(QrSvg.render(projectUrl), "image/svg+xml");
		container.replaceChildren(document.importNode(documentNode.documentElement, true));
	}
	/** Renders expiry. */
	renderExpiry() {
		if (!this.state) return;
		this.slot("pair-expiry").textContent = ProjectPanel.formatExpiry(this.project()?.pairing?.expiresAt ?? 0);
	}
	/**
	 * Renders routes.
	 * @param {any[]} projects Projects.
	 */
	renderRoutes(projects) {
		const fragment = document.createDocumentFragment();
		projects.forEach(project => {
			const row = document.createElement("div");
			row.className = "route-row";
			const name = document.createElement("strong");
			const code = document.createElement("code");
			name.textContent = project.name;
			code.textContent = project.url || `/p/${project.id}/`;
			row.append(name, code);
			fragment.append(row);
		});
		this.slot("routes").replaceChildren(fragment);
	}
}
if (typeof module === "undefined") new ProjectPanel(document).start();
else module.exports = {
	ProjectPanel,
	syncThemeVariables
};
