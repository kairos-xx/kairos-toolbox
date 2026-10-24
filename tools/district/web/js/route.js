/**
 * Where the dashboard is: one of its screens, the agent or session it is
 * about, and the filters in force. All of it lives in the URL hash, so every
 * screen is a plain link that can be shared, bookmarked and returned to.
 */
import {
	Filters
} from "./filters.js";
/**
 * @typedef {{view:string,id:string,scope:import("./filters.js").Scope}} Place
 */
/** Parse and serialize dashboard locations stored in the URL hash. */
class Route {
	/** Screens, in tab-bar order, then the two that are about one thing. */
	static views = ["overview", "sessions", "activity", "commands", "agent", "session"];
	/** Screens that are about one agent or one session and so need an id. */
	static subjects = ["agent", "session"];
	/**
	 * Read a place from a URL hash such as `#/sessions?agent=devin&state=live`.
	 * @param {string} hash Location hash.
	 * @returns {Place} The screen, its subject and its filters; unknown or incomplete places fall back to the overview.
	 */
	static parse = hash => {
		const raw = hash.replace(/^#\/?/u, "");
		const split = raw.indexOf("?");
		const path = split < 0 ? raw : raw.slice(0, split);
		const scope = Filters.parse(split < 0 ? "" : raw.slice(split + 1));
		const [name = "", ...rest] = path.split("/");
		const encoded = rest.join("/");
		let id;
		try {
			id = decodeURIComponent(encoded);
		} catch {
			id = "";
		}
		if (name === "" || !Route.views.includes(name)) return {
			view: "overview",
			id: "",
			scope
		};
		if (Route.subjects.includes(name) && id === "") return {
			view: "overview",
			id: "",
			scope
		};
		return {
			view: name,
			id: Route.subjects.includes(name) ? id : "",
			scope
		};
	};
	/**
	 * Write a place as a URL hash.
	 * @param {Place} place Screen, subject and filters.
	 * @returns {string} `#/view/id?filters`.
	 */
	static format = place => {
		const path = place.view === "overview" ? "" : `${place.view}${place.id === "" ? "" : `/${encodeURIComponent(place.id)}`}`;
		const query = Filters.query(place.scope);
		return `#/${path}${query === "" ? "" : `?${query}`}`;
	};
	/**
	 * Link to a screen.
	 * @param {string} view Screen name.
	 * @param {string} [id] Agent or session, for the screens about one.
	 * @param {import("./filters.js").Scope} [scope] Filters to carry over.
	 * @returns {string} Hash to link to.
	 */
	static href = (view, id = "", scope = {}) => Route.format({
		view,
		id,
		scope
	});
	/**
	 * Filters that apply on a screen: its own filters plus the thing it is about.
	 * @param {Place} place Current place.
	 * @returns {import("./filters.js").Scope} Scope for matching and totals.
	 */
	static effective = place => ({
		...place.scope,
		...(place.view === "agent" ? {
			agent: place.id
		} : {}),
		...(place.view === "session" ? {
			session: place.id
		} : {})
	});
}
export {
	Route
};
