"use strict";
const crypto = require("node:crypto");
/**
 * @typedef {object} ActivityItem
 * @property {string} id Entry identifier.
 * @property {string} type Activity category.
 * @property {string} title Activity title.
 * @property {string} detail Optional detail text.
 * @property {Record<string, unknown>} metadata Structured metadata.
 * @property {number} createdAt Creation timestamp.
 */
/** Records per-project activity entries with a size cap. */
class ActivityService {
	/**
	 * Creates an instance of ActivityService.
	 * @param {number} limit Maximum retained entries per project.
	 */
	constructor(limit = 240) {
		/** @type {number} */
		this.limit = limit;
		/** @type {Map<string, ActivityItem[]>} */
		this.projects = new Map();
	}
	/**
	 * Records.
	 * @param {string} projectId ProjectId.
	 * @param {string} type Type.
	 * @param {string} title Title.
	 * @param {string} detail Detail.
	 * @param {Record<string, unknown>} metadata Metadata.
	 * @returns {ActivityItem} The recorded item.
	 */
	record(projectId, type, title, detail = "", metadata = {}) {
		const items = this.projects.get(projectId) ?? [];
		const item = {
			id: crypto.randomBytes(8).toString("hex"),
			type,
			title,
			detail,
			metadata,
			createdAt: Date.now()
		};
		items.unshift(item);
		if (items.length > this.limit) items.length = this.limit;
		this.projects.set(projectId, items);
		return item;
	}
	/**
	 * Lists.
	 * @param {string} projectId ProjectId.
	 * @param {number} limit Limit.
	 * @returns {ActivityItem[]} Recent items.
	 */
	list(projectId, limit = 80) {
		return (this.projects.get(projectId) ?? []).slice(0, Math.max(1, Math.min(limit, this.limit)));
	}
	/**
	 * Clears.
	 * @param {string} projectId ProjectId.
	 */
	clear(projectId) {
		this.projects.delete(projectId);
	}
}
module.exports = {
	ActivityService
};
