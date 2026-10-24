"use strict";
const assert = require("node:assert/strict");
const {
	describe,
	it
} = require("node:test");
const {
	FileTree
} = require("../../../../plugins/open_selected_editor_panel/src/file-tree.js");
const DIRECTORY = 2;
const FILE = 1;
/** Creates the minimal URI surface required by FileTree. */
class FakeUri {
	/** @param {string} path URI path. */
	constructor(path) {
		this.path = path;
		this.fsPath = path;
		/** Returns the stable URI identity. @returns {string} Stable URI identity. */
		this.toString = () => "file://".concat(this.path);
		/**
		 * Returns a URI with the path replaced.
		 * @param {{path?: string}} change Replacement path.
		 * @returns {FakeUri} Updated URI.
		 */
		this.with = change => new FakeUri(change.path ?? this.path);
	}
}
describe("FileTree", () => {
	it("recursively expands folders, sorts files, and removes duplicates", async () => {
		/** @type {Map<string, [string, number][]>} */
		const entries = new Map([
			["/root", [
				["b.txt", FILE],
				["nested", DIRECTORY]
			]],
			["/root/nested", [
				["a.txt", FILE]
			]],
		]);
		const tree = new FileTree(async uri => {
			const value = entries.get(uri.path);
			if (value === undefined) throw new Error("ENOTDIR");
			return value;
		}, DIRECTORY);
		const files = await tree.resolve([
			/** @type {import("vscode").Uri} */
			( /** @type {unknown} */ (new FakeUri("/root"))),
			/** @type {import("vscode").Uri} */
			( /** @type {unknown} */ (new FakeUri("/root/nested/a.txt"))),
		]);
		assert.deepEqual(files.map(file => file.path), ["/root/b.txt", "/root/nested/a.txt", ]);
	});
	it("propagates directory enumeration failures", async () => {
		const tree = new FileTree(async () => {
			throw new Error("permission denied");
		}, DIRECTORY);
		await assert.rejects(tree.resolve([ /** @type {import("vscode").Uri} */ ( /** @type {unknown} */ (new FakeUri("/root")))]), /permission denied/u);
	});
});
