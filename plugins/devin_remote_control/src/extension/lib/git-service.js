"use strict";
const path = require("node:path");
const {
	spawn
} = require("node:child_process");
const {
	HttpError
} = require("./errors");
/**
 * @typedef {object} Project
 * @property {string} root Workspace folder path.
 * @typedef {object} GitResult
 * @property {number} code Exit code.
 * @property {string} stdout Standard output.
 * @property {string} stderr Standard error.
 * @typedef {object} GitStatus
 * @property {boolean} repository Whether the root is a Git worktree.
 * @property {string | null} branch Current branch name.
 * @property {string} [upstream] Configured upstream ref.
 * @property {number} [ahead] Commits ahead of upstream.
 * @property {number} [behind] Commits behind upstream.
 * @property {{status: string, path: string}[]} files Working tree entries.
 */
/** Git operations exposed to the webview. */
class GitService {
	/**
	 * Creates an instance of GitService.
	 * @param {Project} project Project whose root hosts the worktree.
	 */
	constructor(project) {
		this.project = project;
	}
	/**
	 * Runs a git subprocess inside the project root.
	 * @param {string[]} args Args.
	 * @returns {Promise<GitResult>} The captured process result.
	 */
	run(args) {
		return new Promise((resolve, reject) => {
			const child = spawn("git", args, {
				cwd: this.project.root,
				env: process.env,
				stdio: ["ignore", "pipe", "pipe"]
			});
			/** @type {Buffer[]} */
			const stdout = [];
			/** @type {Buffer[]} */
			const stderr = [];
			child.stdout?.on("data", chunk => stdout.push(chunk));
			child.stderr?.on("data", chunk => stderr.push(chunk));
			child.on("error", reject);
			child.on("close", code => resolve({
				code: code ?? 1,
				stdout: Buffer.concat(stdout).toString("utf8"),
				stderr: Buffer.concat(stderr).toString("utf8")
			}));
		});
	}
	/**
	 * Status.
	 * @returns {Promise<GitStatus>} Working tree summary.
	 */
	async status() {
		const result = await this.run(["status", "--porcelain=v1", "-z", "--branch"]);
		if (result.code !== 0) return {
			repository: false,
			branch: null,
			files: []
		};
		const records = result.stdout.split("\0").filter(Boolean);
		const branchRecord = records[0]?.startsWith("## ") ? records.shift()?.slice(3) ?? "HEAD" : "HEAD";
		const trackingStart = branchRecord.indexOf(" [");
		const head = trackingStart < 0 ? branchRecord : branchRecord.slice(0, trackingStart);
		const tracking = trackingStart < 0 ? "" : branchRecord.slice(trackingStart + 2).replace(/\]$/u, "");
		const separator = head.indexOf("...");
		const branch = separator < 0 ? head : head.slice(0, separator);
		const upstream = separator < 0 ? "" : head.slice(separator + 3);
		const aheadGroups = /ahead (?<count>\d+)/u.exec(tracking)?.groups;
		const ahead = Number(aheadGroups ? Reflect.get(aheadGroups, "count") : 0);
		const behindGroups = /behind (?<count>\d+)/u.exec(tracking)?.groups;
		const behind = Number(behindGroups ? Reflect.get(behindGroups, "count") : 0);
		return {
			repository: true,
			branch,
			upstream,
			ahead,
			behind,
			files: records.map(record => ({
				status: record.slice(0, 2),
				path: record.slice(3)
			}))
		};
	}
	/**
	 * Diff.
	 * @param {string} relativePath RelativePath.
	 * @returns {Promise<{diff: string, tracked: boolean, staged?: boolean}>} Diff payload.
	 */
	async diff(relativePath) {
		const normalized = relativePath.split("/").join(path.sep);
		const result = await this.run(["diff", "--no-ext-diff", "--no-color", "--unified=60", "--",
			normalized
		]);
		if (result.stdout) return {
			diff: result.stdout,
			tracked: true
		};
		const staged = await this.run(["diff", "--cached", "--no-ext-diff", "--no-color", "--unified=60", "--",
			normalized
		]);
		if (staged.stdout) return {
			diff: staged.stdout,
			tracked: true,
			staged: true
		};
		const tracked = await this.run(["ls-files", "--error-unmatch", "--", normalized]);
		if (tracked.code === 0) return {
			diff: "",
			tracked: true
		};
		const nullDevice = process.platform === "win32" ? "NUL" : "/dev/null";
		const untracked = await this.run(["diff", "--no-index", "--no-color", "--unified=60",
			nullDevice,
			normalized
		]);
		return {
			diff: untracked.stdout,
			tracked: false
		};
	}
	/**
	 * Stages.
	 * @param {string} relativePath RelativePath.
	 * @returns {Promise<GitStatus>} Updated status.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async stage(relativePath) {
		const result = await this.run(["add", "--", relativePath]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to stage file.");
		return this.status();
	}
	/**
	 * Unstages.
	 * @param {string} relativePath RelativePath.
	 * @returns {Promise<GitStatus>} Updated status.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async unstage(relativePath) {
		const result = await this.run(["restore", "--staged", "--", relativePath]);
		if (result.code !== 0) {
			const fallback = await this.run(["reset", "HEAD", "--", relativePath]);
			if (fallback.code !== 0) throw new HttpError(400, fallback.stderr.trim() || "Unable to unstage file.");
		}
		return this.status();
	}
	/**
	 * Log.
	 * @param {number} limit Limit.
	 * @returns {Promise<{hash: string, shortHash: string, author: string, email: string, date: string, subject: string}[]>} Recent commits.
	 */
	async log(limit = 30) {
		const count = Math.max(1, Math.min(Number(limit) || 30, 100));
		const result = await this.run(["log", `-${count}`, "--date=iso-strict", "--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s%x1e"]);
		if (result.code !== 0) return [];
		return result.stdout.split("\x1e").filter(Boolean).map(record => {
			const [hash = "", shortHash = "", author = "", email = "", date = "", subject = ""] = record.split("\x1f");
			return {
				hash,
				shortHash,
				author,
				email,
				date,
				subject
			};
		});
	}
	/**
	 * Branches.
	 * @returns {Promise<{name: string, current: boolean, upstream: string, updatedAt: string, subject: string}[]>} Local branches.
	 */
	async branches() {
		const result = await this.run(["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)%00%(HEAD)%00%(upstream:short)%00%(committerdate:iso-strict)%00%(subject)", "refs/heads"]);
		if (result.code !== 0) return [];
		return result.stdout.split("\n").filter(Boolean).map(line => {
			const [name = "", head = "", upstream = "", updatedAt = "", subject = ""] = line.split("\0");
			return {
				name,
				current: head === "*",
				upstream,
				updatedAt,
				subject
			};
		});
	}
	/**
	 * Validates branch.
	 * @param {string} name Name.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async validateBranch(name) {
		const result = await this.run(["check-ref-format", "--branch", name]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Invalid branch name.");
	}
	/**
	 * Creates branch.
	 * @param {string} name Name.
	 * @param {boolean} checkout Checkout.
	 * @returns {Promise<{status: GitStatus, branches: {name: string, current: boolean, upstream: string, updatedAt: string, subject: string}[]}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async createBranch(name, checkout = true) {
		await this.validateBranch(name);
		const result = await this.run(checkout ? ["switch", "-c", name] : ["branch", name]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to create branch.");
		return {
			status: await this.status(),
			branches: await this.branches()
		};
	}
	/**
	 * Checks out.
	 * @param {string} name Name.
	 * @returns {Promise<{status: GitStatus, branches: {name: string, current: boolean, upstream: string, updatedAt: string, subject: string}[]}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async checkout(name) {
		await this.validateBranch(name);
		const result = await this.run(["switch", name]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to switch branch.");
		return {
			status: await this.status(),
			branches: await this.branches()
		};
	}
	/**
	 * Commits.
	 * @param {string} message Message.
	 * @returns {Promise<{status: GitStatus, log: {hash: string, shortHash: string, author: string, email: string, date: string, subject: string}[], output: string}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async commit(message) {
		const clean = String(message).trim();
		if (!clean) throw new HttpError(400, "Commit message is required.");
		const result = await this.run(["commit", "-m", clean]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || result.stdout.trim() || "Unable to commit changes.");
		return {
			status: await this.status(),
			log: await this.log(20),
			output: result.stdout.trim()
		};
	}
	/**
	 * Pulls.
	 * @returns {Promise<{status: GitStatus, output: string}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async pull() {
		const result = await this.run(["pull", "--ff-only"]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to pull changes.");
		return {
			status: await this.status(),
			output: result.stdout.trim()
		};
	}
	/**
	 * Pushes.
	 * @returns {Promise<{status: GitStatus, output: string}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async push() {
		const status = await this.status();
		const args = status.upstream ? ["push"] : ["push", "-u", "origin", status.branch ?? "HEAD"];
		const result = await this.run(args);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to push changes.");
		return {
			status: await this.status(),
			output: result.stdout.trim() || result.stderr.trim()
		};
	}
	/**
	 * Discards.
	 * @param {string} relativePath RelativePath.
	 * @returns {Promise<GitStatus>} Updated status.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async discard(relativePath) {
		const tracked = await this.run(["ls-files", "--error-unmatch", "--", relativePath]);
		const result = tracked.code === 0 ? await this.run(["restore", "--staged", "--worktree", "--", relativePath]) : await this.run(["clean", "-f", "--", relativePath]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to discard changes.");
		return this.status();
	}
	/**
	 * Stages all.
	 * @returns {Promise<GitStatus>} Updated status.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async stageAll() {
		const result = await this.run(["add", "--all"]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to stage changes.");
		return this.status();
	}
	/**
	 * Unstages all.
	 * @returns {Promise<GitStatus>} Updated status.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async unstageAll() {
		const result = await this.run(["restore", "--staged", "."]);
		if (result.code !== 0) {
			const fallback = await this.run(["reset"]);
			if (fallback.code !== 0) throw new HttpError(400, fallback.stderr.trim() || "Unable to unstage changes.");
		}
		return this.status();
	}
	/**
	 * Commits detail.
	 * @param {string} hash Hash.
	 * @returns {Promise<{hash: string, shortHash: string, author: string, email: string, date: string, message: string, summary: string}>} Commit details.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async commitDetail(hash) {
		const result = await this.run(["show", "--stat", "--summary", "--format=%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%B%x1e", "--date=iso-strict",
			String(hash)
		]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to read commit.");
		const marker = result.stdout.indexOf("\x1e");
		const header = result.stdout.slice(0, marker).split("\x1f");
		return {
			hash: header[0] ?? "",
			shortHash: header[1] ?? "",
			author: header[2] ?? "",
			email: header[3] ?? "",
			date: header[4] ?? "",
			message: header.slice(5).join("\x1f").trim(),
			summary: marker < 0 ? "" : result.stdout.slice(marker + 1).trim()
		};
	}
	/**
	 * Shows blame for.
	 * @param {string} relativePath RelativePath.
	 * @returns {Promise<{hash: string, line: number, author: string, time: number, text: string}[]>} Blame entries.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async blame(relativePath) {
		const result = await this.run(["blame", "--line-porcelain", "--",
			relativePath
		]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to blame file.");
		/** @type {{hash: string, line: number, author: string, time: number, text: string}[]} */
		const lines = [];
		/** @type {{hash: string, line: number, author: string, time: number, text: string} | null} */
		let current = null;
		for (const line of result.stdout.split("\n")) {
			if (/^[0-9a-f]{40} /u.test(line)) {
				const parts = line.split(" ");
				current = {
					hash: parts[0] ?? "",
					line: Number(parts[2]),
					author: "",
					time: 0,
					text: ""
				};
			} else if (current && line.startsWith("author ")) current.author = line.slice(7);
			else if (current && line.startsWith("author-time ")) current.time = Number(line.slice(12));
			else if (current && line.startsWith("\t")) {
				current.text = line.slice(1);
				lines.push(current);
				current = null;
			}
		}
		return lines.slice(0, 5000);
	}
	/**
	 * File history.
	 * @param {string} relativePath RelativePath.
	 * @param {number} limit Limit.
	 * @returns {Promise<{hash: string, shortHash: string, author: string, date: string, subject: string}[]>} Commit history.
	 */
	async fileHistory(relativePath, limit = 40) {
		const count = Math.max(1, Math.min(Number(limit) || 40, 100));
		const result = await this.run(["log", "--follow", `-${count}`, "--date=iso-strict", "--pretty=format:%H%x1f%h%x1f%an%x1f%ad%x1f%s%x1e", "--",
			relativePath
		]);
		if (result.code !== 0) return [];
		return result.stdout.split("\x1e").filter(Boolean).map(record => {
			const [hash = "", shortHash = "", author = "", date = "", subject = ""] = record.split("\x1f");
			return {
				hash,
				shortHash,
				author,
				date,
				subject
			};
		});
	}
	/**
	 * File at revision.
	 * @param {string} relativePath RelativePath.
	 * @param {string} hash Hash.
	 * @returns {Promise<{hash: string, path: string, content: string}>} File contents at the revision.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async fileAtRevision(relativePath, hash) {
		const cleanHash = String(hash).trim();
		if (!/^[0-9a-f]{7,40}$/iu.test(cleanHash)) throw new HttpError(400, "Invalid commit hash.");
		const normalized = String(relativePath).replaceAll("\\", "/").replace(/^\/+/, "");
		const result = await this.run(["show", `${cleanHash}:${normalized}`]);
		if (result.code !== 0) throw new HttpError(404, result.stderr.trim() || "File does not exist at this revision.");
		return {
			hash: cleanHash,
			path: normalized,
			content: result.stdout
		};
	}
	/**
	 * Compares.
	 * @param {string} base Base.
	 * @param {string} head Head.
	 * @returns {Promise<{base: string, head: string, diff: string, stat: string, truncated: boolean}>} Comparison payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async compare(base, head = "HEAD") {
		/** @param {unknown} value Value. */
		const safeRef = value => {
			const ref = String(value).trim();
			if (!ref || !/^[A-Za-z0-9._/-]+$/u.test(ref)) throw new HttpError(400, "Invalid Git reference.");
			return ref;
		};
		const left = safeRef(base);
		const right = safeRef(head);
		const [diff, stat] = await Promise.all([
			this.run(["diff", "--no-ext-diff", "--no-color", "--unified=12", `${left}...${right}`]),
			this.run(["diff", "--stat", "--no-color", `${left}...${right}`])
		]);
		if (diff.code !== 0) throw new HttpError(400, diff.stderr.trim() || "Unable to compare revisions.");
		const maximum = 1_500_000;
		return {
			base: left,
			head: right,
			diff: diff.stdout.length > maximum ? `${diff.stdout.slice(0, maximum)}\n\n[diff truncated]` : diff.stdout,
			stat: stat.stdout.trim(),
			truncated: diff.stdout.length > maximum
		};
	}
	/**
	 * Stashes.
	 * @returns {Promise<{ref: string, hash: string, date: string, subject: string}[]>} Stash entries.
	 */
	async stashes() {
		const result = await this.run(["stash", "list", "--format=%gd%x1f%H%x1f%ai%x1f%s"]);
		if (result.code !== 0) return [];
		return result.stdout.split("\n").filter(Boolean).map(line => {
			const [ref = "", hash = "", date = "", subject = ""] = line.split("\x1f");
			return {
				ref,
				hash,
				date,
				subject
			};
		});
	}
	/**
	 * Stashes.
	 * @param {string} message Message.
	 * @returns {Promise<{status: GitStatus, stashes: {ref: string, hash: string, date: string, subject: string}[], output: string}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async stash(message = "Remote workspace stash") {
		const result = await this.run(["stash", "push", "--include-untracked", "-m", String(message)]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || "Unable to stash changes.");
		return {
			status: await this.status(),
			stashes: await this.stashes(),
			output: result.stdout.trim()
		};
	}
	/**
	 * Applies stash.
	 * @param {string} ref Ref.
	 * @param {boolean} drop Drop.
	 * @returns {Promise<{status: GitStatus, stashes: {ref: string, hash: string, date: string, subject: string}[], output: string}>} Result payload.
	 * @throws {HttpError} When the operation cannot complete.
	 */
	async applyStash(ref, drop = false) {
		const action = drop ? "pop" : "apply";
		const result = await this.run(["stash", action, String(ref)]);
		if (result.code !== 0) throw new HttpError(400, result.stderr.trim() || `Unable to ${action} stash.`);
		return {
			status: await this.status(),
			stashes: await this.stashes(),
			output: result.stdout.trim()
		};
	}
	/**
	 * Remote.
	 * @returns {Promise<string>} The origin URL or empty.
	 */
	async remote() {
		const result = await this.run(["config", "--get", "remote.origin.url"]);
		return result.code === 0 ? result.stdout.trim() : "";
	}
}
module.exports = {
	GitService
};
