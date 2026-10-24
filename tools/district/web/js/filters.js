/**
 * The dashboard's scope: one set of filters shared by the stats, the sessions,
 * the activity feed and the command history, and kept in the URL hash so that
 * every agent, session id and stat can be a plain link to a filtered view.
 */
/**
 * @typedef {{q?:string,state?:string,agent?:string,session?:string,clients?:string,sort?:string}} Scope
 */
/**
 * @typedef {{id:string,agent:string,agentSession:string,state:string,command:string,cwd:string,workspace:string,clients:number,cpuPercent:number,rssBytes:number,processCount:number}} ScopedSession
 */
/**
 * @typedef {{agent:string,session_id:string,command:string,state?:string,event_type?:string}} ScopedRecord
 */
/** Parse, normalize, and apply dashboard filter scopes. */
class Filters {
	/** Filters shown as removable chips, in display order. */
	static chips = ["agent", "session", "state", "clients", "q"];
	/** Every key the hash may carry. */
	static keys = ["q", "state", "agent", "session", "clients", "sort"];
	/** Sort that applies when none is chosen. */
	static defaultSort = "newest";
	/** State value that stands for "not idle and not dead". */
	static live = "live";
	/**
	 * Read a scope from a URL hash.
	 * @param {string} hash Hash such as `#agent=devin&state=live`.
	 * @returns {Scope} Known, non-empty keys only.
	 */
	static parse = hash => {
		/** @type {Record<string,string>} */
		const scope = {};
		const params = new URLSearchParams(hash.replace(/^#/u, ""));
		Filters.keys.forEach(key => {
			const value = (params.get(key) ?? "").trim();
			if (value !== "" && !(key === "sort" && value === Filters.defaultSort)) scope[key] = value;
		});
		return scope;
	};
	/**
	 * Write a scope as a query string.
	 * @param {Scope} scope Filters.
	 * @returns {string} `key=value&...`, empty when nothing is set.
	 */
	static query = scope => {
		const params = new URLSearchParams();
		const values = /** @type {Record<string,string|undefined>} */ (scope);
		Filters.keys.forEach(key => {
			const value = values[key];
			if (value !== undefined && value !== "") params.set(key, value);
		});
		return params.toString();
	};
	/**
	 * Write a scope as a URL hash.
	 * @param {Scope} scope Filters.
	 * @returns {string} `#key=value&...`, or `#` when nothing is set.
	 */
	static format = scope => `#${Filters.query(scope)}`;
	/**
	 * Link to the current scope with some filters changed.
	 * @param {Scope} scope Current filters.
	 * @param {Record<string,string>} patch Keys to set; an empty string removes one.
	 * @returns {string} Hash to link to.
	 */
	static link = (scope, patch) => Filters.format(Filters.parse(Filters.format({
		...scope,
		...patch
	})));
	/**
	 * @param {Scope} scope Filters.
	 * @returns {boolean} Whether anything but the sort order narrows the view.
	 */
	static narrowed = scope => Filters.chips.some(key => /** @type {Record<string,string|undefined>} */ (scope)[key] !== undefined);
	/**
	 * Name an agent the way every list shows it.
	 * @param {{agent?:string}} item Session or record.
	 * @returns {string} Agent name, `shell` when unset.
	 */
	static agent = item => item.agent || "shell";
	/**
	 * @param {ScopedSession} session Session.
	 * @param {Scope} scope Filters.
	 * @returns {boolean} Whether the session is in scope.
	 */
	static session = (session, scope) => {
		if (scope.agent !== undefined && Filters.agent(session) !== scope.agent) return false;
		if (scope.session !== undefined && session.id !== scope.session) return false;
		if (scope.clients !== undefined && session.clients < 1) return false;
		if (scope.state !== undefined && !Filters.stateMatches(session.state, scope.state)) return false;
		return Filters.search([session.id, session.agent, session.agentSession, session.cwd, session.command, session.workspace, session.state], scope.q);
	};
	/**
	 * @param {string} state Session state.
	 * @param {string} wanted Filter value.
	 * @returns {boolean} Whether the state satisfies the filter.
	 */
	static stateMatches = (state, wanted) => wanted === Filters.live ? state !== "IDLE" && state !== "DEAD" : state === wanted;
	/**
	 * Activity events and commands are scoped by who ran them and by the search text.
	 * @param {ScopedRecord} record Event or command record.
	 * @param {Scope} scope Filters.
	 * @returns {boolean} Whether the record is in scope.
	 */
	static record = (record, scope) => {
		if (scope.agent !== undefined && Filters.agent(record) !== scope.agent) return false;
		if (scope.session !== undefined && record.session_id !== scope.session) return false;
		return Filters.search([record.session_id, record.agent, record.command, record.state ?? "", record.event_type ?? ""], scope.q);
	};
	/**
	 * @param {string[]} fields Searchable text.
	 * @param {string|undefined} query Search text; every word must appear.
	 * @returns {boolean} Whether the fields match.
	 */
	static search = (fields, query) => {
		if (query === undefined) return true;
		const haystack = fields.join(" ").toLowerCase();
		return query.toLowerCase().split(/\s+/u).every(word => haystack.includes(word));
	};
	/**
	 * Fleet totals for a set of sessions, computed the way the server does.
	 * @param {ScopedSession[]} sessions Sessions in scope.
	 * @returns {{sessions:number,active:number,idle:number,dead:number,cpuPercent:number,rssBytes:number,processes:number,clients:number}} Totals.
	 */
	static totals = sessions => ({
		sessions: sessions.length,
		active: sessions.filter(session => session.state !== "IDLE" && session.state !== "DEAD").length,
		idle: sessions.filter(session => session.state === "IDLE").length,
		dead: sessions.filter(session => session.state === "DEAD").length,
		cpuPercent: Math.round(sessions.reduce((sum, session) => sum + session.cpuPercent, 0) * 10) / 10,
		rssBytes: sessions.reduce((sum, session) => sum + session.rssBytes, 0),
		processes: sessions.reduce((sum, session) => sum + session.processCount, 0),
		clients: sessions.reduce((sum, session) => sum + session.clients, 0)
	});
	/**
	 * Distinct agent names, for the agent filter.
	 * @param {{agent?:string}[]} sessions Sessions.
	 * @returns {string[]} Sorted names.
	 */
	static agents = sessions => [...new Set(sessions.map(Filters.agent))].sort((a, b) => a.localeCompare(b));
	/**
	 * Human wording for a chip.
	 * @param {string} key Chip key.
	 * @param {string} value Filter value.
	 * @returns {string} Label such as `agent devin`.
	 */
	static label = (key, value) => {
		const names = /** @type {Record<string,string>} */ ({
			agent: "agent",
			session: "session",
			state: "state",
			clients: "attached",
			q: "search"
		});
		if (key === "clients") return "attached only";
		return `${names[key] ?? key} ${key === "state" && value === Filters.live ? "live" : value}`;
	};
}
export {
	Filters
};
