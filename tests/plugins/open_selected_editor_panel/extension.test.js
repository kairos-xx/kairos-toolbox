"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
/** @typedef {typeof Module & {_load(request: string, parent: NodeModule | null | undefined, isMain: boolean): unknown}} ModuleLoader */
const loader = /** @type {ModuleLoader} */ (/** @type {unknown} */ (Module));
const original = loader._load;
/** @type {Array<(...args: unknown[]) => unknown>} */
const handlers = [];
loader._load = (
  /** @type {string} */ request,
  /** @type {NodeModule | null | undefined} */ parent,
  /** @type {boolean} */ isMain,
) => {
  if (request === "vscode") {
    return {
      commands: {
        registerCommand: (
          /** @type {string} */ _name,
          /** @type {(...args: unknown[]) => unknown} */ handler,
        ) => {
          handlers.push(handler);
          return {dispose: () => {}};
        },
      },
      workspace: {fs: {readDirectory: async () => []}},
      FileType: {Directory: 2},
    };
  }
  return original.call(loader, request, parent, isMain);
};
const extension = require("../../../plugins/open_selected_editor_panel/src/extension.js");
loader._load = original;
test("activation registers both explorer commands", () => {
  /** @type {import("vscode").Disposable[]} */
  const subscriptions = [];
  const context = /** @type {import("vscode").ExtensionContext} */ (
    /** @type {unknown} */ ({subscriptions})
  );
  extension.activate(context);
  assert.equal(subscriptions.length, 2);
  assert.equal(handlers.length, 2);
});
