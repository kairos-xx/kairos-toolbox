"use strict";
const crypto = require("node:crypto");
const os = require("node:os");
const { EventEmitter } = require("node:events");
const { spawn } = require("node:child_process");
const { Constants } = require("./constants");
const { HttpError } = require("./errors");
/**
 * @typedef {object} Project
 * @property {string} id Stable twelve-character project identifier.
 * @property {string} root Workspace folder path.
 */
/** Persistent interactive shell session. */
class ShellSession extends EventEmitter {
  /**
   * Creates an instance of ShellSession.
   * @param {Project} project Owning project.
   * @param {string} requestedShell Configured shell override.
   */
  constructor(project, requestedShell) {
    super();
    /** @type {string} */
    this.id = crypto.randomBytes(12).toString("base64url");
    /** @type {string} */
    this.projectId = project.id;
    /** @type {Project} */
    this.project = project;
    /** @type {string} */
    this.requestedShell = requestedShell;
    /** @type {string} */
    this.buffer = "";
    /** @type {boolean} */
    this.closed = false;
    /** @type {number} */
    this.createdAt = Date.now();
    /** @type {import("node:child_process").ChildProcess} */
    this.process = this.start();
    this.process.stdout?.on("data", chunk => this.push("stdout", chunk));
    this.process.stderr?.on("data", chunk => this.push("stderr", chunk));
    this.process.on("error", error => this.push("stderr", Buffer.from(`${error.message}\n`)));
    this.process.on("close", code => {
      this.closed = true;
      this.emit("terminal", { type: "exit", code: code ?? 1 });
      this.emit("close");
    });
  }
  /**
   * Shell.
   * @returns {string} The shell executable path.
   */
  shell() {
    return this.requestedShell
      || Reflect.get(process.env, "SHELL")
      || (process.platform === "win32" ? "powershell.exe" : "/bin/sh");
  }
  /**
   * Starts.
   * @returns {import("node:child_process").ChildProcess} The spawned shell process.
   */
  start() {
    const shell = this.shell();
    if (process.platform === "darwin")
      return spawn("script", ["-q", "/dev/null", shell, "-l"], {
        cwd: this.project.root,
        env: { ...process.env, TERM: "xterm-256color" },
        stdio: ["pipe", "pipe", "pipe"]
      });
    if (process.platform === "linux")
      return spawn("script", ["-qfec", `${shell} -l`, "/dev/null"], {
        cwd: this.project.root,
        env: { ...process.env, TERM: "xterm-256color" },
        stdio: ["pipe", "pipe", "pipe"]
      });
    return spawn(shell, [], {
      cwd: this.project.root,
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
  }
  /**
   * Pushes.
   * @param {string} stream Stream.
   * @param {Buffer} chunk Chunk.
   */
  push(stream, chunk) {
    const text = chunk.toString("utf8");
    this.buffer = `${this.buffer}${text}`.slice(-Constants.terminalBufferBytes);
    this.emit("terminal", { type: "output", stream, text });
  }
  /**
   * Writes.
   * @param {string} input Input.
   * @throws {HttpError} When the operation cannot complete.
   */
  write(input) {
    if (this.closed || !this.process.stdin?.writable) throw new HttpError(409, "Terminal session is closed.");
    this.process.stdin?.write(input);
  }
  /** Closes. */
  close() {
    if (this.closed) return;
    this.process.kill("SIGTERM");
    this.closed = true;
  }
  /**
   * Returns a snapshot of.
   * @returns {{id: string, projectId: string, createdAt: number, closed: boolean, buffer: string, shell: string, platform: NodeJS.Platform}} Session snapshot.
   */
  snapshot() {
    return {
      id: this.id,
      projectId: this.projectId,
      createdAt: this.createdAt,
      closed: this.closed,
      buffer: this.buffer,
      shell: this.shell(),
      platform: os.platform()
    };
  }
}
/** Creates and tracks shell sessions. */
class TerminalManager {
  /**
   * Creates an instance of TerminalManager.
   * @param {string} shell Configured shell override.
   */
  constructor(shell) {
    /** @type {string} */
    this.shell = shell;
    /** @type {Map<string, ShellSession>} */
    this.sessions = new Map();
  }
  /**
   * Creates.
   * @param {Project} project Project.
   * @returns {ShellSession} The new session.
   */
  create(project) {
    const session = new ShellSession(project, this.shell);
    this.sessions.set(session.id, session);
    session.once("close", () => this.sessions.delete(session.id));
    return session;
  }
  /**
   * Returns.
   * @param {string} projectId ProjectId.
   * @param {string} sessionId SessionId.
   * @returns {ShellSession} The matching session.
   * @throws {HttpError} When the operation cannot complete.
   */
  get(projectId, sessionId) {
    const session = this.sessions.get(sessionId);
    if (!session || session.projectId !== projectId) throw new HttpError(404, "Terminal session not found.");
    return session;
  }
  /**
   * Lists.
   * @param {string} projectId ProjectId.
   * @returns {{id: string, projectId: string, createdAt: number, closed: boolean, buffer: string, shell: string, platform: NodeJS.Platform}[]} Session snapshots.
   */
  list(projectId) {
    return [...this.sessions.values()]
      .filter(session => session.projectId === projectId)
      .map(session => session.snapshot())
      .sort((left, right) => right.createdAt - left.createdAt);
  }
  /** Closes all. */
  closeAll() {
    this.sessions.forEach(session => { session.close(); });
    this.sessions.clear();
  }
}
module.exports = { ShellSession, TerminalManager };
