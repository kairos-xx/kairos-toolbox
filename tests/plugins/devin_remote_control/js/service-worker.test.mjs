import assert from "node:assert/strict";
import {
	createRequire
} from "node:module";
import test from "node:test";
const require = createRequire(import.meta.url);
test("service worker covers lifecycle and fetch policies", async () => {
	/** @type {Record<string, (event: Event) => void>} */
	const listeners = {};
	globalThis.self = /** @type {Window & typeof globalThis} */ ( /** @type {unknown} */ ({
		addEventListener: (
			/** @type {string} */
			name,
			/** @type {(event: Event) => void} */
			handler, ) => {
			listeners[name] = handler;
		},
		skipWaiting: async () => {},
		clients: {
			claim: async () => {}
		},
	}));
	globalThis.caches = /** @type {CacheStorage} */ ( /** @type {unknown} */ ({
		open: async () => ({
			addAll: async () => {},
			put: async () => {}
		}),
		keys: async () => ["old"],
		delete: async () => true,
		match: async () => undefined,
	}));
	globalThis.fetch = /** @type {typeof fetch} */ (async () =>
		/** @type {Response} */
		( /** @type {unknown} */ ({
			ok: false,
			type: "basic",
			clone() {
				return this;
			},
		})));
	require("../../../../plugins/devin_remote_control/src/media/web/js/service-worker.js");
	const fetchHandler = Reflect.get(listeners, "fetch");
	if (fetchHandler === undefined) throw new Error("Fetch handler missing.");
	let responded = false;
	fetchHandler( /** @type {Event} */ ( /** @type {unknown} */ ({
		request: {
			method: "POST",
			url: "https://x.test/"
		},
		respondWith: () => {
			responded = true;
		},
	})));
	assert.equal(responded, false);
	fetchHandler( /** @type {Event} */ ( /** @type {unknown} */ ({
		request: {
			method: "GET",
			url: "https://x.test/api/x"
		},
		respondWith: () => {
			responded = true;
		},
	})));
	assert.equal(responded, false);
	const installHandler = Reflect.get(listeners, "install");
	const activateHandler = Reflect.get(listeners, "activate");
	if (installHandler === undefined || activateHandler === undefined) throw new Error("Lifecycle handlers missing.");
	/** @type {Promise<unknown> | null} */
	let lifecycle = null;
	installHandler( /** @type {Event} */ ( /** @type {unknown} */ ({
		waitUntil: ( /** @type {Promise<unknown>} */ promise) => {
			lifecycle = promise;
		},
	})));
	await lifecycle;
	activateHandler( /** @type {Event} */ ( /** @type {unknown} */ ({
		waitUntil: ( /** @type {Promise<unknown>} */ promise) => {
			lifecycle = promise;
		},
	})));
	await lifecycle;
	/** @type {Promise<Response> | null} */
	let response = null;
	fetchHandler( /** @type {Event} */ ( /** @type {unknown} */ ({
		request: {
			method: "GET",
			url: "https://x.test/index.html"
		},
		respondWith: ( /** @type {Promise<Response>} */ promise) => {
			response = promise;
		},
	})));
	if (response === null) throw new Error("Fetch response missing.");
	await response;
});
