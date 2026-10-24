/** Tests for the small client that talks to the dashboard's API. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	Api,
	ApiError
} from "../../../../tools/district/web/js/api.js";
/**
 * Build a fetch that answers once and remembers the request.
 * @param {number} status HTTP status.
 * @param {object|string} body Body to answer with.
 * @returns {{fetcher:typeof fetch,calls:{url:string,init:RequestInit|undefined}[]}} The fake and its log.
 * @throws {SyntaxError} When a string body is decoded as JSON.
 */
const answering = (status, body) => {
	/** @type {{url:string,init:RequestInit|undefined}[]} */
	const calls = [];
	return {
		calls,
		fetcher: /** @type {typeof fetch} */ (async (url, init) => {
			calls.push({
				url: String(url),
				init
			});
			return /** @type {Response} */ ({
				ok: status >= 200 && status < 300,
				status,
				json: async () => {
					if (typeof body === "string") throw new SyntaxError("not json");
					return body;
				}
			});
		})
	};
};
test("a GET returns the decoded body", async () => {
	const {
		fetcher,
		calls
	} = answering(200, {
		commands: []
	});
	const body = await new Api(fetcher).get("/api/commands");
	assert.deepEqual(body, {
		commands: []
	});
	assert.equal(calls[0]?.url, "/api/commands");
	assert.equal(calls[0]?.init?.method, "GET");
});
test("a POST sends its body as JSON and says so", async () => {
	const {
		fetcher,
		calls
	} = answering(200, {
		ok: true
	});
	await new Api(fetcher).post("/api/commands/sessions", {
		values: {
			kind: "own"
		}
	});
	assert.equal(calls[0]?.init?.method, "POST");
	assert.equal(calls[0]?.init?.body, JSON.stringify({
		values: {
			kind: "own"
		}
	}));
	assert.deepEqual(calls[0]?.init?.headers, {
		"Content-Type": "application/json"
	});
});
test("a failure carries the server's own words and its status", async () => {
	const {
		fetcher
	} = answering(409, {
		detail: "Stop a district needs your confirmation."
	});
	await assert.rejects(new Api(fetcher).post("/api/commands/kill", {}), error => {
		assert.ok(error instanceof ApiError);
		assert.equal(error.status, 409);
		assert.equal(error.message, "Stop a district needs your confirmation.");
		return true;
	});
});
test("an unpaired device is told to pair, whatever the body says", async () => {
	const {
		fetcher
	} = answering(401, {
		detail: "x"
	});
	await assert.rejects(new Api(fetcher).get("/api/host"), error => {
		assert.ok(error instanceof ApiError);
		assert.equal(error.status, 401);
		assert.equal(error.message, "Pair this device first.");
		return true;
	});
});
test("a body that is not JSON still gives a readable failure", async () => {
	const {
		fetcher
	} = answering(502, "<html>bad gateway</html>");
	await assert.rejects(new Api(fetcher).get("/api/host"), error => {
		assert.ok(error instanceof ApiError);
		assert.equal(error.message, "The server answered 502.");
		return true;
	});
});
test("a network failure becomes a failure a person can read", async () => {
	const fetcher = /** @type {typeof fetch} */ (async () => {
		throw new TypeError("Failed to fetch");
	});
	await assert.rejects(new Api(fetcher).get("/api/host"), error => {
		assert.ok(error instanceof ApiError);
		assert.equal(error.status, 0);
		assert.equal(error.message, "Cannot reach the server.");
		return true;
	});
});
