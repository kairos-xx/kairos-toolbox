"use strict";
const crypto = require("node:crypto");
const { EventEmitter } = require("node:events");
const { GitService } = require("./git-service");
const { HttpError } = require("./errors");
/**
 * @typedef {object} Project
 * @property {string} id Stable twelve-character project identifier.
 * @property {string} name Display name.
 * @property {string} root Workspace folder path.
 * @property {import("vscode").Uri} uri Workspace folder URI.
 * @property {string} gitRemote Upstream remote URL.
 */
/** Tracks known projects and the active selection. */
class ProjectRegistry extends EventEmitter {
  /**
   * Creates an instance of ProjectRegistry.
   * @param {typeof import("vscode")} vscode The VS Code API surface.
   */
  constructor(vscode) {
    super();
    /** @type {typeof import("vscode")} */
    this.vscode = vscode;
    /** @type {Map<string, Project>} */
    this.projects = new Map();
    /** @type {import("vscode").Disposable[]} */
    this.disposables = [];
    this.refresh();
    this.disposables.push(
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this.refresh();
        this.emit("projects");
      }),
      vscode.window.onDidChangeActiveTextEditor(() => this.emit("active")),
      vscode.languages.onDidChangeDiagnostics(event => {
        const projectIds = new Set(event.uris.map(uri => {
          const folder = vscode.workspace.getWorkspaceFolder(uri);
          return folder ? ProjectRegistry.idFor(folder.uri) : "";
        }).filter(Boolean));
        projectIds.forEach(projectId => { this.emit("diagnostics", projectId); });
      })
    );
  }
  /** @param {import("vscode").Uri} uri Uri. */
  static idFor = uri => crypto.createHash("sha256").update(uri.toString()).digest("hex").slice(0, 12);
  /**
   * Refreshes the project set from open workspace folders.
   * @returns {void} Nothing.
   */
  refresh() {
    const folders = this.vscode.workspace.workspaceFolders ?? [];
    const next = new Map(
      folders.map(folder => {
        const id = ProjectRegistry.idFor(folder.uri);
        const existing = this.projects.get(id);
        return /** @type {[string, Project]} */ ([id, existing ?? {
          id,
          name: folder.name,
          root: folder.uri.fsPath,
          uri: folder.uri,
          gitRemote: ""
        }]);
      })
    );
    this.projects = next;
    Promise.all([...next.values()].map(async project => {
      const gitRemote = await new GitService(project).remote();
      Object.assign(project, { gitRemote });
    }));
  }
  /**
   * All.
   * @returns {Project[]} Every known project.
   */
  all() {
    return [...this.projects.values()];
  }
  /**
   * Public list.
   * @returns {{id: string, name: string, gitRemote: string}[]} Public project descriptors.
   */
  publicList() {
    return this.all().map(({ id, name, gitRemote }) => ({ id, name, gitRemote }));
  }
  /**
   * Returns.
   * @param {string} projectId ProjectId.
   * @returns {Project} The matching project.
   * @throws {HttpError} When the operation cannot complete.
   */
  get(projectId) {
    const project = this.projects.get(projectId);
    if (!project) throw new HttpError(404, "Project is no longer open in VS Code.");
    return project;
  }
  /**
   * Active.
   * @returns {Project | null} The project owning the active editor.
   */
  active() {
    const editor = this.vscode.window.activeTextEditor;
    const folder = editor ? this.vscode.workspace.getWorkspaceFolder(editor.document.uri) : null;
    return folder ? this.projects.get(ProjectRegistry.idFor(folder.uri)) ?? null : this.all()[0] ?? null;
  }
  /**
   * Diagnostics.
   * @param {string} projectId ProjectId.
   * @returns {{path: string, severity: string, message: string, source: string, code: string, line: number, column: number, endLine: number, endColumn: number}[]} Diagnostics for the project.
   */
  diagnostics(projectId) {
    const project = this.get(projectId);
    const severity = new Map([
      [this.vscode.DiagnosticSeverity.Error, "error"],
      [this.vscode.DiagnosticSeverity.Warning, "warning"],
      [this.vscode.DiagnosticSeverity.Information, "information"],
      [this.vscode.DiagnosticSeverity.Hint, "hint"]
    ]);
    return this.vscode.languages.getDiagnostics().flatMap(([uri, diagnostics]) => {
      const folder = this.vscode.workspace.getWorkspaceFolder(uri);
      if (!folder || ProjectRegistry.idFor(folder.uri) !== project.id) return [];
      const relative = this.vscode.workspace.asRelativePath(uri, false);
      return diagnostics.map(diagnostic => ({
        path: relative,
        severity: severity.get(diagnostic.severity) ?? "information",
        message: diagnostic.message,
        source: diagnostic.source ?? "",
        code: typeof diagnostic.code === "object"
          ? String(diagnostic.code?.value ?? "")
          : String(diagnostic.code ?? ""),
        line: diagnostic.range.start.line + 1,
        column: diagnostic.range.start.character + 1,
        endLine: diagnostic.range.end.line + 1,
        endColumn: diagnostic.range.end.character + 1
      }));
    }).sort((left, right) => {
      const rank = /** @type {Record<string, number>} */ ({
        error: 0, warning: 1, information: 2, hint: 3
      });
      return (rank[left.severity] ?? 4) - (rank[right.severity] ?? 4)
        || left.path.localeCompare(right.path)
        || left.line - right.line;
    });
  }
  /**
   * Opens location.
   * @param {string} projectId ProjectId.
   * @param {string} relativePath RelativePath.
   * @param {number} line Line.
   * @param {number} column Column.
   * @returns {Promise<{ok: boolean, path: string, line: number, column: number}>} Navigation result.
   * @throws {HttpError} When the operation cannot complete.
   */
  async openLocation(projectId, relativePath, line = 1, column = 1) {
    const project = this.get(projectId);
    const normalized = relativePath.replaceAll("\\", "/").replace(/^\/+/, "");
    const root = this.vscode.Uri.file(project.root);
    const uri = this.vscode.Uri.joinPath(root, ...normalized.split("/").filter(Boolean));
    const folder = this.vscode.workspace.getWorkspaceFolder(uri);
    if (!folder || ProjectRegistry.idFor(folder.uri) !== project.id)
      throw new HttpError(403, "Path is outside the project workspace.");
    const document = await this.vscode.workspace.openTextDocument(uri);
    const editor = await this.vscode.window.showTextDocument(document, { preview: false });
    const position = new this.vscode.Position(
      Math.max(0, Number(line) - 1),
      Math.max(0, Number(column) - 1)
    );
    editor.selection = new this.vscode.Selection(position, position);
    editor.revealRange(new this.vscode.Range(position, position));
    return { ok: true, path: normalized, line: position.line + 1, column: position.character + 1 };
  }
  /** Disposes the registry and its VS Code subscriptions. */
  dispose() {
    this.disposables.forEach(disposable => { disposable.dispose(); });
    this.disposables = [];
    this.removeAllListeners();
  }
}
module.exports = { ProjectRegistry };
