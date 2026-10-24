"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const {
	ProviderService
} = require("../../../../plugins/devin_remote_control/src/extension/lib/provider-service.js");
const {
	NgrokManager
} = require("../../../../plugins/devin_remote_control/src/extension/lib/ngrok-manager.js");
const {
	TerminalManager
} = require("../../../../plugins/devin_remote_control/src/extension/lib/shell-session.js");
/** @typedef {typeof Module & {_load(request: string, parent: NodeModule | null | undefined, isMain: boolean): unknown}} ModuleLoader */
const loader = /** @type {ModuleLoader} */ ( /** @type {unknown} */ (Module));
const original = loader._load;
loader._load = (
	/** @type {string} */
	request,
	/** @type {NodeModule | null | undefined} */
	parent,
	/** @type {boolean} */
	isMain, ) => {
	if (request === "vscode") return {};
	return original.call(loader, request, parent, isMain);
};
const {
	ProjectRegistry
} = require("../../../../plugins/devin_remote_control/src/extension/lib/project-registry.js");
loader._load = original;
test("provider usage history is capped", async () => {
	const service = new ProviderService({
		openAIBaseUrl: "https://api",
		openAIModel: "m1",
		anthropicModel: "m2"
	}, async () => "key", );
	for (let index = 0; index < 45; index += 1) service.addUsage("anthropic", 1, 2, 3, "m2");
	const providers = await service.providers();
	const anthropic = providers[1];
	if (anthropic === undefined) throw new Error("Anthropic provider missing.");
	assert.equal(anthropic.history.length, 40);
	assert.equal(anthropic.usage.requests, 45);
});
test("project registry rejects unknown project", () => {
	/** Disposable returned by mocked VS Code event registrations. */
	const disposable = {
		dispose: () => {}
	};
	const vscode = /** @type {typeof import("vscode")} */ ( /** @type {unknown} */ ({
		workspace: {
			workspaceFolders: [],
			onDidChangeWorkspaceFolders: () => disposable,
			getWorkspaceFolder: () => undefined,
		},
		window: {
			onDidChangeActiveTextEditor: () => disposable,
			activeTextEditor: undefined,
		},
		languages: {
			onDidChangeDiagnostics: () => disposable
		},
	}));
	const registry = new ProjectRegistry(vscode);
	assert.throws(
		() => registry.get("missing"),
		( /** @type {unknown} */ error) => error instanceof Error && Reflect.get(error, "status") === 404, );
	assert.equal(registry.active(), null);
	registry.dispose();
});
test("ngrok log parsing and lifecycle helpers", () => {
	const manager = new NgrokManager("ngrok");
	manager.inspectLog(Buffer.from('{"url":"https://demo.ngrok.app"}\n'));
	assert.equal(manager.snapshot().state, "online");
	assert.equal(manager.snapshot().publicUrl, "https://demo.ngrok.app");
	manager.inspectLog(Buffer.from("ERR_NGROK failure\n"));
	assert.match(manager.snapshot().error, /ERR_NGROK/u);
	manager.stop();
});
test("shell manager rejects unknown sessions", () => {
	const manager = new TerminalManager("/bin/sh");
	assert.deepEqual(manager.list("project"), []);
	assert.throws(
		() => manager.get("project", "missing"),
		( /** @type {unknown} */ error) => error instanceof Error && Reflect.get(error, "status") === 404, );
	manager.closeAll();
});
test("provider chat covers credentials, upstream failures, and both APIs", async () => {
	const missing = new ProviderService({
		openAIBaseUrl: "https://api.test/",
		openAIModel: "",
		anthropicModel: ""
	}, async () => undefined, );
	await assert.rejects(missing.chat("openai-compatible", [], ""));
	await assert.rejects(missing.chat("anthropic", [], ""));
	const service = new ProviderService({
		openAIBaseUrl: "https://api.test/",
		openAIModel: "gpt-test",
		anthropicModel: "claude-test"
	}, async () => "secret", );
	const originalFetch = globalThis.fetch;
	try {
		globalThis.fetch = /** @type {typeof fetch} */ (async input => {
			const url = String(input);
			if (url.includes("anthropic")) {
				return /** @type {Response} */ ( /** @type {unknown} */ ({
					ok: true,
					status: 200,
					text: async () => JSON.stringify({
						model: "claude-test",
						usage: {
							input_tokens: 3,
							output_tokens: 4
						},
						content: [{
							type: "text",
							text: "anthropic reply"
						}, {
							type: "tool",
							text: "ignored"
						}]
					})
				}));
			}
			return /** @type {Response} */ ( /** @type {unknown} */ ({
				ok: true,
				status: 200,
				text: async () => JSON.stringify({
					model: "gpt-test",
					usage: {
						prompt_tokens: 1,
						completion_tokens: 2
					},
					choices: [{
						message: {
							content: "openai reply"
						}
					}]
				})
			}));
		});
		const openAI = await service.chat("openai-compatible", [{
			role: "user",
			content: "hello"
		}], "");
		const anthropic = await service.chat("anthropic", [{
			role: "system",
			content: "system"
		}, {
			role: "user",
			content: "hello"
		}, ], "");
		assert.equal(openAI.text, "openai reply");
		assert.equal(anthropic.text, "anthropic reply");
		assert.equal(openAI.usage?.inputTokens, 1);
		assert.equal(anthropic.usage?.outputTokens, 4);
		await assert.rejects(ProviderService.parseResponse( /** @type {Response} */ ( /** @type {unknown} */ ({
			ok: true,
			status: 200,
			text: async () => "not-json"
		})), "Broken"), );
		await assert.rejects(ProviderService.parseResponse(
			/** @type {Response} */
			( /** @type {unknown} */ ({
				ok: false,
				status: 403,
				text: async () => JSON.stringify({
					error: {
						message: "denied"
					}
				})
			})), "Denied", ), );
	} finally {
		Reflect.set(globalThis, "fetch", originalFetch);
	}
});
