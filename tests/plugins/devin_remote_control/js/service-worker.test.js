"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
test("service worker filters non-cacheable fetch requests", () => {
	/** @type {Record<string, (event: Event) => void>} */
	const listeners = {};
	const context = /** @type {vm.Context} */ ({
		URL,
		Promise,
		self: {
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
		},
		caches: {
			open: async () => ({
				addAll: async () => {},
				put: async () => {}
			}),
			keys: async () => ["old"],
			delete: async () => true,
			match: async () => undefined,
		},
		fetch: async () => ({
			ok: false,
			type: "basic",
			clone() {
				return this;
			},
		}),
	});
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../../../plugins/devin_remote_control/src/media/web/js/service-worker.js"), "utf8", ), context, );
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
});
