/**
 * A small client for the dashboard's API. Every failure becomes an
 * {@link ApiError} that carries the server's own words, so a screen can show
 * a person what went wrong instead of a status code.
 */
/** A request that did not succeed. */
class ApiError extends Error {
	/**
	 * @param {number} status HTTP status; 0 when the server could not be reached.
	 * @param {string} message What went wrong, in words a person can act on.
	 */
	constructor(status, message) {
		super(message);
		this.name = "ApiError";
		this.status = status;
	}
}
/** Talk to the dashboard's JSON API. */
class Api {
	/** Status that means this browser is not paired. */
	static unauthorized = 401;
	/**
	 * @param {typeof fetch} fetcher How to send a request; injected so tests need no network.
	 */
	constructor(fetcher = (...args) => fetch(...args)) {
		this.fetcher = fetcher;
	}
	/**
	 * Read JSON from the server.
	 * @param {string} path Absolute path.
	 * @returns {Promise<any>} The decoded body.
	 */
	get(path) {
		return this.send(path, {
			method: "GET"
		});
	}
	/**
	 * Send JSON to the server.
	 * @param {string} path Absolute path.
	 * @param {object} body What to send.
	 * @returns {Promise<any>} The decoded body.
	 */
	post(path, body) {
		return this.send(path, {
			method: "POST",
			headers: {
				"Content-Type": "application/json"
			},
			body: JSON.stringify(body)
		});
	}
	/**
	 * Make a request and turn any failure into an ApiError.
	 * @param {string} path Absolute path.
	 * @param {RequestInit} init Request options.
	 * @returns {Promise<any>} The decoded body.
	 * @throws {ApiError} When the request fails or the server rejects it.
	 */
	async send(path, init) {
		let response;
		try {
			response = await this.fetcher(path, init);
		} catch {
			throw new ApiError(0, "Cannot reach the server.");
		}
		if (response.status === Api.unauthorized) throw new ApiError(response.status, "Pair this device first.");
		/** @type {{detail?:unknown}|null} */
		let body;
		try {
			body = await response.json();
		} catch {
			body = null;
		}
		if (!response.ok) throw new ApiError(response.status, typeof body?.detail === "string" ? body.detail : `The server answered ${response.status}.`);
		return body;
	}
}
export {
	Api,
	ApiError
};
