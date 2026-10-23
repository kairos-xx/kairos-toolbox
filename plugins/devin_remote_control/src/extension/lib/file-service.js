"use strict";
const fs = require("node:fs/promises");
const path = require("node:path");
const { HttpError } = require("./errors");
/**
 * @typedef {object} Project
 * @property {string} root Workspace folder path.
 */
/** Workspace file operations exposed to the webview. */
class FileService {
  /**
   * Creates an instance of FileService.
   * @param {number} maxBytes Maximum editable/transferred file size.
   * @param {string[]} excludes Directory entry names to hide.
   */
  constructor(maxBytes, excludes) {
    this.maxBytes = maxBytes;
    this.excludes = new Set(excludes);
  }
  /** @param {Project} project Project. */
  static rootReal = project => fs.realpath(project.root);
  /**
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   */
  static existingPath = async (project, relativePath) => {
    const root = await FileService.rootReal(project);
    const lexical = path.resolve(root, relativePath || ".");
    FileService.assertInside(root, lexical);
    const target = await fs.realpath(lexical);
    FileService.assertInside(root, target);
    return { root, target, lexical };
  };
  /**
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   */
  static writablePath = async (project, relativePath) => {
    const root = await FileService.rootReal(project);
    const target = path.resolve(root, relativePath);
    FileService.assertInside(root, target);
    const lexicalParent = path.dirname(target);
    const parent = await fs.realpath(lexicalParent);
    FileService.assertInside(root, parent);
    if (parent !== lexicalParent)
      throw new HttpError(403, "Writing through symlinked directories is disabled.");
    return { root, target };
  };
  /**
   * @param {string} root Root.
   * @param {string} target Target.
   */
  static assertInside = (root, target) => {
    const prefix = root.endsWith(path.sep) ? root : `${root}${path.sep}`;
    if (target !== root && !target.startsWith(prefix))
      throw new HttpError(403, "Path escapes the project root.");
  };
  /**
   * Lists.
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @returns {Promise<{name: string, path: string, kind: string, size: number, mtimeMs: number}[]>} Directory entries.
   * @throws {HttpError} When the operation cannot complete.
   */
  async list(project, relativePath = "") {
    const { root, target } = await FileService.existingPath(project, relativePath);
    if (!(await fs.stat(target)).isDirectory()) throw new HttpError(400, "Path is not a directory.");
    const entries = await fs.readdir(target, { withFileTypes: true });
    const visible = entries
      .filter(entry => !this.excludes.has(entry.name))
      .map(async entry => {
        const absolute = path.join(target, entry.name);
        const stats = await fs.lstat(absolute);
        const relative = path.relative(root, absolute).split(path.sep).join("/");
        return {
          name: entry.name,
          path: relative,
          kind: entry.isDirectory() ? "directory" : entry.isSymbolicLink() ? "symlink" : "file",
          size: stats.size,
          mtimeMs: stats.mtimeMs
        };
      });
    return (await Promise.all(visible)).sort((left, right) => (
      left.kind === right.kind
        ? left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: "base" })
        : left.kind === "directory" ? -1 : 1
    ));
  }
  /**
   * Reads.
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @returns {Promise<{path: string, content: string, size: number, mtimeMs: number, language: string}>} File contents and metadata.
   * @throws {HttpError} When the operation cannot complete.
   */
  async read(project, relativePath) {
    const { root, target } = await FileService.existingPath(project, relativePath);
    const stats = await fs.stat(target);
    if (!stats.isFile()) throw new HttpError(400, "Path is not a regular file.");
    if (stats.size > this.maxBytes) throw new HttpError(413, `File exceeds the ${this.maxBytes}-byte remote editing limit.`);
    const buffer = await fs.readFile(target);
    if (buffer.includes(0)) throw new HttpError(415, "Binary files are preview-only and cannot be edited remotely.");
    return {
      path: path.relative(root, target).split(path.sep).join("/"),
      content: buffer.toString("utf8"),
      size: stats.size,
      mtimeMs: stats.mtimeMs,
      language: FileService.languageFor(target)
    };
  }
  /**
   * Reads raw.
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @returns {Promise<{path: string, content: Buffer, size: number, contentType: string}>} Raw file contents.
   * @throws {HttpError} When the operation cannot complete.
   */
  async readRaw(project, relativePath) {
    const { root, target } = await FileService.existingPath(project, relativePath);
    const stats = await fs.stat(target);
    if (!stats.isFile()) throw new HttpError(400, "Path is not a regular file.");
    if (stats.size > this.maxBytes)
      throw new HttpError(413, `File exceeds the ${this.maxBytes}-byte remote transfer limit.`);
    return {
      path: path.relative(root, target).split(path.sep).join("/"),
      content: await fs.readFile(target),
      size: stats.size,
      contentType: FileService.contentTypeFor(target)
    };
  }
  /**
   * Writes base64.
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @param {string} base64 Base64.
   * @param {boolean} overwrite Overwrite.
   * @returns {Promise<{path: string, size: number, mtimeMs: number}>} Written file metadata.
   * @throws {HttpError} When the operation cannot complete.
   */
  async writeBase64(project, relativePath, base64, overwrite = false) {
    const normalized = String(base64).replace(/\s+/gu, "");
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(normalized))
      throw new HttpError(400, "Upload content is not valid base64.");
    const content = Buffer.from(normalized, "base64");
    if (content.byteLength > this.maxBytes)
      throw new HttpError(413, `Upload exceeds the ${this.maxBytes}-byte remote transfer limit.`);
    const { root, target } = await FileService.writablePath(project, relativePath);
    let exists = false;
    try {
      const current = await fs.lstat(target);
      exists = true;
      if (current.isSymbolicLink()) throw new HttpError(403, "Writing through symlinks is disabled.");
      if (!current.isFile()) throw new HttpError(400, "Upload target exists and is not a regular file.");
    } catch (error) {
      if (error instanceof HttpError) throw error;
      if (/** @type {NodeJS.ErrnoException} */ (error).code !== "ENOENT") throw error;
    }
    if (exists && !overwrite) throw new HttpError(409, "Upload target already exists.");
    await fs.writeFile(target, content, { flag: overwrite ? "w" : "wx" });
    const stats = await fs.stat(target);
    return {
      path: path.relative(root, target).split(path.sep).join("/"),
      size: stats.size,
      mtimeMs: stats.mtimeMs
    };
  }
  /**
   * Writes.
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @param {string} content Content.
   * @param {number | null} expectedMtimeMs ExpectedMtimeMs.
   * @returns {Promise<{path: string, size: number, mtimeMs: number}>} Written file metadata.
   * @throws {HttpError} When the operation cannot complete.
   */
  async write(project, relativePath, content, expectedMtimeMs = null) {
    if (Buffer.byteLength(content, "utf8") > this.maxBytes) throw new HttpError(413, "Content exceeds the remote editing limit.");
    const { root, target } = await FileService.writablePath(project, relativePath);
    try {
      const current = await fs.lstat(target);
      if (current.isSymbolicLink()) throw new HttpError(403, "Writing through symlinks is disabled.");
      if (!current.isFile()) throw new HttpError(400, "Target exists and is not a regular file.");
      if (expectedMtimeMs !== null && Math.abs(current.mtimeMs - expectedMtimeMs) > 1)
        throw new HttpError(409, "File changed on disk. Reload before overwriting it.");
    } catch (error) {
      if (error instanceof HttpError) throw error;
      if (/** @type {NodeJS.ErrnoException} */ (error).code !== "ENOENT") throw error;
    }
    await fs.writeFile(target, content, { encoding: "utf8", flag: "w" });
    const stats = await fs.stat(target);
    return {
      path: path.relative(root, target).split(path.sep).join("/"),
      size: stats.size,
      mtimeMs: stats.mtimeMs
    };
  }
  /**
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   */
  static createDirectory = async (project, relativePath) => {
    const { root, target } = await FileService.writablePath(project, relativePath);
    await fs.mkdir(target, { recursive: false });
    return { path: path.relative(root, target).split(path.sep).join("/") };
  };
  /**
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @param {string} nextRelativePath NextRelativePath.
   */
  static rename = async (project, relativePath, nextRelativePath) => {
    const { root, target, lexical } = await FileService.existingPath(project, relativePath);
    if ((await fs.lstat(lexical)).isSymbolicLink())
      throw new HttpError(403, "Renaming symlinks remotely is disabled.");
    const next = await FileService.writablePath(project, nextRelativePath);
    await fs.rename(target, next.target);
    return { path: path.relative(root, next.target).split(path.sep).join("/") };
  };
  /**
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   */
  static remove = async (project, relativePath) => {
    const { root, target, lexical } = await FileService.existingPath(project, relativePath);
    if (target === root) throw new HttpError(403, "The project root cannot be removed.");
    const lexicalStats = await fs.lstat(lexical);
    if (lexicalStats.isSymbolicLink())
      throw new HttpError(403, "Removing symlinks remotely is disabled.");
    await fs.rm(target, { recursive: lexicalStats.isDirectory(), force: false });
    return { removed: path.relative(root, target).split(path.sep).join("/") };
  };
  /**
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @param {string} nextRelativePath NextRelativePath.
   */
  static copy = async (project, relativePath, nextRelativePath) => {
    const { root, target, lexical } = await FileService.existingPath(project, relativePath);
    if ((await fs.lstat(lexical)).isSymbolicLink())
      throw new HttpError(403, "Copying symlinks remotely is disabled.");
    const next = await FileService.writablePath(project, nextRelativePath);
    await fs.cp(target, next.target, { recursive: true, errorOnExist: true });
    return { path: path.relative(root, next.target).split(path.sep).join("/") };
  };
  /**
   * Stats.
   * @param {Project} project Project.
   * @returns {Promise<{files: number, directories: number, bytes: number, truncated: boolean, languages: {language: string, files: number}[]}>} Workspace statistics.
   */
  async stats(project) {
    const root = await FileService.rootReal(project);
    const summary = { files: 0, directories: 0, bytes: 0, languages: /** @type {Map<string, number>} */ (new Map()) };
    /** @type {string[]} */
    const queue = [root];
    /** @returns {Promise<void>} Nothing. */
    const visitDirectory = async () => {
      const directory = queue.shift();
      if (directory === undefined || summary.files >= 10000) return;
      const entries = await fs.readdir(directory, { withFileTypes: true });
      /**
       * Visits a single directory entry.
       * @param {number} index Entry index.
       * @returns {Promise<void>} Nothing.
       */
      const visitEntry = async index => {
        const entry = entries[index];
        if (entry === undefined) return;
        if (!(this.excludes.has(entry.name) || entry.isSymbolicLink())) {
          const absolute = path.join(directory, entry.name);
          if (entry.isDirectory()) {
            summary.directories += 1;
            queue.push(absolute);
          } else if (entry.isFile()) {
            const stats = await fs.stat(absolute);
            summary.files += 1;
            summary.bytes += stats.size;
            const language = FileService.languageFor(absolute);
            summary.languages.set(language, (summary.languages.get(language) ?? 0) + 1);
          }
        }
        await visitEntry(index + 1);
      };
      await visitEntry(0);
      await visitDirectory();
    };
    await visitDirectory();
    return {
      files: summary.files,
      directories: summary.directories,
      bytes: summary.bytes,
      truncated: summary.files >= 10000,
      languages: [...summary.languages.entries()]
        .map(([language, files]) => ({ language, files }))
        .sort((left, right) => right.files - left.files)
        .slice(0, 12)
    };
  }
  /**
   * Search.
   * @param {Project} project Project.
   * @param {string} query Query.
   * @param {number} limit Limit.
   * @returns {Promise<{path: string, line: number, preview: string, kind: string}[]>} Matching results.
   */
  async search(project, query, limit = 60) {
    const text = String(query).trim();
    if (!text) return [];
    const root = await FileService.rootReal(project);
    const lower = text.toLowerCase();
    /** @type {{path: string, line: number, preview: string, kind: string}[]} */
    const results = [];
    /** @type {string[]} */
    const queue = [root];
    let scanned = 0;
    /** @returns {Promise<void>} Nothing. */
    const visitDirectory = async () => {
      const directory = queue.shift();
      if (directory === undefined || results.length >= limit || scanned >= 10000) return;
      const entries = await fs.readdir(directory, { withFileTypes: true });
      /**
       * Visits a single directory entry.
       * @param {number} index Entry index.
       * @returns {Promise<void>} Nothing.
       */
      const visitEntry = async index => {
        const entry = entries[index];
        if (entry === undefined || results.length >= limit) return;
        if (!(this.excludes.has(entry.name) || entry.isSymbolicLink())) {
          const absolute = path.join(directory, entry.name);
          if (entry.isDirectory()) queue.push(absolute);
          else if (entry.isFile()) {
            scanned += 1;
            const relative = path.relative(root, absolute).split(path.sep).join("/");
            if (entry.name.toLowerCase().includes(lower))
              results.push({ path: relative, line: 0, preview: "Filename match", kind: "name" });
            if (results.length < limit) {
              const stats = await fs.stat(absolute);
              if (stats.size <= Math.min(this.maxBytes, 524288)) {
                const buffer = await fs.readFile(absolute);
                if (!buffer.includes(0)) {
                  const lines = buffer.toString("utf8").split(/\r?\n/u);
                  for (let lineIndex = 0; lineIndex < lines.length && results.length < limit; lineIndex += 1) {
                    const lineText = lines[lineIndex] ?? "";
                    if (lineText.toLowerCase().includes(lower))
                      results.push({
                        path: relative,
                        line: lineIndex + 1,
                        preview: lineText.trim().slice(0, 240),
                        kind: "content"
                      });
                  }
                }
              }
            }
          }
        }
        await visitEntry(index + 1);
      };
      await visitEntry(0);
      await visitDirectory();
    };
    await visitDirectory();
    return results;
  }
  /**
   * Inspects.
   * @param {Project} project Project.
   * @param {string} relativePath RelativePath.
   * @returns {Promise<Record<string, unknown>>} File inspection summary.
   */
  async inspect(project, relativePath) {
    const file = await this.read(project, relativePath);
    const lines = file.content.split(/\r?\n/u);
    const todo = lines.reduce(
      /** @type {(total: number, line: string) => number} */
      ((total, line) => total + (/\b(?:TODO|FIXME|HACK|XXX)\b/u.test(line) ? 1 : 0)),
      0
    );
    const nonEmpty = lines.filter(line => line.trim()).length;
    const words = file.content.trim() ? file.content.trim().split(/\s+/u).length : 0;
    /** @type {{name: string, kind: string, line: number}[]} */
    const symbols = [];
    /** @type {[RegExp, string][]} */
    const patterns = [
      [/^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)/u, "function"],
      [/^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)/u, "class"],
      [/^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)/u, "variable"],
      [/^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)/u, "function"],
      [/^\s*class\s+([A-Za-z_][\w]*)/u, "class"]
    ];
    lines.forEach((line, index) => {
      for (const [pattern, kind] of patterns) {
        const match = pattern.exec(line);
        if (!match) continue;
        symbols.push({ name: match[1] ?? "", kind, line: index + 1 });
        break;
      }
    });
    return {
      path: file.path,
      language: file.language,
      size: file.size,
      lines: lines.length,
      nonEmptyLines: nonEmpty,
      words,
      characters: file.content.length,
      todo,
      symbols: symbols.slice(0, 200)
    };
  }
  /** @param {string} filename Filename. */
  static contentTypeFor = filename => new Map([
    [".css", "text/css; charset=utf-8"],
    [".csv", "text/csv; charset=utf-8"],
    [".gif", "image/gif"],
    [".html", "text/html; charset=utf-8"],
    [".jpeg", "image/jpeg"],
    [".jpg", "image/jpeg"],
    [".js", "text/javascript; charset=utf-8"],
    [".json", "application/json; charset=utf-8"],
    [".md", "text/markdown; charset=utf-8"],
    [".pdf", "application/pdf"],
    [".png", "image/png"],
    [".svg", "image/svg+xml; charset=utf-8"],
    [".txt", "text/plain; charset=utf-8"],
    [".webp", "image/webp"],
    [".xml", "application/xml; charset=utf-8"]
  ]).get(path.extname(filename).toLowerCase()) ?? "application/octet-stream";
  /** @param {string} filename Filename. */
  static languageFor = filename => {
    const basename = path.basename(filename).toLowerCase();
    if (["dockerfile", "makefile"].includes(basename)) return basename;
    return new Map([
      [".py", "python"], [".js", "javascript"], [".mjs", "javascript"],
      [".cjs", "javascript"], [".ts", "typescript"], [".tsx", "typescript"],
      [".jsx", "javascript"], [".html", "html"], [".htm", "html"],
      [".css", "css"], [".scss", "css"], [".json", "json"],
      [".md", "markdown"], [".toml", "toml"], [".yaml", "yaml"],
      [".yml", "yaml"], [".sh", "shell"], [".zsh", "shell"],
      [".bash", "shell"], [".xml", "xml"], [".svg", "xml"],
      [".sql", "sql"], [".go", "go"], [".rs", "rust"], [".swift", "swift"]
    ]).get(path.extname(basename)) ?? "text";
  };
}
module.exports = { FileService };
