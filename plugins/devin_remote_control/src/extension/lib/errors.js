"use strict";
/** HTTP failure raised by the Devin client. */
class HttpError extends Error {
	/**
	 * Creates an instance of HttpError.
	 * @param {number} status HTTP status code.
	 * @param {string} message Error message.
	 * @param {unknown} details Additional structured details.
	 */
	constructor(status, message, details = null) {
		super(message);
		this.name = "HttpError";
		/** @type {number} */
		this.status = status;
		/** @type {unknown} */
		this.details = details;
	}
}
/** Upstream API failure raised by the Devin client. */
class UpstreamError extends Error {
	/**
	 * Creates an instance of UpstreamError.
	 * @param {string} service Upstream service name.
	 * @param {number} status HTTP status code.
	 * @param {string} message Error message.
	 */
	constructor(service, status, message) {
		super(message);
		this.name = "UpstreamError";
		/** @type {string} */
		this.service = service;
		/** @type {number} */
		this.status = status;
	}
}
module.exports = {
	HttpError,
	UpstreamError
};
