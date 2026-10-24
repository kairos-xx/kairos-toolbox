"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
const {
	performance
} = require("node:perf_hooks");
const {
	Constants
} = require("./constants");
const {
	UpstreamError
} = require("./errors");
/**
 * @typedef {object} ProviderConfiguration
 * @property {string} openAIBaseUrl OpenAI-compatible API base URL.
 * @property {string} openAIModel Default OpenAI-compatible model.
 * @property {string} anthropicModel Default Anthropic model.
 * @typedef {object} UsageSummary
 * @property {number} requests Request count.
 * @property {number} inputTokens Prompt tokens consumed.
 * @property {number} outputTokens Completion tokens produced.
 * @property {number} latencyMs Last observed latency.
 * @typedef {object} UsageHistoryEntry
 * @property {number} inputTokens Prompt tokens consumed.
 * @property {number} outputTokens Completion tokens produced.
 * @property {number} latencyMs Observed latency.
 * @property {string} model Model used.
 * @property {number} createdAt Epoch milliseconds.
 * @typedef {object} ChatMessage
 * @property {string} role Message role.
 * @property {string} content Message content.
 */
/** Third-party AI provider usage and chat. */
class ProviderService {
	/**
	 * Creates an instance of ProviderService.
	 * @param {ProviderConfiguration} configuration Provider configuration.
	 * @param {(provider: string) => Promise<string | undefined>} secretProvider Secret lookup.
	 */
	constructor(configuration, secretProvider) {
		/** @type {ProviderConfiguration} */
		this.configuration = configuration;
		/** @type {(provider: string) => Promise<string | undefined>} */
		this.secretProvider = secretProvider;
		/** @type {Map<string, UsageSummary>} */
		this.usage = new Map();
		/** @type {Map<string, UsageHistoryEntry[]>} */
		this.history = new Map();
	}
	/**
	 * Providers.
	 * @returns {Promise<{id: string, label: string, configured: boolean, model: string, usage: UsageSummary, history: UsageHistoryEntry[]}[]>} Provider summaries.
	 */
	async providers() {
		const openAIKey = await this.secretProvider("openai-compatible");
		const anthropicKey = await this.secretProvider("anthropic");
		return [{
			id: "openai-compatible",
			label: "OpenAI compatible",
			configured: Boolean(openAIKey && this.configuration.openAIModel),
			model: this.configuration.openAIModel,
			usage: this.usage.get("openai-compatible") ?? ProviderService.emptyUsage(),
			history: this.history.get("openai-compatible") ?? []
		}, {
			id: "anthropic",
			label: "Anthropic",
			configured: Boolean(anthropicKey && this.configuration.anthropicModel),
			model: this.configuration.anthropicModel,
			usage: this.usage.get("anthropic") ?? ProviderService.emptyUsage(),
			history: this.history.get("anthropic") ?? []
		}];
	}
	static emptyUsage = () => ({
		requests: 0,
		inputTokens: 0,
		outputTokens: 0,
		latencyMs: 0
	});
	/**
	 * Adds usage.
	 * @param {string} provider Provider.
	 * @param {number} inputTokens InputTokens.
	 * @param {number} outputTokens OutputTokens.
	 * @param {number} latencyMs LatencyMs.
	 * @param {string} model Model.
	 */
	addUsage(provider, inputTokens, outputTokens, latencyMs, model) {
		const current = this.usage.get(provider) ?? ProviderService.emptyUsage();
		this.usage.set(provider, {
			requests: current.requests + 1,
			inputTokens: current.inputTokens + inputTokens,
			outputTokens: current.outputTokens + outputTokens,
			latencyMs
		});
		const history = this.history.get(provider) ?? [];
		history.unshift({
			inputTokens,
			outputTokens,
			latencyMs,
			model,
			createdAt: Date.now()
		});
		if (history.length > 40) history.length = 40;
		this.history.set(provider, history);
	}
	/**
	 * Chat.
	 * @param {string} provider Provider.
	 * @param {ChatMessage[]} messages Messages.
	 * @param {string} model Model.
	 * @returns {Promise<{text: string, model: string, usage: UsageSummary | undefined}>} Chat result.
	 */
	async chat(provider, messages, model = "") {
		const started = performance.now();
		return provider === "anthropic" ? this.chatAnthropic(messages, started, model) : this.chatOpenAI(messages, started, model);
	}
	/**
	 * Chat open ai.
	 * @param {ChatMessage[]} messages Messages.
	 * @param {number} started Started.
	 * @param {string} modelOverride ModelOverride.
	 * @returns {Promise<{text: string, model: string, usage: UsageSummary | undefined}>} Chat result.
	 * @throws {UpstreamError} When the operation cannot complete.
	 */
	async chatOpenAI(messages, started, modelOverride = "") {
		const token = await this.secretProvider("openai-compatible");
		const model = modelOverride || this.configuration.openAIModel;
		if (!token || !model) throw new UpstreamError("OpenAI compatible", 401, "Provider credentials or model are not configured in VS Code.");
		const response = await fetch(`${this.configuration.openAIBaseUrl.replace(/\/$/, "")}/chat/completions`, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				model,
				messages,
				temperature: 0.2
			}),
			signal: AbortSignal.timeout(Constants.requestTimeoutMs)
		});
		const payload = await ProviderService.parseResponse(response, "OpenAI compatible");
		this.addUsage("openai-compatible", Number(getAttribute( /** @type {Record<string, *>} */ (getAttribute(payload, "usage")), "prompt_tokens") ?? 0), Number(getAttribute( /** @type {Record<string, *>} */ (getAttribute(payload, "usage")), "completion_tokens") ?? 0), Math.round(performance.now() - started), String(getAttribute(payload, "model") ?? model));
		return {
			text: String(getAttribute(
				/** @type {Record<string, *>} */
				(Array.isArray(getAttribute(payload, "choices")) ? getAttribute(payload, "choices")[0] : {}), "message") ? getAttribute(
				/** @type {Record<string, *>} */
				(getAttribute(
					/** @type {Record<string, *>} */
					(Array.isArray(getAttribute(payload, "choices")) ? getAttribute(payload, "choices")[0] : {}), "message")), "content") ?? "" : ""),
			model: String(getAttribute(payload, "model") ?? model),
			usage: this.usage.get("openai-compatible")
		};
	}
	/**
	 * Chat anthropic.
	 * @param {ChatMessage[]} messages Messages.
	 * @param {number} started Started.
	 * @param {string} modelOverride ModelOverride.
	 * @returns {Promise<{text: string, model: string, usage: UsageSummary | undefined}>} Chat result.
	 * @throws {UpstreamError} When the operation cannot complete.
	 */
	async chatAnthropic(messages, started, modelOverride = "") {
		const token = await this.secretProvider("anthropic");
		const model = modelOverride || this.configuration.anthropicModel;
		if (!token || !model) throw new UpstreamError("Anthropic", 401, "Provider credentials or model are not configured in VS Code.");
		const system = messages.filter(message => message.role === "system").map(message => getAttribute(message, "content")).join("\n\n");
		const conversation = messages.filter(message => message.role !== "system");
		const response = await fetch("https://api.anthropic.com/v1/messages", {
			method: "POST",
			headers: {
				"x-api-key": token,
				"anthropic-version": "2023-06-01",
				"Content-Type": "application/json"
			},
			body: JSON.stringify({
				model,
				max_tokens: 4096,
				...(system ? {
					system
				} : {}),
				messages: conversation
			}),
			signal: AbortSignal.timeout(Constants.requestTimeoutMs)
		});
		const payload = await ProviderService.parseResponse(response, "Anthropic");
		this.addUsage("anthropic", Number(getAttribute( /** @type {Record<string, *>} */ (getAttribute(payload, "usage")), "input_tokens") ?? 0), Number(getAttribute( /** @type {Record<string, *>} */ (getAttribute(payload, "usage")), "output_tokens") ?? 0), Math.round(performance.now() - started), String(getAttribute(payload, "model") ?? model));
		const content = getAttribute(payload, "content");
		return {
			text: (Array.isArray(content) ? content : []).filter(item => getAttribute( /** @type {Record<string, *>} */ (item), "type") === "text").map(item => getAttribute( /** @type {Record<string, *>} */ (item), "text")).join("\n"),
			model: String(getAttribute(payload, "model") ?? model),
			usage: this.usage.get("anthropic")
		};
	}
	/**
	 * @param {Response} response Response.
	 * @param {string} service Service.
	 * @returns {Promise<Record<string, *>>} Parsed payload.
	 */
	static parseResponse = async (response, service) => {
		const text = await getAttribute(response, "text")();
		/** @type {Record<string, *>} */
		let payload;
		try {
			const parsed = text ? JSON.parse(text) : {};
			payload = parsed && typeof parsed === "object" ? parsed : {};
		} catch (error) {
			throw new UpstreamError(service, response.status, `${service} returned invalid JSON: ${error instanceof Error ? getAttribute(error, "message") : String(error)}`);
		}
		if (!response.ok) {
			const errorBody = getAttribute(payload, "error");
			const message = errorBody && typeof errorBody === "object" ? getAttribute(errorBody, "message") : getAttribute(payload, "message");
			throw new UpstreamError(service, response.status, String(message ?? text));
		}
		return payload;
	};
}
module.exports = {
	ProviderService
};
