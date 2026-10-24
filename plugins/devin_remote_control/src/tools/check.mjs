#!/usr/bin/env node
 // Syntax-checks owned JavaScript and enforces the project's structural rules.
import {
	execFileSync
} from "node:child_process";
import {
	readdirSync,
	readFileSync,
	statSync,
	existsSync
} from "node:fs";
import {
	dirname,
	join,
	resolve
} from "node:path";
import {
	fileURLToPath
} from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const owned = ["extension", "media"];
/**
 * Collect JavaScript sources recursively.
 *
 * @param {string} dir Directory to walk.
 * @param {string[]} sink Collected file paths.
 * @returns {void}
 */
const walk = (dir, sink) => {
	for (const entry of readdirSync(dir)) {
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) {
			if (entry !== "node_modules") walk(path, sink);
			continue;
		}
		if (/\.(m?js)$/.test(entry)) sink.push(path);
	}
};
/**
 * Report a missing required file.
 *
 * @param {string[]} failures Collected failure messages.
 * @param {string} path File path relative to the source root.
 * @param {string} reason Why the file is required.
 * @returns {void}
 */
const requireFile = (failures, path, reason) => {
	if (!existsSync(join(root, path))) failures.push(`missing: ${path} (${reason})`);
};
/** @type {string[]} */
const sources = [];
for (const dir of owned) walk(join(root, dir), sources);
for (const dir of ["test", "test_original"]) {
	const path = join(root, dir);
	if (existsSync(path)) walk(path, sources);
}
/**
 * Describe a failed child process call.
 *
 * @param {unknown} error Caught failure.
 * @returns {string} Human-readable detail.
 */
const describe = error => {
	if (error instanceof Error) return "stderr" in error ? String(error.stderr) : error.message;
	return String(error);
};
/** @type {string[]} */
const failures = [];
for (const file of sources) try {
	execFileSync(process.execPath, ["--check", file], {
		stdio: "pipe"
	});
}
catch (error) {
	failures.push(`syntax: ${file}\n${describe(error)}`);
}
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
if (typeof manifest.main !== "string") failures.push("package.json main is not a string");
else requireFile(failures, manifest.main.replace(/^\.\//, ""), "package.json main must resolve");
if (manifest.dependencies && Object.keys(manifest.dependencies).length) failures.push("package.json must not declare runtime dependencies");
for (const file of ["media/web/index.html", "media/web/js/app.js", "media/web/css/app.css", "media/web/js/service-worker.js", "media/panel/index.html", "media/panel/js/panel.js", "media/panel/css/panel.css", "media/shared/qr.js", ]) requireFile(failures, file, "structural web asset");
const gateway = join(root, "extension/lib/gateway-server.js");
if (existsSync(gateway) && !/Content-Security-Policy/i.test(readFileSync(gateway, "utf8"))) failures.push("gateway-server.js must emit a Content-Security-Policy header");
const shell = join(root, "media/web/index.html");
if (existsSync(shell)) {
	const html = readFileSync(shell, "utf8");
	if (/<script[^>]+src=["']https?:/i.test(html) || /<link[^>]+href=["']https?:/i.test(html)) failures.push("media/web/index.html must not load remote scripts or styles");
}
if (failures.length) {
	console.error(failures.join("\n"));
	process.exit(1);
}
console.log(`check: ${sources.length} files, structural rules OK`);
