"use strict";
const fs = require("fs");
/** Classifies editor tabs by whether their files still exist on disk. */
class DeletedEditorPolicy {
  /**
   * Returns the paths that still exist.
   *
   * @param {string[]} paths File paths.
   * @param {(path: string) => boolean} exists Existence probe.
   * @returns {string[]} Existing paths.
   */
  static existing = (paths, exists = fs.existsSync) => paths.filter(path => exists(path));
  /**
   * Returns the paths that no longer exist.
   *
   * @param {string[]} paths File paths.
   * @param {(path: string) => boolean} exists Existence probe.
   * @returns {string[]} Deleted paths.
   */
  static deleted = (paths, exists = fs.existsSync) => paths.filter(path => !exists(path));
  /**
   * Extracts a file path from a tab's input URI.
   *
   * @param {import("vscode").Tab} tab Editor tab.
   * @returns {string | null} File path, or null when the tab has none.
   */
  static uriPath = tab =>
    (/** @type {{ uri?: { fsPath?: string } } | undefined} */ (tab?.input))?.uri?.fsPath ?? null;
}
module.exports = {DeletedEditorPolicy};
