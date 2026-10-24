"use strict";
const vscode = require("vscode");
const {
	spawn
} = require("node:child_process");
const {
	ServerPolicy
} = require("./core.js");
/** Simple web-server lifecycle wrapper. */
class WebServer {
	/**
	 * Creates an instance of.
	 * @param {typeof vscode} api VS Code API.
	 */
	constructor(api) {
		/** @type {typeof vscode} */
		this.api = api; /** @type {import("node:child_process").ChildProcess | null} */
		this.process = null;
	}
	/**
	 * Start the configured local web server when it is not already running.
	 *
	 * @returns {Promise<void>} Resolves after the server starts and its URL is shown.
	 */
	async start() {
		if (this.process) return;
		const cfg = this.api.workspace.getConfiguration("simpleWebServer");
		const port = ServerPolicy.normalizePort(cfg.get("port", 8000));
		const root = ServerPolicy.root(this.api.workspace.workspaceFolders?.[0]?.uri.fsPath, process.cwd());
		const spec = ServerPolicy.command(cfg.get("python", "python3"), port, root);
		this.process = spawn(spec.command, spec.args, {
			stdio: "ignore"
		});
		await this.api.window.showInformationMessage(`Web server: ${ServerPolicy.url(port)}`);
	}
	/**
	 * Stop the running local web server, if present.
	 *
	 * @returns {Promise<void>} Resolves after the running server is stopped.
	 */
	async stop() {
		if (this.process) {
			this.process.kill();
			this.process = null;
		}
	}
	/**
	 * Open the configured local web server URL in the external browser.
	 *
	 * @returns {Promise<void>} Resolves after the URL is handed to the browser.
	 */
	async open() {
		const port = ServerPolicy.normalizePort(this.api.workspace.getConfiguration("simpleWebServer").get("port", 8000));
		await this.api.env.openExternal(this.api.Uri.parse(ServerPolicy.url(port)));
	}
	dispose() {
		if (this.process) this.process.kill();
		this.process = null;
	}
}
/**
 * Activates the extension.
 * @param {import("vscode").ExtensionContext} context Context.
 */
const activate = context => {
	const server = new WebServer(vscode);
	context.subscriptions.push(vscode.commands.registerCommand("simpleWebServer.start", server.start), vscode.commands.registerCommand("simpleWebServer.stop", server.stop), vscode.commands.registerCommand("simpleWebServer.open", server.open), server);
};
/** Deactivates the extension. */
const deactivate = () => {};
module.exports = {
	activate,
	deactivate,
	WebServer
};
