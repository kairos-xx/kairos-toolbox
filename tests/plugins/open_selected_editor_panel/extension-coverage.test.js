"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
/** @typedef {typeof Module & {_load(request: string, parent: NodeModule | null | undefined, isMain: boolean): unknown}} ModuleLoader */
const loader = /** @type {ModuleLoader} */ (/** @type {unknown} */ (Module));
const original = loader._load;
/** @type {string[]} */
const warnings = [];
/** @type {Array<{resource: unknown, options: unknown}>} */
const shown = [];
/** @type {number | null} */
let viewColumn = null;
/** @type {boolean} */
let cancelRequested = false;
const api = {
  commands: {registerCommand: () => ({dispose: () => {}})},
  workspace: {
    fs: {readDirectory: async () => []},
    openTextDocument: async (/** @type {unknown} */ resource) => ({uri: resource}),
  },
  window: {
    get activeTextEditor() { return viewColumn === null ? undefined : {viewColumn}; },
    withProgress: async (
      /** @type {unknown} */ _options,
      /** @type {(progress: unknown, token: {isCancellationRequested: boolean}) => Promise<unknown>} */ task,
    ) => task({}, {isCancellationRequested: cancelRequested}),
    showTextDocument: async (
      /** @type {unknown} */ document,
      /** @type {unknown} */ options,
    ) => { shown.push({resource: document, options}); return {}; },
    showWarningMessage: async (/** @type {string} */ message) => { warnings.push(message); },
  },
  ViewColumn: {Active: -1},
  ProgressLocation: {Notification: 15},
  FileType: {Directory: 2},
};
/** Structural vscode substitute cast to the real module type. */
const vscodeApi = /** @type {typeof import("vscode")} */ (
  /** @type {unknown} */ (api)
);
/**
 * Wraps a path in the minimal Uri shape the opener reads.
 * @param {string} fsPath File system path.
 * @returns {import("vscode").Uri} Fake Uri.
 */
const fakeUri = fsPath => /** @type {import("vscode").Uri} */ (
  /** @type {unknown} */ ({fsPath})
);
/** Deterministic stand-in for the FileTree resolver. */
class FakeFileTree {
  /**
   * Creates a fake tree.
   * @param {(uri: unknown) => Promise<unknown>} readDirectory Read-directory function.
   * @param {number} directoryType Directory marker.
   */
  constructor(readDirectory, directoryType) {
    this.readDirectory = readDirectory;
    this.directoryType = directoryType;
  }
  /**
   * Resolves resources into files via the retained directory reader.
   * @param {Array<{fsPath: string}>} resources Selected resources.
   * @returns {Promise<Array<{fsPath: string}>>} Resolved files.
   */
  async resolve(resources) {
    const groups = await Promise.all(resources.map(async resource => {
      if (resource.fsPath.endsWith(".dir") && this.directoryType === api.FileType.Directory) {
        const entries = await this.readDirectory(resource);
        return Array.isArray(entries) && entries.length > 0
          ? entries.map((/** @type {unknown} */ entry) => (
            {fsPath: `${resource.fsPath}/${String(entry)}`}
          ))
          : [{fsPath: `${resource.fsPath}/inner.txt`}];
      }
      return [resource];
    }));
    return groups.flat();
  }
}
loader._load = (
  /** @type {string} */ request,
  /** @type {NodeModule | null | undefined} */ parent,
  /** @type {boolean} */ isMain,
) => {
  if (request === "vscode") return api;
  if (request.endsWith("file-tree.js")) return {FileTree: FakeFileTree};
  return original.call(loader, request, parent, isMain);
};
const {ExplorerOpener, deactivate} = require("../../../plugins/open_selected_editor_panel/src/extension.js");
loader._load = original;
const {ExplorerOpener: ExportedOpener} = require("../../../plugins/open_selected_editor_panel/src/extension.js");
assert.equal(ExportedOpener, ExplorerOpener);
test("selection normalizes context-menu arguments", () => {
  assert.deepEqual(ExplorerOpener.selection(undefined, undefined), []);
  const one = fakeUri("/a.txt");
  assert.deepEqual(ExplorerOpener.selection(one, undefined), [one]);
  assert.deepEqual(ExplorerOpener.selection(undefined, [one]), [one]);
  const two = fakeUri("/b.txt");
  assert.deepEqual(ExplorerOpener.selection(one, [one, two]), [one, two]);
});
test("open returns early when the selection is empty", async () => {
  warnings.length = 0;
  const opener = new ExplorerOpener(vscodeApi);
  await opener.open(undefined, undefined);
  assert.equal(shown.length, 0);
});
test("open resolves directories and opens every file", async () => {
  shown.length = 0;
  warnings.length = 0;
  cancelRequested = false;
  const opener = new ExplorerOpener(vscodeApi);
  await opener.open(undefined, [fakeUri("/f.txt"), fakeUri("/d.dir")]);
  assert.equal(shown.length, 2);
  assert.equal(warnings.length, 0);
  const last = shown.at(-1);
  assert.ok(last);
  assert.equal(
    /** @type {{viewColumn: number}} */ (last.options).viewColumn,
    api.ViewColumn.Active,
  );
});
test("open prefers the active editor column and stops on cancellation", async () => {
  shown.length = 0;
  warnings.length = 0;
  viewColumn = 3;
  cancelRequested = true;
  const opener = new ExplorerOpener(vscodeApi);
  await opener.open(undefined, [fakeUri("/a.txt"), fakeUri("/b.txt")]);
  assert.equal(shown.length, 0);
  viewColumn = null;
  cancelRequested = false;
});
test("open warns when a document cannot be shown", async () => {
  warnings.length = 0;
  shown.length = 0;
  const failing = {
    ...api,
    workspace: {
      ...api.workspace,
      openTextDocument: async () => { throw new Error("broken"); },
    },
  };
  const opener = new ExplorerOpener(
    /** @type {typeof import("vscode")} */ (/** @type {unknown} */ (failing)),
  );
  await opener.open(undefined, [fakeUri("/bad.txt")]);
  assert.equal(warnings.length, 1);
  const [warning] = warnings;
  assert.ok(warning);
  assert.match(warning, /Could not open \/bad\.txt/u);
});
test("deactivate is a no-op that completes cleanly", () => {
  assert.equal(deactivate(), undefined);
});
