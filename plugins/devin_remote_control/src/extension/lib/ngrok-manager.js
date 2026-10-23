"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
const { EventEmitter } = require("node:events");
const { spawn } = require("node:child_process");
/** Manages the ngrok tunnel lifecycle. */
class NgrokManager extends EventEmitter {
  /**
   * Creates an instance of NgrokManager.
   * @param {string} command ngrok executable name or path.
   * @param {() => Promise<string | undefined>} tokenProvider Secret token lookup.
   */
  constructor(command, tokenProvider = async () => "") {
    super();
    /** @type {string} */
    this.command = command;
    /** @type {() => Promise<string | undefined>} */
    this.tokenProvider = tokenProvider;
    /** @type {import("node:child_process").ChildProcess | null} */
    this.child = null;
    /** @type {string} */
    this.publicUrl = "";
    /** @type {string} */
    this.error = "";
    /** @type {"stopped" | "starting" | "online" | "error" | "stopping"} */
    this.state = "stopped";
  }
  /**
   * Returns a snapshot of.
   * @returns {{state: string, publicUrl: string, error: string}} Tunnel snapshot.
   */
  snapshot() {
    return {
      state: this.state,
      publicUrl: this.publicUrl,
      error: this.error
    };
  }
  /**
   * Starts.
   * @param {number} port Port.
   * @returns {Promise<{state: string, publicUrl: string, error: string}>} Tunnel snapshot.
   */
  async start(port) {
    if (this.child && !this.child.killed) return this.snapshot();
    this.state = "starting";
    this.error = "";
    this.publicUrl = "";
    this.emit("change", this.snapshot());
    const token = await this.tokenProvider();
    const child = spawn(
      this.command,
      ["http", String(port), "--log", "stdout", "--log-format", "json"],
      {
        env: {
          ...process.env,
          ...(token ? { NGROK_AUTHTOKEN: token } : {})
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true
      }
    );
    this.child = child;
    child.stdout?.on("data", chunk => this.inspectLog(chunk));
    child.stderr?.on("data", chunk => this.inspectLog(chunk));
    child.on("error", error => {
      this.state = "error";
      this.error = error.message;
      this.emit("change", this.snapshot());
    });
    child.on("close", code => {
      if (this.state !== "stopping") {
        this.state = code === 0 ? "stopped" : "error";
        this.error = code === 0 ? "" : `ngrok exited with code ${code ?? "unknown"}.`;
      } else {
        this.state = "stopped";
        this.error = "";
      }
      this.child = null;
      this.publicUrl = "";
      this.emit("change", this.snapshot());
    });
    await this.discoverUrl();
    return this.snapshot();
  }
  /**
   * Inspects log.
   * @param {Buffer} chunk Chunk.
   */
  inspectLog(chunk) {
    chunk.toString("utf8").split(/\r?\n/u).filter(Boolean).forEach(line => {
      try {
        const parsed = JSON.parse(line);
        const payload = parsed && typeof parsed === "object" ? /** @type {Record<string, *>} */ (parsed) : {};
        const nested = getAttribute(payload, "obj");
        const url = getAttribute(payload, "url")
          ?? (nested && typeof nested === "object" ? getAttribute(/** @type {Record<string, *>} */ (nested), "url") : "")
          ?? getAttribute(payload, "addr")
          ?? "";
        if (String(url).startsWith("https://")) {
          this.publicUrl = String(url);
          this.state = "online";
          this.error = "";
          this.emit("change", this.snapshot());
        }
      } catch {
        if (/ERR_NGROK|error|failed/iu.test(line)) {
          this.error = line.slice(0, 500);
          this.emit("change", this.snapshot());
        }
      }
    });
  }
  /** Discover url. */
  async discoverUrl() {
    await this.discoverAttempt(0);
  }
  /**
   * Performs one ngrok discovery attempt.
   * @param {number} attempt Attempt index.
   */
  async discoverAttempt(attempt) {
    if (attempt >= 32 || !this.child) return;
    try {
      const response = await fetch("http://127.0.0.1:4040/api/tunnels", {
        signal: AbortSignal.timeout(700)
      });
      if (response.ok) {
        const parsed = await response.json();
        const payload = parsed && typeof parsed === "object"
          ? /** @type {Record<string, *>} */ (parsed)
          : {};
        const tunnels = getAttribute(payload, "tunnels");
        const tunnel = Array.isArray(tunnels)
          ? tunnels.find(item => String(
              getAttribute(/** @type {Record<string, *>} */ (item), "public_url") ?? ""
            ).startsWith("https://"))
          : null;
        if (tunnel) {
          this.publicUrl = String(
            getAttribute(/** @type {Record<string, *>} */ (tunnel), "public_url")
          );
          this.state = "online";
          this.error = "";
          this.emit("change", this.snapshot());
          return;
        }
      }
    } catch {
      // The local ngrok inspector may not be ready yet.
    }
    await new Promise(resolve => { setTimeout(resolve, 250); });
    await this.discoverAttempt(attempt + 1);
  }
  /** Stops. */
  stop() {
    if (!this.child) return;
    this.state = "stopping";
    this.emit("change", this.snapshot());
    this.child.kill("SIGTERM");
  }
  dispose() {
    this.stop();
    this.removeAllListeners();
  }
}
module.exports = { NgrokManager };
