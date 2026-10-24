"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
const fs = require("node:fs/promises");
const path = require("node:path");
/**
 * @typedef {object} Project
 * @property {string} root Workspace folder path.
 * @typedef {object} TaskItem
 * @property {string} id Stable task identifier.
 * @property {string} source Originating file.
 * @property {string} label Display label.
 * @property {string} detail Explanation.
 * @property {string} command Shell command to run.
 */
/** Background task tracking. */
class TaskService {
	/** @param {unknown} value Value. */
	static quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;
	/** @param {string} filename Filename. */
	static exists = async filename => {
		try {
			await fs.access(filename);
			return true;
		} catch {
			return false;
		}
	};
	/** @param {string} root Root. */
	static packageManager = async root => {
		if (await TaskService.exists(path.join(root, "pnpm-lock.yaml"))) return "pnpm";
		if (await TaskService.exists(path.join(root, "yarn.lock"))) return "yarn";
		if (await TaskService.exists(path.join(root, "bun.lockb")) || await TaskService.exists(path.join(root, "bun.lock"))) return "bun";
		return "npm";
	};
	/**
	 * @param {Project} project Project.
	 * @returns {Promise<TaskItem[]>} Discovered tasks.
	 */
	static discover = async project => {
		/** @type {TaskItem[]} */
		const tasks = [];
		const packagePath = path.join(project.root, "package.json");
		try {
			const parsed = JSON.parse(await fs.readFile(packagePath, "utf8"));
			const pkg = parsed && typeof parsed === "object" ? /** @type {Record<string, *>} */ (parsed) : {};
			const manager = await TaskService.packageManager(project.root);
			const scripts = getAttribute(pkg, "scripts");
			Object.entries(scripts && typeof scripts === "object" ? scripts : {}).slice(0, 40).forEach(([name, script]) => {
				tasks.push({
					id: `package:${name}`,
					source: "package.json",
					label: name,
					detail: String(script),
					command: `${manager} run ${TaskService.quote(name)}`
				});
			});
		} catch (error) {
			if ( /** @type {NodeJS.ErrnoException} */ (error).code !== "ENOENT") throw error;
		}
		if (await TaskService.exists(path.join(project.root, "pyproject.toml"))) tasks.push({
			id: "python:pytest",
			source: "Python",
			label: "pytest",
			detail: "Run the Python test suite",
			command: "pytest"
		}, {
			id: "python:ruff",
			source: "Python",
			label: "ruff check",
			detail: "Run Ruff checks",
			command: "ruff check ."
		}, {
			id: "python:basedpyright",
			source: "Python",
			label: "basedpyright",
			detail: "Run BasedPyright",
			command: "basedpyright"
		});
		if (await TaskService.exists(path.join(project.root, "Makefile"))) {
			const source = await fs.readFile(path.join(project.root, "Makefile"), "utf8");
			const targets = [...source.matchAll(/^([A-Za-z0-9_.-]+):(?![=])/gmu)].map(match => match[1] ?? "").filter(name => !name.startsWith(".")).slice(0, 24);
			targets.forEach(name => {
				tasks.push({
					id: `make:${name}`,
					source: "Makefile",
					label: name,
					detail: `make ${name}`,
					command: `make ${TaskService.quote(name)}`
				});
			});
		}
		return tasks;
	};
}
module.exports = {
	TaskService
};
