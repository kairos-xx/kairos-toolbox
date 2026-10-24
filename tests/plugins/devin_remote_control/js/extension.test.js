"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
/** @typedef {typeof Module & {_load(request: string, parent: NodeModule | null | undefined, isMain: boolean): unknown}} ModuleLoader */
const loader = /** @type {ModuleLoader} */ ( /** @type {unknown} */ (Module));
const original = loader._load;
/** @type {string[]} */
const errors = [];
/** @type {string[]} */
const information = [];
/** @type {string[]} */
const opened = [];
/** @type {string[]} */
const copied = [];
/** @type {string[]} */
const inputs = [];
/** @type {Array<{label: string, id: string}>} */
const picks = [];
/** @type {Array<[string, unknown]>} */
const updates = [];
/** @type {Map<string, boolean | number | string>} */
const configuration = new Map( /** @type {Array<[string, boolean | number | string]>} */ ([
	["gateway.autoStart", false],
	["gateway.port", 0],
	["ngrok.autoStart", false],
]));
loader._load = (
	/** @type {string} */
	request,
	/** @type {NodeModule | null | undefined} */
	parent,
	/** @type {boolean} */
	isMain, ) => {
	if (request === "vscode") {
		return {
			workspace: {
				getConfiguration: () => ({
					get: ( /** @type {string} */ key, /** @type {unknown} */ fallback) => configuration.has(key) ? configuration.get(key) : fallback,
					update: async ( /** @type {string} */ key, /** @type {unknown} */ value) => {
						updates.push([key, value]);
					},
				}),
			},
			window: {
				showErrorMessage: ( /** @type {string} */ message) => errors.push(message),
				showInformationMessage: ( /** @type {string} */ message) => information.push(message),
				showInputBox: async () => inputs.shift(),
				showQuickPick: async () => picks.shift(),
			},
			env: {
				openExternal: async ( /** @type {{toString(): string}} */ uri) => {
					opened.push(uri.toString());
					return true;
				},
				clipboard: {
					writeText: async ( /** @type {string} */ value) => {
						copied.push(value);
					}
				},
			},
			Uri: {
				parse: ( /** @type {string} */ value) => ({
					toString: () => value
				})
			},
			ConfigurationTarget: {
				Global: 1
			},
		};
	}
	return original.call(loader, request, parent, isMain);
};
const {
	ExtensionController
} = require("../../../../plugins/devin_remote_control/src/extension/extension.js");
loader._load = original;
test("configuration normalizes extension defaults", () => {
	const config = ExtensionController.configuration();
	assert.equal(config.autoStart, false);
	assert.equal(config.port, 0);
	assert.equal(config.ngrokAutoStart, false);
	assert.equal(config.ngrokCommand, "ngrok");
	assert.equal(config.sessionHours, 12);
});
test("controller status and state cover runtime states", async () => {
	/** @type {InstanceType<typeof ExtensionController>} */
	const controller = Object.create(ExtensionController.prototype);
	controller.status = /** @type {import("vscode").StatusBarItem} */ ( /** @type {unknown} */ ({}));
	controller.pairing = new Map([
		["project", {
			code: "123456"
		}]
	]);
	controller.context = /** @type {import("vscode").ExtensionContext} */ ( /** @type {unknown} */ ({
		secrets: {
			get: async ( /** @type {string} */ key) => key.includes("Token") ? "secret" : "",
		},
	}));
	/** @type {{state: string, publicUrl: string, error: string}} */
	let charter = {
		state: "stopped",
		publicUrl: "",
		error: ""
	};
	controller.ngrok = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/ngrok-manager.js").NgrokManager>} */ ( /** @type {unknown} */ ({
		snapshot: () => charter,
	}));
	/** @type {{running: boolean, port: number, localUrl: string}} */
	let gateway = {
		running: false,
		port: 0,
		localUrl: ""
	};
	controller.gateway = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/gateway-server.js").GatewayServer>} */ ( /** @type {unknown} */ ({
		snapshot: () => gateway,
		agentCache: [{
			status: "running",
			acu_usage: 2
		}, {
			state: "idle",
			acu_used: 3
		}, {
			state: "done",
			consumed_acu: 4
		}, ],
	}));
	controller.registry = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/project-registry.js").ProjectRegistry>} */ ( /** @type {unknown} */ ({
		active: () => ({
			id: "project"
		}),
		publicList: () => [{
			id: "project",
			name: "Project"
		}],
	}));
	controller.security = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/security.js").SecurityService>} */ ( /** @type {unknown} */ ({
		listProjectSessions: () => [{
			id: "device"
		}],
	}));
	controller.updateStatus();
	assert.equal(controller.status.text, "$(circle-slash) Devin Remote");
	gateway = {
		running: true,
		port: 3000,
		localUrl: "http://127.0.0.1:3000"
	};
	controller.updateStatus();
	assert.equal(controller.status.text, "$(sync~spin) Devin Remote");
	charter = {
		state: "online",
		publicUrl: "https://example.test",
		error: ""
	};
	controller.updateStatus();
	assert.equal(controller.status.text, "$(remote) Devin Remote");
	assert.equal(controller.projectUrl("project"), "https://example.test/p/project/");
	charter = {
		state: "error",
		publicUrl: "",
		error: "charter failed"
	};
	controller.updateStatus();
	assert.equal(controller.status.text, "$(warning) Devin Remote");
	controller.updateStatus("explicit failure");
	assert.equal(controller.status.tooltip, "explicit failure");
	const state = await controller.state();
	assert.equal(Reflect.get(state, "activeProjectId"), "project");
	assert.deepEqual(Reflect.get(state, "metrics"), {
		agents: 3,
		activeAgents: 1,
		acu: 9,
		pairedDevices: 1,
	});
	const projects = /** @type {Array<{pairing: {code: string} | null}>} */ (Reflect.get(state, "projects"));
	const project = projects.at(0);
	assert.ok(project);
	assert.equal(project.pairing?.code, "123456");
});
test("controller postState handles absent and active panels", async () => {
	/** @type {InstanceType<typeof ExtensionController>} */
	const controller = Object.create(ExtensionController.prototype);
	controller.panel = null;
	await controller.postState();
	let message = null;
	controller.state = async () => ({
		ready: true
	});
	controller.panel = /** @type {import("vscode").WebviewPanel} */ ( /** @type {unknown} */ ({
		webview: {
			postMessage: async ( /** @type {unknown} */ value) => {
				message = value;
				return true;
			},
		},
	}));
	await controller.postState();
	assert.deepEqual(message, {
		type: "state",
		state: {
			ready: true
		}
	});
});
test("controller actions cover gateway and panel dispatch", async () => {
	/** @type {InstanceType<typeof ExtensionController>} */
	const controller = Object.create(ExtensionController.prototype);
	/** @type {{state: string, publicUrl: string, error: string}} */
	let charter = {
		state: "online",
		publicUrl: "https://example.test",
		error: ""
	};
	/** @type {{running: boolean, port: number, localUrl: string}} */
	let gateway = {
		running: true,
		port: 4321,
		localUrl: "http://127.0.0.1:4321"
	};
	let paired = 0;
	let revoked = 0;
	let stopped = 0;
	let started = 0;
	let posted = 0;
	controller.registry = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/project-registry.js").ProjectRegistry>} */ ( /** @type {unknown} */ ({
		active: () => ({
			id: "project"
		}),
		get: ( /** @type {string} */ id) => id === "project" ? {
			id
		} : null,
	}));
	controller.security = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/security.js").SecurityService>} */ ( /** @type {unknown} */ ({
		createPairing: () => {
			paired += 1;
			return {
				code: "123456"
			};
		},
		revokeProject: () => {
			revoked += 1;
		},
	}));
	controller.ngrok = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/ngrok-manager.js").NgrokManager>} */ ( /** @type {unknown} */ ({
		snapshot: () => charter,
		stop: () => {
			stopped += 1;
			charter = {
				state: "stopped",
				publicUrl: "",
				error: ""
			};
		},
		start: async () => {
			started += 1;
			charter = {
				state: "online",
				publicUrl: "https://example.test",
				error: ""
			};
		},
	}));
	controller.gateway = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/gateway-server.js").GatewayServer>} */ ( /** @type {unknown} */ ({
		snapshot: () => gateway,
		start: async () => {
			started += 1;
			gateway = {
				running: true,
				port: 4321,
				localUrl: "http://127.0.0.1:4321"
			};
			return 4321;
		},
		stop: async () => {
			stopped += 1;
			gateway = {
				running: false,
				port: 0,
				localUrl: ""
			};
		},
	}));
	controller.pairing = new Map();
	controller.postState = async () => {
		posted += 1;
	};
	controller.updateStatus = () => {};
	controller.startGateway = async () => {
		started += 1;
	};
	controller.stopGateway = async () => {
		stopped += 1;
	};
	await controller.handlePanelMessage({
		action: "pair",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "open",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "copy",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "revoke",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "toggle-charter",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "toggle-charter",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "toggle-gateway",
		projectId: "project"
	});
	gateway = {
		running: false,
		port: 0,
		localUrl: ""
	};
	await controller.handlePanelMessage({
		action: "toggle-gateway",
		projectId: "project"
	});
	await controller.handlePanelMessage({
		action: "unknown",
		projectId: "project"
	});
	assert.equal(paired, 1);
	assert.equal(revoked, 1);
	assert.ok(stopped >= 2);
	assert.ok(started >= 2);
	assert.ok(posted >= 8);
	assert.equal(opened.at(-1), "https://example.test/p/project/");
	assert.equal(copied.at(-1), "https://example.test/p/project/");
	assert.equal(information.at(-1), "Project URL copied.");
	assert.match(errors.at(-1) ?? "", /Unknown panel action/u);
});
test("controller lifecycle and credential flows", async () => {
	/** @type {InstanceType<typeof ExtensionController>} */
	const controller = Object.create(ExtensionController.prototype);
	let gatewayStarts = 0;
	let gatewayStops = 0;
	let tunnelStarts = 0;
	let tunnelStops = 0;
	let posts = 0;
	let refreshes = 0;
	/** @type {Map<string, string>} */
	const secrets = new Map();
	controller.context = /** @type {import("vscode").ExtensionContext} */ ( /** @type {unknown} */ ({
		secrets: {
			get: async ( /** @type {string} */ key) => secrets.get(key),
			store: async ( /** @type {string} */ key, /** @type {string} */ value) => {
				secrets.set(key, value);
			},
		},
	}));
	controller.gateway = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/gateway-server.js").GatewayServer>} */ ( /** @type {unknown} */ ({
		start: async () => {
			gatewayStarts += 1;
			return 4321;
		},
		stop: async () => {
			gatewayStops += 1;
		},
		snapshot: () => ({
			running: true,
			port: 4321,
			localUrl: "http://127.0.0.1:4321"
		}),
		refreshAgents: () => {
			refreshes += 1;
		},
	}));
	controller.ngrok = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/ngrok-manager.js").NgrokManager>} */ ( /** @type {unknown} */ ({
		start: async () => {
			tunnelStarts += 1;
		},
		stop: () => {
			tunnelStops += 1;
		},
		dispose: () => {
			tunnelStops += 1;
		},
		snapshot: () => ({
			state: "stopped",
			publicUrl: "",
			error: ""
		}),
	}));
	controller.status = /** @type {import("vscode").StatusBarItem} */ ( /** @type {unknown} */ ({}));
	controller.postState = async () => {
		posts += 1;
	};
	controller.providers = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/provider-service.js").ProviderService>} */ ( /** @type {unknown} */ ({
		configuration: {
			openAIBaseUrl: "",
			openAIModel: "",
			anthropicModel: ""
		},
	}));
	controller.devin = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/devin-client.js").DevinClient>} */ ( /** @type {unknown} */ ({
		orgId: ""
	}));
	await controller.startGateway();
	await controller.stopGateway();
	await controller.dispose();
	assert.equal(gatewayStarts, 1);
	assert.equal(gatewayStops, 2);
	assert.ok(tunnelStops >= 2);
	assert.ok(posts >= 2);
	assert.equal(refreshes, 0);
	assert.equal(tunnelStarts, 0);
});
test("credential methods persist provider settings", async () => {
	/** @type {InstanceType<typeof ExtensionController>} */
	const controller = Object.create(ExtensionController.prototype);
	/** @type {Map<string, string>} */
	const secrets = new Map();
	let refreshes = 0;
	let posts = 0;
	controller.context = /** @type {import("vscode").ExtensionContext} */ ( /** @type {unknown} */ ({
		secrets: {
			store: async ( /** @type {string} */ key, /** @type {string} */ value) => {
				secrets.set(key, value);
			},
		},
	}));
	controller.devin = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/devin-client.js").DevinClient>} */ ( /** @type {unknown} */ ({
		orgId: ""
	}));
	controller.gateway = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/gateway-server.js").GatewayServer>} */ ( /** @type {unknown} */ ({
		refreshAgents: () => {
			refreshes += 1;
		},
		snapshot: () => ({
			running: false,
			port: 0,
			localUrl: ""
		}),
	}));
	controller.providers = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/provider-service.js").ProviderService>} */ ( /** @type {unknown} */ ({
		configuration: {
			openAIBaseUrl: "",
			openAIModel: "",
			anthropicModel: ""
		},
	}));
	controller.ngrok = /** @type {InstanceType<typeof import("../../../../plugins/devin_remote_control/src/extension/lib/ngrok-manager.js").NgrokManager>} */ ( /** @type {unknown} */ ({
		stop: () => {},
		start: async () => {}
	}));
	controller.postState = async () => {
		posts += 1;
	};
	inputs.push(" org-1 ", " token ");
	await controller.setDevinCredentials();
	inputs.push(" gpt-test ", " https://api.test/v1 ", " key-openai ");
	await controller.setProviderCredentials("openai-compatible");
	inputs.push(" claude-test ", " key-anthropic ");
	await controller.setProviderCredentials("anthropic");
	inputs.push(" ngrok-token ");
	await controller.setNgrokToken();
	assert.equal(controller.devin.orgId, "org-1");
	assert.equal(controller.providers.configuration.openAIModel, "gpt-test");
	assert.equal(controller.providers.configuration.anthropicModel, "claude-test");
	assert.equal(secrets.get("devinRemote.devinToken"), "token");
	assert.equal(secrets.get("devinRemote.provider.openai-compatible"), "key-openai");
	assert.equal(secrets.get("devinRemote.provider.anthropic"), "key-anthropic");
	assert.equal(secrets.get("devinRemote.ngrokToken"), "ngrok-token");
	assert.equal(refreshes, 1);
	assert.ok(posts >= 4);
	assert.ok(updates.length >= 4);
});
