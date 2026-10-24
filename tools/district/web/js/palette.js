/**
 * The command palette: every district command, grouped by what it is for, each one
 * a tap away. A command with nothing to fill in runs at once; the others ask
 * for their input in a sheet. Results appear as widgets, not JSON.
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
 * @typedef {{name:string,title:string,summary:string,group:string,danger:string,params:import("./forms.js").Param[]}} CommandSpec
 */
/** Draw the palette and run its commands. */
class Palette {
	/** How many results stay on screen. */
	static keep = 6;
	/** What each group of commands is called. */
	static titles = {
		observe: "Look around",
		act: "Work with lakes",
		system: "This installation"
	};
	/**
	 * @param {HTMLElement} root The screen to draw into.
	 * @param {import("./api.js").Api} api How to reach the server.
	 * @param {() => number} clock Current time in milliseconds.
	 */
	constructor(root, api, clock) {
		this.api = api;
		this.clock = clock;
		/** @type {CommandSpec[]} */
		this.catalog = [];
		/** @type {import("./forms.js").District[]} */
		this.lakes = [];
		this.query = "";
		this.opened = "";
		this.onClose = () => {};
		this.search = /** @type {HTMLInputElement} */ (Kit.el("input", "cmd-search"));
		this.search.setAttribute("type", "search");
		this.search.setAttribute("placeholder", "Find a command");
		this.search.setAttribute("aria-label", "Find a command");
		this.search.setAttribute("autocomplete", "off");
		this.search.addEventListener("input", () => {
			this.query = this.search.value.trim();
			this.drawGroups();
		});
		this.groups = Kit.el("div", "cmd-groups");
		this.results = Kit.el("section", "run-results");
		this.results.setAttribute("aria-label", "Results");
		root.append(Kit.el("div", "cmd-bar"), this.groups, this.results);
		/** @type {HTMLElement} */ (root.querySelector(".cmd-bar")).append(this.search);
		this.sheet = new Sheet(root);
	}
	/**
	 * Fetch the commands and draw them.
	 * @returns {Promise<void>} Settles when the screen shows commands or why it cannot.
	 */
	async load() {
		try {
			const body = await this.api.get("/api/commands");
			this.catalog = body.commands;
			this.drawGroups();
		} catch (failure) {
			const box = Kit.el("div", "cmd-error");
			const notice = Kit.el("p", "w-notice", failure instanceof Error ? failure.message : "Could not load the commands.");
			notice.setAttribute("data-tone", String("bad"));
			const retry = Kit.el("button", "cmd-retry", "Try again");
			retry.setAttribute("type", "button");
			retry.addEventListener("click", () => {
				this.load();
			});
			box.append(notice, retry);
			this.groups.replaceChildren(box);
		}
	}
	/**
	 * Follow the address: remember the live lakes, and open the command it names once.
	 * @param {string} name Command named in the address, or empty.
	 * @param {import("./forms.js").District[]} lakes Live lakes.
	 * @returns {void}
	 */
	show(name, lakes) {
		this.lakes = lakes;
		if (name === "") {
			this.opened = "";
			return;
		}
		if (name === this.opened) return;
		this.opened = name;
		const found = this.catalog.find(spec => spec.name === name);
		if (found !== undefined) this.choose(found);
	}
	/**
	 * Draw the command tiles that match the search, under their headings.
	 * @returns {void}
	 */
	drawGroups() {
		const words = this.query.toLowerCase().split(/\s+/u).filter(Boolean);
		const matching = this.catalog.filter(spec => {
			const hay = `${spec.title} ${spec.summary} ${spec.name}`.toLowerCase();
			return words.every(word => hay.includes(word));
		});
		if (matching.length === 0) {
			this.groups.replaceChildren(Kit.el("p", "cmd-none", `No command matches “${this.query}”.`));
			return;
		}
		this.groups.replaceChildren(...Object.entries(Palette.titles).flatMap(([group, title]) => {
			const members = matching.filter(spec => spec.group === group);
			if (members.length === 0) return [];
			const section = Kit.el("section", "cmd-group");
			const list = Kit.el("ul", "cmd-list");
			members.forEach(spec => {
				const item = Kit.el("li");
				item.append(this.tile(spec));
				list.append(item);
			});
			section.append(Kit.el("h2", "cmd-group-title", title), list);
			return [section];
		}));
	}
	/**
	 * One command as a button.
	 * @param {CommandSpec} spec The command.
	 * @returns {HTMLButtonElement} Its tile.
	 */
	tile(spec) {
		const button = /** @type {HTMLButtonElement} */ (Kit.el("button", "cmd"));
		button.setAttribute("type", "button");
		button.setAttribute("data-danger", String(spec.danger));
		button.append(Kit.el("span", "cmd-title", spec.title), Kit.el("span", "cmd-summary", spec.summary));
		if (spec.danger === "destructive") button.append(Kit.el("span", "cmd-flag", "asks first"));
		button.addEventListener("click", () => this.choose(spec));
		return button;
	}
	/**
	 * Act on a chosen command: run it, or ask for what it needs.
	 * @param {CommandSpec} spec The command.
	 * @returns {void}
	 * @throws {Error} When a required command value is missing.
	 */
	choose(spec) {
		if (spec.params.length === 0 && spec.danger === "safe") {
			this.direct(spec, {}, null);
			return;
		}
		const destructive = spec.danger === "destructive";
		const built = Forms.build(spec.params, this.lakes);
		this.sheet.open({
			title: spec.title,
			summary: destructive ? `${spec.summary} This cannot be undone.` : spec.summary,
			body: built.form,
			actions: [{
				label: "Cancel",
				tone: "quiet",
				run: null
			}, {
				label: destructive ? spec.title : "Run",
				tone: destructive ? "danger" : "primary",
				run: async () => {
					const values = built.read();
					const missing = Forms.missing(spec.params, values);
					if (missing !== "") throw new Error(`${missing} is required.`);
					await this.run(spec, values, destructive);
				}
			}],
			onClose: () => this.onClose()
		});
	}
	/**
	 * Run a command from the sheet; a failure goes back to the sheet.
	 * @param {CommandSpec} spec The command.
	 * @param {Record<string,string>} values Its input.
	 * @param {boolean} confirmed Whether the person confirmed a destructive command.
	 * @returns {Promise<void>} Settles when the result is shown.
	 */
	async run(spec, values, confirmed) {
		const card = this.card(spec, values);
		try {
			this.fill(card, await this.post(spec, values, confirmed));
		} catch (failure) {
			card.remove();
			throw failure;
		}
	}
	/**
	 * Run a command with no sheet; a failure is shown on its card.
	 * @param {CommandSpec} spec The command.
	 * @param {Record<string,string>} values Its input.
	 * @param {HTMLElement|null} reuse A card to refresh instead of adding one.
	 * @returns {Promise<void>} Settles when the card shows the outcome.
	 */
	async direct(spec, values, reuse) {
		const card = reuse ?? this.card(spec, values);
		Palette.busy(card);
		try {
			this.fill(card, await this.post(spec, values, false));
		} catch (failure) {
			Palette.fail(card, failure instanceof Error ? failure.message : "The command failed.");
		}
	}
	/**
	 * Ask the server to run a command.
	 * @param {CommandSpec} spec The command.
	 * @param {Record<string,string>} values Its input.
	 * @param {boolean} confirmed Whether a destructive command was confirmed.
	 * @returns {Promise<import("./widgets.js").CommandResult>} What it produced.
	 */
	post(spec, values, confirmed) {
		return this.api.post(`/api/commands/${spec.name}`, {
			values,
			confirmed
		});
	}
	/**
	 * Add a card for a command at the top of the results.
	 * @param {CommandSpec} spec The command.
	 * @param {Record<string,string>} values Its input.
	 * @returns {HTMLElement} The card, waiting for a result.
	 */
	card(spec, values) {
		const card = Kit.el("article", "run-card");
		const head = Kit.el("header", "run-head");
		const given = Object.entries(values).filter(([, value]) => value !== "").map(([key, value]) => `${key} ${value}`).join(" · ");
		const names = Kit.el("div", "run-names");
		names.append(Kit.el("h3", "run-name", spec.title));
		if (given !== "") names.append(Kit.el("p", "run-given", given));
		const again = Kit.el("button", "run-again", "Run again");
		again.setAttribute("type", "button");
		again.addEventListener("click", () => {
			if (spec.danger === "destructive") this.choose(spec);
			else this.direct(spec, values, card);
		});
		const dismiss = Kit.el("button", "run-dismiss", "Dismiss");
		dismiss.setAttribute("type", "button");
		dismiss.addEventListener("click", () => card.remove());
		const actions = Kit.el("div", "run-actions");
		actions.append(again, dismiss);
		head.append(names, actions);
		card.append(head, Kit.el("div", "run-body"));
		this.results.insertBefore(card, this.results.children[0] ?? null);
		Array.from(this.results.children).slice(Palette.keep).forEach(old => {
			old.remove();
		});
		return card;
	}
	/**
	 * Show that a card is waiting for its result.
	 * @param {HTMLElement} card The card.
	 * @returns {void}
	 */
	static busy = card => {
		card.setAttribute("aria-busy", "true");
		const body = /** @type {HTMLElement} */ (card.querySelector(".run-body"));
		body.replaceChildren(Palette.notice("neutral", "Running…"));
	};
	/**
	 * Show a result on a card.
	 * @param {HTMLElement} card The card.
	 * @param {import("./widgets.js").CommandResult} result The result.
	 * @returns {void}
	 */
	fill(card, result) {
		card.removeAttribute("aria-busy");
		Widgets.render(/** @type {HTMLElement} */ (card.querySelector(".run-body")), result, this.clock());
	}
	/**
	 * Show why a command did not run.
	 * @param {HTMLElement} card The card.
	 * @param {string} message What went wrong.
	 * @returns {void}
	 */
	static fail = (card, message) => {
		card.removeAttribute("aria-busy");
		/** @type {HTMLElement} */ (card.querySelector(".run-body")).replaceChildren(Palette.notice("bad", message));
	};
	/**
	 * A one-line message.
	 * @param {string} tone Emphasis.
	 * @param {string} message The text.
	 * @returns {HTMLElement} The message.
	 */
	static notice = (tone, message) => {
		const notice = Kit.el("p", "w-notice", message);
		notice.setAttribute("data-tone", String(tone));
		notice.setAttribute("role", "status");
		return notice;
	};
}
export {
	Palette
};
