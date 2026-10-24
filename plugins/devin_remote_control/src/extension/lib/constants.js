"use strict";
const Constants = Object.freeze({
	apiBase: "https://api.devin.ai/v3",
	cookieName: "devin_remote_session",
	eventHeartbeatMs: 15000,
	agentRefreshMs: 4000,
	maxBodyBytes: 8 * 1024 * 1024,
	maxMessagePages: 10,
	messagePageSize: 200,
	pairCodeDigits: 6,
	pairCodeLifetimeMs: 5 * 60 * 1000,
	pairAttemptWindowMs: 60 * 1000,
	pairAttemptsPerWindow: 8,
	requestTimeoutMs: 30000,
	terminalBufferBytes: 512 * 1024,
	publicAssetsCache: "public, max-age=3600",
	noCache: "no-store"
});
module.exports = {
	Constants
};
