"use strict";
const crypto = require("node:crypto");
const { Constants } = require("./constants");
const { HttpError } = require("./errors");
/**
 * @typedef {object} Pairing
 * @property {string} hash Hashed pairing code.
 * @property {number} expiresAt Epoch milliseconds when the code expires.
 * @typedef {object} Session
 * @property {string} id Session identifier.
 * @property {string} projectId Owning project identifier.
 * @property {string} csrfHash Hashed CSRF token.
 * @property {number} expiresAt Epoch milliseconds when the session expires.
 * @property {number} createdAt Creation timestamp.
 * @property {number} lastSeenAt Last activity timestamp.
 * @property {string} remoteAddress Client address.
 * @property {string} userAgent Client user agent.
 * @typedef {object} Attempt
 * @property {number} count Attempts inside the current window.
 * @property {number} resetAt Epoch milliseconds when the window resets.
 */
/** Pairing tokens, authentication, and session revocation. */
class SecurityService {
  /**
   * Creates an instance of SecurityService.
   * @param {number} sessionHours Session lifetime in hours.
   */
  constructor(sessionHours) {
    this.sessionLifetimeMs = sessionHours * 60 * 60 * 1000;
    /** @type {Map<string, Pairing>} */
    this.pairings = new Map();
    /** @type {Map<string, Session>} */
    this.sessions = new Map();
    /** @type {Map<string, Attempt>} */
    this.attempts = new Map();
  }
  /** @param {number} bytes Bytes. */
  static randomToken = bytes => crypto.randomBytes(bytes).toString("base64url");
  /** @param {string} value Value. */
  static hash = value => crypto.createHash("sha256").update(value).digest("hex");
  /**
   * @param {string} left Left.
   * @param {string} right Right.
   */
  static safeEqual = (left, right) => {
    const leftBuffer = Buffer.from(left);
    const rightBuffer = Buffer.from(right);
    return leftBuffer.length === rightBuffer.length
      && crypto.timingSafeEqual(leftBuffer, rightBuffer);
  };
  /**
   * Creates pairing.
   * @param {string} projectId ProjectId.
   * @returns {{code: string, expiresAt: number}} Pairing code details.
   */
  createPairing(projectId) {
    const code = String(crypto.randomInt(10 ** (Constants.pairCodeDigits - 1), 10 ** Constants.pairCodeDigits));
    this.pairings.set(projectId, {
      hash: SecurityService.hash(code),
      expiresAt: Date.now() + Constants.pairCodeLifetimeMs
    });
    return {
      code,
      expiresAt: Date.now() + Constants.pairCodeLifetimeMs
    };
  }
  /**
   * Pairs.
   * @param {string} projectId ProjectId.
   * @param {string} code Code.
   * @param {string} remoteAddress RemoteAddress.
   * @param {string} userAgent UserAgent.
   * @returns {{token: string, csrf: string, expiresAt: number}} Session credentials.
   * @throws {HttpError} When the operation cannot complete.
   */
  pair(projectId, code, remoteAddress, userAgent = "") {
    this.checkPairRate(remoteAddress);
    const pairing = this.pairings.get(projectId);
    if (!pairing || pairing.expiresAt < Date.now()) {
      this.pairings.delete(projectId);
      throw new HttpError(401, "Pairing code expired. Generate a new code in VS Code.");
    }
    if (!SecurityService.safeEqual(pairing.hash, SecurityService.hash(code)))
      throw new HttpError(401, "Invalid pairing code.");
    this.pairings.delete(projectId);
    const token = SecurityService.randomToken(32);
    const csrf = SecurityService.randomToken(24);
    const createdAt = Date.now();
    this.sessions.set(SecurityService.hash(token), {
      id: SecurityService.randomToken(9),
      projectId,
      csrfHash: SecurityService.hash(csrf),
      expiresAt: createdAt + this.sessionLifetimeMs,
      createdAt,
      lastSeenAt: createdAt,
      remoteAddress,
      userAgent: String(userAgent).slice(0, 240)
    });
    return { token, csrf, expiresAt: Date.now() + this.sessionLifetimeMs };
  }
  /**
   * Checks pair rate.
   * @param {string} remoteAddress RemoteAddress.
   * @throws {HttpError} When the operation cannot complete.
   */
  checkPairRate(remoteAddress) {
    const now = Date.now();
    const current = this.attempts.get(remoteAddress);
    if (!current || current.resetAt <= now) {
      this.attempts.set(remoteAddress, { count: 1, resetAt: now + Constants.pairAttemptWindowMs });
      return;
    }
    current.count += 1;
    if (current.count > Constants.pairAttemptsPerWindow)
      throw new HttpError(429, "Too many pairing attempts. Try again shortly.");
  }
  /**
   * Authenticate.
   * @param {string} projectId ProjectId.
   * @param {string} token Token.
   * @returns {Session} The authenticated session.
   * @throws {HttpError} When the operation cannot complete.
   */
  authenticate(projectId, token) {
    if (!token) throw new HttpError(401, "Pair this device from VS Code first.");
    const key = SecurityService.hash(token);
    const session = this.sessions.get(key);
    if (!session || session.expiresAt <= Date.now()) {
      this.sessions.delete(key);
      throw new HttpError(401, "Remote session expired. Pair this device again.");
    }
    if (session.projectId !== projectId) throw new HttpError(403, "Session is not valid for this project.");
    session.lastSeenAt = Date.now();
    return session;
  }
  /** @param {Session} session Session. */
  static issueCsrf = session => {
    const csrf = SecurityService.randomToken(24);
    session.csrfHash = SecurityService.hash(csrf);
    return csrf;
  }
  /**
   * @param {Session} session Session.
   * @param {string} csrf Csrf.
   */
  static authorizeMutation = (session, csrf) => {
    if (!csrf || !SecurityService.safeEqual(session.csrfHash, SecurityService.hash(csrf)))
      throw new HttpError(403, "Invalid CSRF token.");
  };
  /**
   * Revokes project.
   * @param {string} projectId ProjectId.
   */
  revokeProject(projectId) {
    [...this.sessions.entries()]
      .filter(([, session]) => session.projectId === projectId)
      .forEach(([key]) => { this.sessions.delete(key); });
  }
  /**
   * Lists project sessions.
   * @param {string} projectId ProjectId.
   * @returns {{id: string, createdAt: number, lastSeenAt: number, expiresAt: number, remoteAddress: string, userAgent: string}[]} Active sessions.
   */
  listProjectSessions(projectId) {
    return [...this.sessions.values()]
      .filter(session => session.projectId === projectId && session.expiresAt > Date.now())
      .map(session => ({
        id: session.id,
        createdAt: session.createdAt,
        lastSeenAt: session.lastSeenAt,
        expiresAt: session.expiresAt,
        remoteAddress: session.remoteAddress,
        userAgent: session.userAgent
      }))
      .sort((left, right) => right.lastSeenAt - left.lastSeenAt);
  }
  /**
   * Revokes session.
   * @param {string} projectId ProjectId.
   * @param {string} sessionId SessionId.
   * @throws {HttpError} When the operation cannot complete.
   */
  revokeSession(projectId, sessionId) {
    const entry = [...this.sessions.entries()].find(([, session]) => (
      session.projectId === projectId && session.id === sessionId
    ));
    if (!entry) throw new HttpError(404, "Paired device session not found.");
    this.sessions.delete(entry[0] ?? "");
  }
  /** Cleans up. */
  cleanup() {
    const now = Date.now();
    [...this.sessions.entries()]
      .filter(([, session]) => session.expiresAt <= now)
      .forEach(([key]) => { this.sessions.delete(key); });
    [...this.pairings.entries()]
      .filter(([, pairing]) => pairing.expiresAt <= now)
      .forEach(([key]) => { this.pairings.delete(key); });
    [...this.attempts.entries()]
      .filter(([, attempt]) => attempt.resetAt <= now)
      .forEach(([key]) => { this.attempts.delete(key); });
  }
}
module.exports = { SecurityService };
