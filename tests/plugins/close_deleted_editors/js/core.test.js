"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
	DeletedEditorPolicy
} = require("../../../../plugins/close_deleted_editors/src/core.js");
test("classifies paths", () => {
	const exists = /** @type {(path: string) => boolean} */ (p => p === "a");
	assert.deepEqual(DeletedEditorPolicy.existing(["a", "b"], exists), ["a"]);
	assert.deepEqual(DeletedEditorPolicy.deleted(["a", "b"], exists), ["b"]);
});
test("extracts URI path", () => {
	assert.equal(DeletedEditorPolicy.uriPath( /** @type {import("vscode").Tab} */ ( /** @type {unknown} */ ({
		input: {
			uri: {
				fsPath: "/a"
			}
		}
	}))), "/a");
	assert.equal(DeletedEditorPolicy.uriPath( /** @type {import("vscode").Tab} */ ( /** @type {unknown} */ ({}))), null);
});
