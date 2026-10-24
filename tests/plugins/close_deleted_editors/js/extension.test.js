"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
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
	DeletedEditors
} = require("../../../../plugins/close_deleted_editors/src/extension.js");
loader._load = original;
test("closes only deleted tabs", async () => {
	/** @type {unknown[][]} */
	const closed = [];
	const api = /** @type {typeof import("vscode")} */ ( /** @type {unknown} */ ({
		window: {
			tabGroups: {
				all: [{
					tabs: [{
						input: {
							uri: {
								fsPath: "/gone.txt"
							}
						}
					}, {
						input: {
							uri: {
								fsPath: __filename
							}
						}
					}, {}, ]
				}],
				close: async ( /** @type {unknown[]} */ ...args) => {
					closed.push(args);
					return true;
				},
			},
		},
	}));
	assert.equal(await new DeletedEditors(api).close(), 1);
	assert.equal(closed.length, 1);
	assert.equal(closed[0]?.[1], true);
});
