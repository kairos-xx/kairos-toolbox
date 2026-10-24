"use strict";
const path = require("node:path");
/** Server configuration policy helpers. */
class ServerPolicy {
	/** @type {(value: unknown) => number} */
	static normalizePort = value => {
		const port = Number(value);
		if (!Number.isInteger(port) || port < 1 || port > 65535) throw new RangeError("Port must be 1..65535");
		return port;
	};
	/** @type {(workspace: string | undefined, fallback: string) => string} */
	static root = (workspace, fallback) => workspace ?? fallback;
	/** @type {(port: number) => string} */
	static url = port => `http://127.0.0.1:${ServerPolicy.normalizePort(port)}/`;
	/** @type {(python: string, port: number, root: string) => {command: string, args: string[]}} */
	static command = (python, port, root) => ({
		command: python,
		args: ["-m", "http.server", String(ServerPolicy.normalizePort(port)), "--bind", "127.0.0.1", "--directory", path.resolve(root)]
	});
}
module.exports = {
	ServerPolicy
};
