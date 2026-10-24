"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
const {
	Constants
} = require("./constants");
const {
	UpstreamError
} = require("./errors");
/**
 * @typedef {object} SessionInput
 * @property {string} prompt Session prompt text.
 * @property {string} [title] Optional session title.
 * @property {number | null} [maxAcuLimit] Optional ACU ceiling.
 * @property {string} [repo] Optional repository URL.
 * @property {string[]} [tags] Optional tag list.
 * @typedef {object} Project
 * @property {string} id Stable twelve-character project identifier.
 * @property {string} gitRemote Upstream remote URL.
 */
/** Minimal Devin API client. */
class DevinClient {
	/**
	 * Creates an instance of DevinClient.
	 * @param {string} orgId Devin organization identifier.
	 * @param {() => Promise<string | undefined>} tokenProvider Secret token lookup.
	 */
	constructor(orgId, tokenProvider) {
		/** @type {string} */
		this.orgId = orgId;
		/** @type {() => Promise<string | undefined>} */
		this.tokenProvider = tokenProvider;
	}
	/**
	 * Configured.
	 * @returns {boolean} Whether an organization ID is configured.
	 */
	configured() {
		return Boolean(this.orgId);
	}
	/**
	 * Request.
	 * @param {string} method Method.
	 * @param {string} pathname Pathname.
	 * @param {unknown} body Body.
	 * @param {Record<string, unknown> | null} query Query.
	 * @returns {Promise<Record<string, *> | null>} Parsed response payload.
	 * @throws {UpstreamError} When the operation cannot complete.
	 */
	async request(method, pathname, body = null, query = null) {
		const token = await this.tokenProvider();
		if (!this.orgId || !token) throw new UpstreamError("Devin", 401, "Devin credentials are not configured in VS Code.");
		const url = new URL(`${Constants.apiBase}/organizations/${encodeURIComponent(this.orgId)}${pathname}`);
		Object.entries(query ?? {}).forEach(([key, value]) => {
			if (value !== null && value !== undefined) url.searchParams.set(key, String(value));
		});
		const response = await fetch(url, {
			method,
			headers: {
				Authorization: `Bearer ${token}`,
				...(body === null ? {} : {
					"Content-Type": "application/json"
				})
			},
			...(body === null ? {} : {
				body: JSON.stringify(body)
			}),
			signal: AbortSignal.timeout(Constants.requestTimeoutMs)
		});
		const text = await response.text();
		/** @type {Record<string, *> | null} */
		let payload;
		try {
			const parsed = text ? JSON.parse(text) : null;
			payload = parsed && typeof parsed === "object" ? parsed : null;
		} catch (error) {
			throw new UpstreamError("Devin", response.status, `Devin returned invalid JSON: ${error instanceof Error ? getAttribute(error, "message") : String(error)}`);
		}
		if (!response.ok) {
			const detail = getAttribute(payload, "detail");
			const first = Array.isArray(detail) ? getAttribute( /** @type {Record<string, *>} */ (detail[0]), "msg") : detail;
			const message = first ?? getAttribute(payload, "message") ?? (text || `HTTP ${response.status}`);
			throw new UpstreamError("Devin", response.status, String(message));
		}
		return payload;
	}
	/**
	 * Lists sessions.
	 * @param {boolean} includeArchived IncludeArchived.
	 * @returns {Promise<*[]>} Session records.
	 */
	async listSessions(includeArchived = true) {
		/** @type {*[]} */
		const items = [];
		await this.collectSessions(items, null, 0, includeArchived);
		return items;
	}
	/**
	 * Collects paginated sessions.
	 * @param {*[]} items Destination records.
	 * @param {string | null} after Cursor.
	 * @param {number} page Page index.
	 * @param {boolean} includeArchived Include archived sessions.
	 */
	async collectSessions(items, after, page, includeArchived) {
		if (page >= Constants.maxMessagePages) return;
		const payload = await this.request("GET", "/sessions", null, {
			first: Constants.messagePageSize,
			after,
			...(includeArchived ? {} : {
				is_archived: false
			})
		});
		const pageItems = getAttribute(payload, "items");
		if (Array.isArray(pageItems)) items.push(...pageItems);
		const cursor = getAttribute(payload, "end_cursor");
		if (getAttribute(payload, "has_next_page") && typeof cursor === "string") await this.collectSessions(items, cursor, page + 1, includeArchived);
	}
	/**
	 * Messages.
	 * @param {string} sessionId SessionId.
	 * @returns {Promise<*[]>} Message records.
	 */
	async messages(sessionId) {
		/** @type {*[]} */
		const items = [];
		await this.collectMessages(sessionId, items, null, 0);
		return items;
	}
	/**
	 * Collects paginated messages.
	 * @param {string} sessionId Session identifier.
	 * @param {*[]} items Destination records.
	 * @param {string | null} after Cursor.
	 * @param {number} page Page index.
	 */
	async collectMessages(sessionId, items, after, page) {
		if (page >= Constants.maxMessagePages) return;
		const payload = await this.request("GET", `/sessions/${encodeURIComponent(sessionId)}/messages`, null, {
			first: Constants.messagePageSize,
			after
		});
		const pageItems = getAttribute(payload, "items");
		if (Array.isArray(pageItems)) items.push(...pageItems);
		const cursor = getAttribute(payload, "end_cursor");
		if (getAttribute(payload, "has_next_page") && typeof cursor === "string") await this.collectMessages(sessionId, items, cursor, page + 1);
	}
	/**
	 * Sends message.
	 * @param {string} sessionId SessionId.
	 * @param {string} message Message.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async sendMessage(sessionId, message) {
		return this.request("POST", `/sessions/${encodeURIComponent(sessionId)}/messages`, {
			message
		});
	}
	/**
	 * Attachments.
	 * @param {string} sessionId SessionId.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async attachments(sessionId) {
		return this.request("GET", `/sessions/${encodeURIComponent(sessionId)}/attachments`);
	}
	/**
	 * Insights.
	 * @param {string} sessionId SessionId.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async insights(sessionId) {
		return this.request("GET", `/sessions/${encodeURIComponent(sessionId)}/insights`);
	}
	/**
	 * Generates insights.
	 * @param {string} sessionId SessionId.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async generateInsights(sessionId) {
		return this.request("POST", `/sessions/${encodeURIComponent(sessionId)}/insights/generate`);
	}
	/**
	 * Tags.
	 * @param {string} sessionId SessionId.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async tags(sessionId) {
		return this.request("GET", `/sessions/${encodeURIComponent(sessionId)}/tags`);
	}
	/**
	 * Replaces tags.
	 * @param {string} sessionId SessionId.
	 * @param {string[]} tags Tags.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async replaceTags(sessionId, tags) {
		return this.request("PUT", `/sessions/${encodeURIComponent(sessionId)}/tags`, {
			tags
		});
	}
	/**
	 * Creates session.
	 * @param {SessionInput} input Input.
	 * @param {Project} project Project.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async createSession(input, project) {
		const tags = [...new Set([...(input.tags ?? []), `vscode-project:${project.id}`])];
		const repo = input.repo || project.gitRemote;
		return this.request("POST", "/sessions", {
			prompt: input.prompt,
			...(input.title ? {
				title: input.title
			} : {}),
			...(input.maxAcuLimit ? {
				max_acu_limit: input.maxAcuLimit
			} : {}),
			...(repo ? {
				repos: [repo]
			} : {}),
			tags
		});
	}
	/**
	 * Terminates.
	 * @param {string} sessionId SessionId.
	 * @param {boolean} archive Archive.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async terminate(sessionId, archive = false) {
		return this.request("DELETE", `/sessions/${encodeURIComponent(sessionId)}`, null, {
			archive
		});
	}
	/**
	 * Archives.
	 * @param {string} sessionId SessionId.
	 * @returns {Promise<Record<string, *> | null>} Response payload.
	 */
	async archive(sessionId) {
		return this.request("POST", `/sessions/${encodeURIComponent(sessionId)}/archive`);
	}
}
module.exports = {
	DevinClient
};
