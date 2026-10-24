"use strict";
/** Recursively resolves Explorer resources into a deterministic file list. */
class FileTree {
	/**
	 * Creates a file-tree resolver.
	 *
	 * @param {(uri: import("vscode").Uri) => Thenable<[string, number][]>} readDirectory
	 * Function used to enumerate a directory.
	 * @param {number} directoryType VS Code FileType.Directory bit.
	 */
	constructor(readDirectory, directoryType) {
		this.readDirectory = readDirectory;
		this.directoryType = directoryType;
	}
	/**
	 * Resolves files from the supplied resources.
	 *
	 * @param {import("vscode").Uri[]} resources Selected files and directories.
	 * @returns {Promise<import("vscode").Uri[]>} Unique files in stable path order.
	 */
	async resolve(resources) {
		const files = new Map();
		await Promise.all(resources.map(resource => this.#collect(resource, files)));
		return [...files.values()].sort((left, right) => left.toString().localeCompare(right.toString()), );
	}
	/**
	 * Collects one resource recursively.
	 *
	 * @param {import("vscode").Uri} resource Resource to collect.
	 * @param {Map<string, import("vscode").Uri>} files Collected files by URI.
	 * @returns {Promise<void>} Completion promise.
	 */
	#collect = async (resource, files) => {
		let entries;
		try {
			entries = await this.readDirectory(resource);
		} catch (error) {
			if (FileTree.isNotDirectory(error)) {
				files.set(resource.toString(), resource);
				return;
			}
			throw error;
		}
		await Promise.all(entries.map(([name, type]) => type & this.directoryType ? this.#collect(FileTree.join(resource, name), files) : FileTree.addFile(FileTree.join(resource, name), files), ), );
	};
	/**
	 * Adds a file and returns an already-resolved promise.
	 *
	 * @param {import("vscode").Uri} resource File URI.
	 * @param {Map<string, import("vscode").Uri>} files Collected files by URI.
	 * @returns {Promise<void>} Completion promise.
	 */
	static addFile = async (resource, files) => {
		files.set(resource.toString(), resource);
	};
	/**
	 * Joins one child name onto a URI path.
	 *
	 * @param {import("vscode").Uri} resource Parent URI.
	 * @param {string} name Child basename.
	 * @returns {import("vscode").Uri} Child URI.
	 */
	static join = (resource, name) => resource.with({
		path: `${resource.path.replace(/\/$/u, "")}/${name}`,
	});
	/**
	 * Reports whether an enumeration failure means the resource is a file.
	 *
	 * @param {unknown} error Enumeration failure.
	 * @returns {boolean} Whether the failure identifies a non-directory resource.
	 */
	static isNotDirectory = error => error instanceof Error && /not a directory|enotdir/iu.test(error.message);
}
module.exports = {
	FileTree
};
