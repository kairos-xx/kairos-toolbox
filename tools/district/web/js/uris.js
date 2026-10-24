/**
 * The URI screen. A district URI is a signed, expiring, single-use ticket that
 * runs one district command with fixed input, so a tap, a scan or a pasted link
 * can do exactly one thing. Here a person issues them, sees which still work,
 * copies, runs or revokes them, and redeems one that was sent to them.
 */
import {
	Forms
} from "./forms.js";
import {
	Kit
} from "./kit.js";
import {
	Sheet
} from "./sheet.js";
import {
	Widgets
} from "./widgets.js";
/**
 * @typedef {{id:string,uri:string,command:string,title:string,values:Record<string,string>,destructive:boolean,createdAt:string,expiresAt:string,state:string}} Ticket
 */
/** Draw the URI screen and talk to the server about tickets. */
class UriScreen {
	/** Milliseconds between refreshes while the screen is open. */
	static every = 5000;
	/** What a ticket's state is called. */
	static states = {
		active: "Active",
		used: "Used",
		expired: "Expired",
		revoked: "Revoked"
	};
	/** @type {[string, string][]} Lifetimes a ticket can be given, in seconds. */
	static lifetimes = [
		["600", "10 minutes"],
		["3600", "1 hour"],
		["86400", "1 day"],
		["604800", "7 days"]
	];
	/**
	 * @param {HTMLElement} root The screen to draw into.
	 * @param {import("./api.js").Api} api How to reach the server.
	 * @param {() => number} clock Current time in milliseconds.
	 * @param {(text:string) => Promise<boolean>} copier How to copy text.
	 */
	constructor(root, api, clock, copier) {
		this.api = api;
		this.clock = clock;
		this.copier = copier;
		/** @type {import("./palette.js").CommandSpec[]|null} */
		this.catalog = null;
		/** @type {import("./forms.js").District[]} */
		this.lakes = [];
		this.last = Number.NEGATIVE_INFINITY;
		const bar = Kit.el("div", "uri-bar");
		const create = Kit.el("button", "uri-new", "New URI");
		create.setAttribute("type", "button");
		create.setAttribute("data-tone", String("primary"));
		create.addEventListener("click", () => {
			this.create();
		});
		const paste = Kit.el("button", "uri-paste", "Paste a URI");
		paste.setAttribute("type", "button");
		paste.addEventListener("click", () => this.paste());
		bar.append(create, paste);
		this.list = Kit.el("ol", "uri-list");
		this.result = Kit.el("section", "uri-result");
		this.result.setAttribute("aria-label", "Result");
		this.result.setAttribute("aria-live", "polite");
		root.append(bar, this.result, this.list);
		this.sheet = new Sheet(root);
	}
	/**
	 * Refresh the list when the screen is watched, but not on every redraw.
	 * @returns {Promise<void>} Settles when the list is current.
	 */
	show() {
		return this.refresh(false);
	}
	/**
	 * Read the tickets and draw them.
	 * @param {boolean} force Read even if the list was read a moment ago.
	 * @returns {Promise<void>} Settles when the list is drawn.
	 */
	async refresh(force) {
		const moment = this.clock();
		if (!force && moment - this.last < UriScreen.every) return;
		this.last = moment;
		try {
			const body = await this.api.get("/api/uris");
			this.draw(/** @type {Ticket[]} */ (body.items));
		} catch (failure) {
			this.list.replaceChildren(UriScreen.none(failure instanceof Error ? failure.message : "Could not read the URIs."));
		}
	}
	/**
	 * Draw the tickets, or invite the first one.
	 * @param {Ticket[]} tickets Tickets, newest first.
	 * @returns {void}
	 */
	draw(tickets) {
		if (tickets.length === 0) {
			this.list.replaceChildren(UriScreen.none("No URIs yet. Create one to share a one-tap action."));
			return;
		}
		this.list.replaceChildren(...tickets.map(ticket => this.card(ticket)));
	}
	/**
	 * A message in place of the list.
	 * @param {string} message What to say.
	 * @returns {HTMLElement} The message.
	 */
	static none = message => {
		const item = Kit.el("li", "uri-none", message);
		return item;
	};
	/**
	 * One ticket as a card.
	 * @param {Ticket} ticket The ticket.
	 * @returns {HTMLElement} Its card.
	 */
	card(ticket) {
		const card = Kit.el("li", "uri-card");
		card.setAttribute("data-state", String(ticket.state));
		const head = Kit.el("div", "uri-head");
		head.append(Kit.el("h3", "uri-name", ticket.title));
		const state = Kit.el("span", "uri-state", UriScreen.states[/** @type {keyof typeof UriScreen.states} */ (ticket.state)] ?? ticket.state);
		state.setAttribute("data-state", String(ticket.state));
		head.append(state);
		card.append(head);
		const given = Object.entries(ticket.values).filter(([, value]) => value !== "").map(([key, value]) => `${key} ${value}`).join(" · ");
		if (given !== "") card.append(Kit.el("p", "uri-values", given));
		if (ticket.state === "active") card.append(Kit.el("p", "uri-when", `expires ${Kit.ago(ticket.expiresAt, this.clock())}`));
		if (ticket.state === "expired") card.append(Kit.el("p", "uri-when", `expired ${Kit.ago(ticket.expiresAt, this.clock())}`));
		card.append(Kit.el("code", "uri-text", UriScreen.short(ticket.uri)));
		const actions = Kit.el("div", "uri-actions");
		const copy = UriScreen.action("Copy");
		copy.addEventListener("click", () => {
			this.copy(ticket, card, copy);
		});
		actions.append(copy);
		if (ticket.state === "active") {
			const run = UriScreen.action("Run");
			run.addEventListener("click", () => {
				this.run(ticket);
			});
			const revoke = UriScreen.action("Revoke");
			revoke.addEventListener("click", () => {
				this.revoke(ticket);
			});
			actions.append(run, revoke);
		}
		card.append(actions);
		return card;
	}
	/**
	 * A small button.
	 * @param {string} label Its words.
	 * @returns {HTMLButtonElement} The button.
	 */
	static action = label => {
		const button = /** @type {HTMLButtonElement} */ (Kit.el("button", "uri-action", label));
		button.setAttribute("type", "button");
		return button;
	};
	/**
	 * Shorten a URI to the start and end that tell tickets apart.
	 * @param {string} uri The URI.
	 * @returns {string} A form short enough for a phone.
	 */
	static short = uri => uri.length <= 56 ? uri : `${uri.slice(0, 40)}…${uri.slice(-10)}`;
	/**
	 * Copy a ticket's URI, or show it whole so it can be selected by hand.
	 * @param {Ticket} ticket The ticket.
	 * @param {HTMLElement} card Its card.
	 * @param {HTMLButtonElement} button The Copy button.
	 * @returns {Promise<void>} Settles when the outcome is shown.
	 */
	async copy(ticket, card, button) {
		if (await this.copier(ticket.uri)) {
			button.textContent = "Copied";
			return;
		}
		if (card.querySelector(".uri-full") === null) card.append(Kit.el("code", "uri-full", ticket.uri));
		button.textContent = "Select and copy";
	}
	/**
	 * Run a ticket and show what it produced.
	 * @param {Ticket} ticket The ticket.
	 * @returns {Promise<void>} Settles when the result is shown.
	 */
	async run(ticket) {
		await this.outcome(() => this.api.post(`/api/uris/${ticket.id}/run`, {}));
		await this.refresh(true);
	}
	/**
	 * Cancel a ticket.
	 * @param {Ticket} ticket The ticket.
	 * @returns {Promise<void>} Settles when the list is current.
	 */
	async revoke(ticket) {
		await this.outcome(() => this.api.post(`/api/uris/${ticket.id}/revoke`, {}), false);
		await this.refresh(true);
	}
	/**
	 * Show a command result, or why there is none.
	 * @param {() => Promise<import("./widgets.js").CommandResult>} work The request.
	 * @param {boolean} [display] Whether a successful answer is a result to show.
	 * @returns {Promise<void>} Settles when shown.
	 */
	async outcome(work, display = true) {
		try {
			const answer = await work();
			if (display) Widgets.render(this.result, answer, this.clock());
		} catch (failure) {
			const notice = Kit.el("p", "w-notice", failure instanceof Error ? failure.message : "That did not work.");
			notice.setAttribute("data-tone", String("bad"));
			notice.setAttribute("role", "status");
			this.result.replaceChildren(notice);
		}
	}
	/**
	 * Open the sheet that issues a ticket.
	 * @returns {Promise<void>} Settles when the sheet is open.
	 * @throws {Error} When required or destructive input is not confirmed.
	 */
	async create() {
		if (this.catalog === null)
			try {
				this.catalog = (await this.api.get("/api/commands")).commands;
			} catch (failure) {
				await this.outcome(() => Promise.reject(failure));
				return;
			}
		const commands = /** @type {import("./palette.js").CommandSpec[]} */ (this.catalog);
		const body = Kit.el("div", "u-body");
		const picker = /** @type {HTMLInputElement} */ (Kit.el("select", "f-input u-command"));
		picker.setAttribute("name", "command");
		commands.forEach(spec => {
			const option = Kit.el("option", "", spec.title);
			option.setAttribute("value", spec.name);
			picker.append(option);
		});
		picker.value = commands[0]?.name ?? "";
		const choose = Kit.el("label", "f-field");
		choose.append(Kit.el("span", "f-name", "Does"), picker);
		const params = Kit.el("div", "u-params");
		const life = /** @type {HTMLInputElement} */ (Kit.el("select", "f-input u-ttl"));
		life.setAttribute("name", "ttl");
		UriScreen.lifetimes.forEach(([seconds, text]) => {
			const option = Kit.el("option", "", text);
			option.setAttribute("value", seconds);
			life.append(option);
		});
		life.value = UriScreen.lifetimes[0]?.[0] ?? "";
		const lifetime = Kit.el("label", "f-field");
		lifetime.append(Kit.el("span", "f-name", "Works for"), life);
		const confirm = Kit.el("label", "f-field f-flag u-confirm");
		const tick = /** @type {HTMLInputElement} */ (Kit.el("input", "f-input"));
		tick.setAttribute("type", "checkbox");
		confirm.append(tick, Kit.el("span", "f-name", "I understand this runs without asking again."));
		confirm.hidden = true;
		/** @type {import("./forms.js").Built} */
		let built = Forms.build([], this.lakes);
		const rebuild = () => {
			const spec = /** @type {import("./palette.js").CommandSpec} */ (commands.find(candidate => candidate.name === picker.value));
			built = Forms.build(spec.params, this.lakes);
			params.replaceChildren(built.form);
			confirm.hidden = spec.danger !== "destructive";
			tick.checked = false;
		};
		picker.addEventListener("change", rebuild);
		rebuild();
		body.append(choose, params, lifetime, confirm);
		this.sheet.open({
			title: "New URI",
			summary: "A signed link that runs one command, once, until it expires.",
			body,
			actions: [{
				label: "Cancel",
				tone: "quiet",
				run: null
			}, {
				label: "Create URI",
				tone: "primary",
				run: async () => {
					const spec = /** @type {import("./palette.js").CommandSpec} */ (commands.find(candidate => candidate.name === picker.value));
					const values = built.read();
					const missing = Forms.missing(spec.params, values);
					if (missing !== "") throw new Error(`${missing} is required.`);
					if (spec.danger === "destructive" && !tick.checked) throw new Error("Tick the box to confirm.");
					await this.api.post("/api/uris", {
						command: spec.name,
						values,
						ttlSeconds: Number(life.value),
						confirmed: tick.checked
					});
					await this.refresh(true);
				}
			}],
			onClose: () => {}
		});
	}
	/**
	 * Open the sheet that redeems a URI sent by someone.
	 * @returns {void}
	 * @throws {Error} When no URI is provided for redemption.
	 */
	paste() {
		const field = /** @type {HTMLInputElement} */ (Kit.el("textarea", "f-input u-paste"));
		field.setAttribute("name", "uri");
		field.setAttribute("rows", "4");
		field.setAttribute("spellcheck", "false");
		field.setAttribute("autocapitalize", "off");
		const label = Kit.el("label", "f-field");
		label.append(Kit.el("span", "f-name", "URI"), field);
		this.sheet.open({
			title: "Paste a URI",
			summary: "Check its signature and run it. A URI works once.",
			body: label,
			actions: [{
				label: "Cancel",
				tone: "quiet",
				run: null
			}, {
				label: "Redeem",
				tone: "primary",
				run: async () => {
					const uri = field.value.trim();
					if (uri === "") throw new Error("Paste a URI first.");
					Widgets.render(this.result, await this.api.post("/api/uris/redeem", {
						uri
					}), this.clock());
					await this.refresh(true);
				}
			}],
			onClose: () => {}
		});
	}
}
export {
	UriScreen
};
