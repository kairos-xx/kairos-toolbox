"use strict";
const vscode = require("vscode");
const {FileTree} = require("./file-tree.js");
/** Implements the Explorer commands contributed by the extension. */
class ExplorerOpener {
    /**
     * Creates an Explorer opener.
     *
     * @param {typeof vscode} api VS Code API.
     */
    constructor(api) {
        this.api = api;
        this.fileTree = new FileTree(api.workspace.fs.readDirectory, api.FileType.Directory);
    }
    /**
     * Opens every selected file in the editor group that is currently active.
     * Directories are expanded recursively.
     *
     * @param {vscode.Uri | undefined} resource Resource under the context menu.
     * @param {vscode.Uri[] | undefined} selectedResources Explorer selection.
     * @returns {Promise<void>} Completion promise.
     */
    async open(resource, selectedResources) {
        const resources = ExplorerOpener.selection(resource, selectedResources);
        if (resources.length === 0)
            return;
        const targetColumn = this.api.window.activeTextEditor?.viewColumn
            ?? this.api.ViewColumn.Active;
        await this.api.window.withProgress(
            {
                location: this.api.ProgressLocation.Notification,
                title: "Opening selected Explorer files",
                cancellable: true,
            },
            async (_progress, token) => {
                const files = await this.fileTree.resolve(resources);
                await files.reduce(async (previous, file) => {
                    await previous;
                    if (!token.isCancellationRequested)
                        await this.#openFile(file, targetColumn);
                }, Promise.resolve());
            },
        );
    }
    /**
     * Opens one file as a pinned tab in the target editor group.
     *
     * @param {vscode.Uri} resource File to open.
     * @param {vscode.ViewColumn} targetColumn Destination editor group.
     * @returns {Promise<void>} Completion promise.
     */
    #openFile = async (resource, targetColumn) => {
        try {
            await this.api.window.showTextDocument(
                await this.api.workspace.openTextDocument(resource),
                {viewColumn: targetColumn, preview: false, preserveFocus: false},
            );
        } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            await this.api.window.showWarningMessage(
                `Could not open ${resource.fsPath}: ${detail}`,
            );
        }
    };
    /**
     * Normalizes the arguments supplied by the Explorer context menu.
     *
     * @param {vscode.Uri | undefined} resource Resource under the context menu.
     * @param {vscode.Uri[] | undefined} selectedResources Explorer selection.
     * @returns {vscode.Uri[]} Resources to open.
     */
    static selection = (resource, selectedResources) =>
        selectedResources?.length
            ? selectedResources
            : resource === undefined
                ? []
                : [resource];
}
/**
 * Activates the extension.
 *
 * @param {vscode.ExtensionContext} context VS Code extension context.
 * @returns {void}
 */
const activate = context => {
    const opener = new ExplorerOpener(vscode);
    context.subscriptions.push(
        vscode.commands.registerCommand("openSelectedEditorPanel.open", opener.open),
        vscode.commands.registerCommand("openSelectedEditorPanel.openAll", opener.open),
    );
};
/** Deactivates the extension. */
const deactivate = () => {};
module.exports = {activate, deactivate, ExplorerOpener};
