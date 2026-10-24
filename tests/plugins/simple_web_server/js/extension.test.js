"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
/** @typedef {typeof Module & {_load(request: string, parent: NodeModule | null | undefined, isMain: boolean): unknown}} ModuleLoader */
const loader = /** @type {ModuleLoader} */ ( /** @type {unknown} */ (Module));
const original = loader._load;
let killed = false;
const child = /** @type {import("node:child_process").ChildProcess} */ (
	/** @type {unknown} */
	({
		kill: () => {
			killed = true;
			return true;
		}
	}));
loader._load = (
	/** @type {string} */
	request,
	/** @type {NodeModule | null | undefined} */
	parent,
	/** @type {boolean} */
	isMain, ) => {
	if (request === "vscode") return {};
	if (request === "node:child_process") return {
		spawn: () => child
	};
	return original.call(loader, request, parent, isMain);
};
const {
	WebServer
} = require("../../../../plugins/simple_web_server/src/extension.js");
loader._load = original;
test("server starts opens and stops", async () => {
	/** @type {import("vscode").Uri[]} */
	const opened = [];
	const api = /** @type {typeof import("vscode")} */ ( /** @type {unknown} */ ({
		workspace: {
			workspaceFolders: [{
				uri: {
					fsPath: "/site"
				}
			}],
			getConfiguration: () => ({
				get: ( /** @type {string} */ key, /** @type {unknown} */ fallback) => key === "port" ? 9000 : fallback,
			}),
		},
		window: {
			showInformationMessage: async () => undefined
		},
		env: {
			openExternal: async ( /** @type {import("vscode").Uri} */ value) => {
				opened.push(value);
				return true;
			}
		},
		Uri: {
			parse: ( /** @type {string} */ value) => value
		},
	}));
	const server = new WebServer(api);
	await server.start();
	await server.start();
	await server.open();
	assert.equal(String(opened[0]), "http://127.0.0.1:9000/");
	await server.stop();
	assert.equal(killed, true);
});
