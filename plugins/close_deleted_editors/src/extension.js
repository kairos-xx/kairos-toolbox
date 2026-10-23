"use strict";
const vscode = require("vscode");
const {DeletedEditorPolicy} = require("./core.js");
/** Closes editor tabs whose underlying files were deleted on disk. */
class DeletedEditors {
  /**
   * Creates the service against the VS Code API surface.
   *
   * @param {typeof import("vscode")} api VS Code API.
   */
  constructor(api) { this.api = api; this.close = this.close.bind(this); }
  /**
   * Closes every open tab whose file no longer exists.
   *
   * @returns {Promise<number>} Number of tabs closed.
   */
  async close() {
    const tabs = this.api.window.tabGroups.all.flatMap(group => group.tabs);
    const deleted = tabs.filter(tab => { const path = DeletedEditorPolicy.uriPath(tab); return path !== null && DeletedEditorPolicy.deleted([path]).length === 1; });
    if (deleted.length) await this.api.window.tabGroups.close(deleted, true);
    return deleted.length;
  }
}
/**
 * Activates the extension.
 *
 * @param {import("vscode").ExtensionContext} context Extension context.
 */
const activate = context => { const service = new DeletedEditors(vscode); context.subscriptions.push(vscode.commands.registerCommand("closeDeletedEditors.close", service.close), vscode.workspace.onDidDeleteFiles(service.close)); };
/** Deactivates the extension. */
const deactivate = () => {};
module.exports = {activate,deactivate,DeletedEditors};
