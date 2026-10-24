/**
 * Live District Terminal dashboard with a stateful ANSI terminal emulator and observability widgets.
 */
import {
	AnsiTerminal
} from "./ansi.js";
import {
	Filters
} from "./filters.js";
import {
	Route
} from "./route.js";
/**
 * @typedef {{pid:number,parentPid:number,name:string,command:string,cpuPercent:number,rssBytes:number,readBytes:number,writeBytes:number,ports:number[]}} ProcessSnapshot
 */
/**
 * @typedef {{id:string,agent:string,agentSession:string,agentState:string,state:string,command:string,cwd:string,surface:string,editor:string,backend:string,workspace:string,attribution:string,hostId:string,output:string,clients:number,ageSeconds:number,lastActivityAt:string,reconnectable:boolean,processCount:number,cpuPercent:number,rssBytes:number,vmsBytes:number,readBytes:number,writeBytes:number,ports:number[],connections:string[],networkBytesSupported:boolean,partialResources:boolean,processes:ProcessSnapshot[],cpuHistory:number[],rssHistory:number[]}} SessionSnapshot
 */
/**
 * @typedef {{mode:string,consumers:number,version:string,pid:number,startedAt:string,uptimeSeconds:number,sampleMs:number}} ServerMeta
 */
/**
 * @typedef {{sessions:number,active:number,idle:number,dead:number,cpuPercent:number,rssBytes:number,processes:number,clients:number}} FleetTotals
 */
/**
 * @typedef {{id:string,event_type:string,occurred_at:string,agent:string,agent_session:string,session_id:string,state:string,command:string,exit_code:number|null,duration_seconds:number}} ActivityEvent
 */
/**
 * @typedef {{command_id:string,session_id:string,agent:string,command:string,started_at:string,finished_at:string,exit_code:number|null,duration_seconds:number|null}} CommandRecord
 */
/**
 * @typedef {{sessions:SessionSnapshot[],server:ServerMeta,totals:FleetTotals,events:ActivityEvent[],commands:CommandRecord[]}} DashboardPayload
 */
/**
   * @typedef {{card:HTMLElement,terminal:AnsiTerminal}} SessionView

   * @typedef {{level?:string,kind?:string,state?:string,exit?:string,connected?:string}} CardData

   * @typedef {(a:SessionSnapshot,b:SessionSnapshot)=>number} RankFn
   */
/**
 * Represent Dom.
 */
class Dom {
	/**
	 * Query one required element.
	 * @template {Element} T
	 * @param {string} selector Selector.
	 * @param {ParentNode} [root] Root.
	 * @returns {T} Element.
	 */
	static one = (selector, root = document) => {
		const element = root.querySelector(selector);
		if (element === null) throw new Error(`Missing dashboard element: ${selector}`);
		return /** @type {T} */ (element);
	};
}
/**
 * Represent Format.
 */
class Format {
	/**
	 * Format a duration in seconds.
	 * @param {number} seconds Seconds.
	 * @returns {string} Compact duration.
	 */
	static age = seconds => {
		if (seconds < 60) return `${Math.floor(seconds)}s`;
		if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
		const hours = Math.floor(seconds / 3600);
		return `${hours}h ${Math.floor((seconds % 3600) / 60)}m`;
	};
	/**
	 * Extract a wall clock from an ISO timestamp.
	 * @param {string} iso ISO timestamp.
	 * @returns {string} Clock text.
	 */
	static clock = iso => {
		const match = /T(\d{2}:\d{2}:\d{2})/u.exec(iso);
		return match?.[1] ?? "—";
	};
	/**
	 * Format a command duration.
	 * @param {number|null} seconds Seconds.
	 * @returns {string} Duration text.
	 */
	static duration = seconds => seconds === null ? "—" : seconds >= 1 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds * 1000)}ms`;
	/**
	 * Format bytes.
	 * @param {number} value Byte count.
	 * @returns {string} MiB display value.
	 */
	static mib = value => (value / 1048576).toFixed(1);
}
/**
 * Represent Sparkline.
 */
class Sparkline {
	/**
	 * Render sampled values as leveled bar spans.
	 * @param {HTMLElement} element Container.
	 * @param {number[]} values Samples.
	 * @param {number} [bars] Maximum bars.
	 * @returns {void}
	 */
	static render = (element, values, bars = 24) => {
		const levels = "▁▂▃▄▅▆▇█";
		const samples = values.slice(-bars);
		const peak = Math.max(1e-9, ...samples);
		element.textContent = samples.map(value => levels[Math.min(levels.length - 1, Math.round(value / peak * (levels.length - 1)))]).join("");
	};
}
/**
 * Represent Dashboard.
 */
class Dashboard {
	/**
	 * Initialize dashboard state.
	 */
	constructor() {
		/** @type {Map<string,SessionView>} */
		this.cards = new Map();
		/** @type {SessionSnapshot[]} */
		this.sessions = [];
		/** @type {WebSocket|null} */
		this.socket = null;
		/** Whether a socket has ever opened, which proves this browser is paired. */
		this.opened = false;
		this.receivedAt = Date.now();
		/** @type {import("./route.js").Place} */
		this.place = Route.parse(location.hash);
		/** @type {DashboardPayload|null} */
		this.payload = null;
		this.connect();
		this.controls();
		this.syncControls();
		window.addEventListener("hashchange", () => this.navigate());
		setInterval(() => Dashboard.freshness(this.receivedAt), 1000);
	}
	/**
	 * Connect to live updates.
	 * @returns {void}
	 */
	connect() {
		const scheme = location.protocol === "https:" ? "wss" : "ws";
		this.socket = new WebSocket(`${scheme}://${location.host}/ws`);
		this.socket.addEventListener("open", () => {
			this.opened = true;
			Dashboard.connection(true);
		});
		this.socket.addEventListener("close", () => {
			Dashboard.connection(false);
			if (!this.opened) Dashboard.unpaired();
			setTimeout(() => this.connect(), this.opened ? 1000 : 5000);
		});
		this.socket.addEventListener("message", event => {
			this.receivedAt = Date.now();
			this.update( /** @type {DashboardPayload} */ (JSON.parse(String(event.data))));
		});
	}
	/**
	 * Bind the search, agent, state and sort controls.
	 * @returns {void}
	 */
	controls() {
		["#filter", "#agent-filter", "#state-filter", "#sort"].forEach(selector => {
			Dom.one(selector).addEventListener("input", () => this.choose());
		});
	}
	/**
	 * Adopt the controls' values as this screen's filters and keep the address in step.
	 * @returns {void}
	 */
	choose() {
		/** @param {string} id Control id. @returns {string} Its trimmed value. */
		const value = id => /** @type {HTMLInputElement} */ (Dom.one(id)).value.trim();
		const scope = Filters.parse(Filters.query({
			...this.place.scope,
			q: value("#filter"),
			agent: value("#agent-filter"),
			state: value("#state-filter"),
			sort: value("#sort")
		}));
		this.place = {
			...this.place,
			scope
		};
		history.replaceState(null, "", Route.format(this.place));
		this.renderAll();
	}
	/**
	 * Follow the address to another screen, starting it at the top.
	 * @returns {void}
	 */
	navigate() {
		const before = this.place;
		this.place = Route.parse(location.hash);
		this.syncControls();
		this.renderAll();
		if (before.view !== this.place.view || before.id !== this.place.id) window.scrollTo(0, 0);
	}
	/**
	 * Show the filters in the controls without disturbing text being typed.
	 * @returns {void}
	 */
	syncControls() {
		const scope = this.place.scope;
		/** @param {string} id Control id. @param {string} value Wanted value. @returns {void} */
		const set = (id, value) => {
			const control = /** @type {HTMLInputElement} */ (Dom.one(id));
			if (control.value.trim() !== value) control.value = value;
		};
		set("#filter", scope.q ?? "");
		set("#agent-filter", scope.agent ?? "");
		set("#state-filter", scope.state ?? "");
		set("#sort", scope.sort ?? Filters.defaultSort);
	}
	/**
	 * Take a new payload and redraw.
	 * @param {DashboardPayload} payload Dashboard payload.
	 * @returns {void}
	 */
	update(payload) {
		this.payload = payload;
		this.renderAll();
	}
	/**
	 * Redraw the current screen from the last payload.
	 * @returns {void}
	 */
	renderAll() {
		const payload = this.payload;
		if (payload === null) return;
		const place = this.place;
		const scope = Route.effective(place);
		this.sessions = payload.sessions;
		const sessions = payload.sessions.filter(session => Filters.session(session, scope));
		const events = payload.events.filter(event => Filters.record(event, scope)).reverse();
		const commands = payload.commands.filter(record => Filters.record(record, scope)).reverse();
		Dashboard.renderHeader(payload.server);
		Dashboard.renderAgents(payload.sessions, place.scope);
		this.syncControls();
		Dashboard.frame(place, sessions.length, payload.sessions.length);
		Dashboard.renderScope(place);
		if (place.view === "overview") Dashboard.renderOverview(payload, events, commands);
		else if (place.view === "sessions") this.renderSessionList(sessions);
		else if (place.view === "agent") this.renderAgent(sessions, events, commands);
		else if (place.view === "session") this.renderSession(payload, events, commands);
		else if (place.view === "activity") Dashboard.renderEvents("#activity-feed", events);
		else Dashboard.renderCommands("#command-feed", commands);
	}
	/**
	 * The chrome around a screen: which screen shows, its title and breadcrumb,
	 * which controls apply, and which tab is current.
	 * @param {import("./route.js").Place} place Current place.
	 * @param {number} shown Sessions in scope.
	 * @param {number} total All sessions.
	 * @returns {void}
	 */
	static frame = (place, shown, total) => {
		const view = place.view;
		document.querySelectorAll(".view").forEach(section => {
			const holder = /** @type {HTMLElement & {dataset:{view?:string}}} */ (section);
			holder.hidden = holder.dataset.view !== view;
		});
		/** @param {string} selector Selector. @param {boolean} hidden Hidden state. @returns {void} */
		const hide = (selector, hidden) => {
			/** @type {HTMLElement} */
			(Dom.one(selector)).hidden = hidden;
		};
		hide("#stats", view !== "overview" && view !== "agent");
		hide("#filters", !["sessions", "activity", "commands"].includes(view));
		hide("#state-filter", view !== "sessions");
		hide("#sort", view !== "sessions");
		const titles = /** @type {Record<string,string>} */ ({
			overview: "Overview",
			sessions: "Sessions",
			activity: "Activity",
			commands: "Commands",
			agent: place.id,
			session: place.id
		});
		const title = titles[view] ?? "Overview";
		Dom.one("#view-title").textContent = title; /** @type {HTMLElement} */
		(Dom.one("#view-title")).hidden = view === "session";
		const sub = view === "sessions" ? (shown === total ? `${total} in total` : `${shown} of ${total}`) : view === "agent" ? `${shown} ${shown === 1 ? "session" : "sessions"}` : "";
		Dom.one("#view-sub").textContent = sub; /** @type {HTMLElement} */
		(Dom.one("#view-sub")).hidden = sub === "";
		const crumb = Dom.one("#crumb");
		/** @type {HTMLElement} */
		(crumb).hidden = view !== "agent" && view !== "session";
		if (view === "agent") crumb.replaceChildren(Dashboard.anchor("Overview", Route.href("overview"), "crumb-link"));
		if (view === "session") crumb.replaceChildren(Dashboard.anchor("Overview", Route.href("overview"), "crumb-link"), document.createTextNode(" › "), Dashboard.anchor(Dashboard.agentOf(place.id), Route.href("agent", Dashboard.agentOf(place.id)), "crumb-link"));
		document.title = view === "overview" ? "District Terminal" : `${title} · District Terminal`;
		const current = view === "agent" || view === "session" ? "sessions" : view;
		document.querySelectorAll(".tab-link").forEach(tab => {
			const link = /** @type {HTMLAnchorElement & {dataset:{tab?:string}}} */ (tab);
			link.ariaCurrent = link.dataset.tab === current ? "page" : null;
		});
	};
	/**
	 * The agent a session belongs to, for breadcrumbs.
	 * @param {string} id Session id.
	 * @returns {string} Agent name, `shell` when unknown.
	 */
	static agentOf = id => {
		const found = Dashboard.known.get(id);
		return found === undefined ? "shell" : Filters.agent({
			agent: found
		});
	};
	/** @type {Map<string,string>} Agent of every session seen, so a breadcrumb survives the session ending. */
	static known = new Map();
	/**
	 * A link that navigates within the dashboard.
	 * @param {string} text Link text.
	 * @param {string} href Hash to go to.
	 * @param {string} className Style class.
	 * @returns {HTMLAnchorElement} The link.
	 */
	static anchor = (text, href, className) => {
		const link = document.createElement("a");
		link.className = className;
		link.href = href;
		link.textContent = text;
		return link;
	};
	/**
	 * @param {string} className Style class.
	 * @param {string} [text] Text.
	 * @returns {HTMLSpanElement} A span.
	 */
	static span = (className, text = "") => {
		const node = document.createElement("span");
		node.className = className;
		node.textContent = text;
		return node;
	};
	/**
	 * Set text only when it differs, so an unchanged value never touches the DOM.
	 * @param {Node} node Node to update.
	 * @param {string} text Wanted text.
	 * @returns {void}
	 */
	static put = (node, text) => {
		if (node.textContent !== text) node.textContent = text;
	};
	/**
	 * Keep a list in step with data without rebuilding it: reuse a row for an item
	 * it already shows, add and remove only the difference, and reorder only if
	 * the order changed. Rows survive a refresh, so taps and focus are not lost.
	 * @template T
	 * @param {Element} list The `ol`.
	 * @param {T[]} items Items, in display order.
	 * @param {(item:T)=>string} key Stable identity of an item.
	 * @param {(item:T)=>HTMLElement} create Build a row.
	 * @param {(row:HTMLElement,item:T)=>void} refresh Bring a row up to date.
	 * @returns {void}
	 */
	static reconcile = (list, items, key, create, refresh) => {
		/** @type {Map<string,HTMLElement>} */
		const present = new Map();
		[...list.children].forEach(child => {
			const row = /** @type {HTMLElement & {dataset:{key?:string}}} */ (child);
			present.set(row.dataset.key ?? "", row);
		});
		const wanted = items.map(item => {
			const id = key(item);
			let row = present.get(id);
			if (row === undefined) {
				row = create(item); /** @type {{key?:string}} */
				(row.dataset).key = id;
			}
			refresh(row, item);
			return row;
		});
		present.forEach(row => {
			if (!wanted.includes(row)) row.remove();
		});
		wanted.forEach((row, index) => {
			if (list.children[index] !== row) list.insertBefore(row, list.children[index] ?? null);
		});
	};
	/**
	 * The sessions screen: one compact row each, linking to the session.
	 * @param {SessionSnapshot[]} sessions Sessions in scope.
	 * @returns {void}
	 */
	renderSessionList(sessions) {
		const ordered = [...sessions];
		Dashboard.sort(ordered, this.place.scope.sort ?? Filters.defaultSort);
		Dashboard.renderSessionRows("#session-list", ordered);
		const none = ordered.length === 0;
		const any = this.sessions.length > 0;
		/** @type {HTMLElement} */
		(Dom.one("#empty-state")).hidden = !none;
		Dom.one("#empty-text").textContent = any ? "No session matches these filters." : "No sessions. Start one with district --new.";
		/** @type {HTMLElement} */
		(Dom.one("#empty-clear")).hidden = !any;
	}
	/**
	 * @param {string} selector The `ol` to fill.
	 * @param {SessionSnapshot[]} sessions Sessions, in display order.
	 * @returns {void}
	 */
	static renderSessionRows = (selector, sessions) => {
		sessions.forEach(session => {
			Dashboard.known.set(session.id, session.agent);
		});
		Dashboard.reconcile(Dom.one(selector), sessions, session => session.id, () => {
			const item = document.createElement("li");
			const link = Dashboard.anchor("", "#", "row");
			const title = Dashboard.span("row-title");
			title.append(Dashboard.span("row-agent"), Dashboard.span("row-id"));
			link.append(Dashboard.span("row-dot"), title, Dashboard.span("row-state"), Dashboard.span("row-command"), Dashboard.span("row-stats"));
			item.append(link);
			return item;
		}, (item, session) => {
			const link = /** @type {HTMLAnchorElement} */ (item.children[0]);
			if (link === undefined) return;
			const [dot, title, state, command, stats] = /** @type {HTMLElement[]} */ ([...link.children]);
			if (dot === undefined || title === undefined || state === undefined || command === undefined || stats === undefined) return;
			link.href = Route.href("session", session.id);
			/** @type {CardData} */
			(item.dataset).state = session.state;
			Dashboard.put( /** @type {Node} */ (title.children[0]), Filters.agent(session));
			Dashboard.put( /** @type {Node} */ (title.children[1]), session.id);
			Dashboard.put(state, session.state.toLowerCase());
			Dashboard.put(command, session.command || "idle");
			Dashboard.put(stats, `${session.cpuPercent.toFixed(1)}% · ${Format.mib(session.rssBytes)} MiB · ${session.processCount} proc`);
		});
	};
	/**
	 * The overview: totals, one row per agent, and the latest of everything.
	 * @param {DashboardPayload} payload Payload.
	 * @param {ActivityEvent[]} events Events, newest first.
	 * @param {CommandRecord[]} commands Commands, newest first.
	 * @returns {void}
	 */
	static renderOverview = (payload, events, commands) => {
		Dashboard.renderStats(payload.totals, {});
		Dashboard.renderAgentRows(payload.sessions);
		Dashboard.renderEvents("#overview-activity", events.slice(0, 5));
		Dashboard.renderCommands("#overview-commands", commands.slice(0, 5));
	};
	/**
	 * One screen for an agent: its totals, sessions, activity and commands.
	 * @param {SessionSnapshot[]} sessions The agent's sessions.
	 * @param {ActivityEvent[]} events Its events, newest first.
	 * @param {CommandRecord[]} commands Its commands, newest first.
	 * @returns {void}
	 */
	renderAgent(sessions, events, commands) {
		Dashboard.renderStats(Filters.totals(sessions), {
			agent: this.place.id
		});
		Dashboard.renderSessionRows("#agent-sessions", [...sessions].sort((a, b) => a.ageSeconds - b.ageSeconds));
		Dashboard.renderEvents("#agent-activity", events.slice(0, 10));
		Dashboard.renderCommands("#agent-commands", commands.slice(0, 10));
	}
	/**
	 * One screen for a session: the full card, then its activity and commands.
	 * @param {DashboardPayload} payload Payload.
	 * @param {ActivityEvent[]} events Its events, newest first.
	 * @param {CommandRecord[]} commands Its commands, newest first.
	 * @returns {void}
	 */
	renderSession(payload, events, commands) {
		const session = payload.sessions.find(item => item.id === this.place.id);
		Dashboard.known.set(this.place.id, session?.agent ?? Dashboard.known.get(this.place.id) ?? "");
		[...this.cards.keys()].filter(id => id !== this.place.id).forEach(id => {
			const view = this.cards.get(id);
			if (view !== undefined) view.card.remove();
			this.cards.delete(id);
		});
		/** @type {HTMLElement} */
		(Dom.one("#missing")).hidden = session !== undefined;
		if (session !== undefined) this.updateCard(session);
		else {
			const view = this.cards.get(this.place.id);
			if (view !== undefined) {
				view.card.remove();
				this.cards.delete(this.place.id);
			}
		}
		Dashboard.renderEvents("#session-activity", events.slice(0, 10));
		Dashboard.renderCommands("#session-commands", commands.slice(0, 10));
	}
	/**
	 * One row per agent, with what it is doing.
	 * @param {SessionSnapshot[]} sessions All sessions.
	 * @returns {void}
	 */
	static renderAgentRows = sessions => {
		const groups = Filters.agents(sessions).map(name => {
			const own = sessions.filter(session => Filters.agent(session) === name);
			return {
				name,
				...Filters.totals(own)
			};
		});
		Dashboard.reconcile(Dom.one("#agent-list"), groups, group => group.name, () => {
			const item = document.createElement("li");
			const link = Dashboard.anchor("", "#", "row");
			link.append(Dashboard.span("row-dot"), Dashboard.span("row-title"), Dashboard.span("row-state"), Dashboard.span("row-command"), Dashboard.span("row-stats"));
			item.append(link);
			return item;
		}, (item, group) => {
			const link = /** @type {HTMLAnchorElement} */ (item.children[0]);
			if (link === undefined) return;
			const [, title, state, command, stats] = /** @type {HTMLElement[]} */ ([...link.children]);
			if (title === undefined || state === undefined || command === undefined || stats === undefined) return;
			link.href = Route.href("agent", group.name);
			/** @type {CardData} */
			(item.dataset).state = group.active > 0 ? "ACTIVE" : group.dead === group.sessions ? "DEAD" : "IDLE";
			Dashboard.put(title, group.name);
			Dashboard.put(state, group.active > 0 ? `${group.active} live` : "idle");
			Dashboard.put(command, `${group.sessions} ${group.sessions === 1 ? "session" : "sessions"}${group.dead > 0 ? ` · ${group.dead} dead` : ""}`);
			Dashboard.put(stats, `${group.cpuPercent.toFixed(1)}% · ${Format.mib(group.rssBytes)} MiB`);
		});
	};
	/**
	 * Offer every agent that has a session.
	 * @param {SessionSnapshot[]} sessions All sessions.
	 * @param {import("./filters.js").Scope} scope Current filters.
	 * @returns {void}
	 */
	static renderAgents = (sessions, scope) => {
		const select = Dom.one("#agent-filter");
		const names = Filters.agents(sessions);
		if (scope.agent !== undefined && !names.includes(scope.agent)) names.push(scope.agent);
		if (!Dashboard.changed(select, names.join("\u0000"))) return;
		const all = document.createElement("option");
		all.value = "";
		all.textContent = "All agents";
		select.replaceChildren(all, ...names.map(name => {
			const option = document.createElement("option");
			option.value = name;
			option.textContent = name;
			return option;
		}));
		/** @type {HTMLSelectElement} */
		(select).value = scope.agent ?? "";
	};
	/**
	 * Show each active filter as a chip that removes it.
	 * @param {import("./route.js").Place} place Current place.
	 * @returns {void}
	 */
	static renderScope = place => {
		const nav = Dom.one("#scope");
		const scope = place.scope;
		const values = /** @type {Record<string,string|undefined>} */ (scope);
		const active = Filters.chips.filter(key => values[key] !== undefined && key !== "session");
		/** @type {HTMLElement} */
		(nav).hidden = active.length === 0;
		if (!Dashboard.changed(nav, JSON.stringify([place.view, scope]))) return;
		const chips = active.map(key => {
			const label = Filters.label(key, values[key] ?? "");
			const chip = Dashboard.anchor(`${label} ×`, Route.href(place.view, place.id, {
				...scope,
				[key]: ""
			}), "scope-chip");
			chip.ariaLabel = `Remove filter: ${label}`;
			return chip;
		});
		if (active.length > 1) chips.push(Dashboard.anchor("Clear all", Route.href(place.view, place.id, {
			sort: scope.sort ?? ""
		}), "scope-clear"));
		nav.replaceChildren(...chips);
	};
	/**
	 * Remember what each element last showed, so an unchanged list is left alone.
	 * A rebuild shifts everything below it, which Safari does not compensate for.
	 * @param {Element} element Element that renders the data.
	 * @param {string} key Serialized data.
	 * @returns {boolean} Whether the data differs from what is shown.
	 */
	static changed = (element, key) => {
		if (Dashboard.seen.get(element) === key) return false;
		Dashboard.seen.set(element, key);
		return true;
	};
	/** @type {WeakMap<Element, string>} */
	static seen = new WeakMap();
	/**
	 * Update header server metadata.
	 * @param {ServerMeta} server Server metadata.
	 * @returns {void}
	 */
	static renderHeader = server => {
		Dom.one("#server-version").textContent = `v${server.version}`;
		Dom.one("#server-uptime").textContent = `up ${Format.age(server.uptimeSeconds)}`;
		Dom.one("#server-mode").textContent = `${server.mode.toLowerCase()} · ${server.sampleMs}ms`;
	};
	/**
	 * Update the stat strip; each tile links to the sessions it counts.
	 * @param {FleetTotals} totals Totals.
	 * @param {import("./filters.js").Scope} scope Filters to carry into the link.
	 * @returns {void}
	 */
	static renderStats = (totals, scope) => {
		Dom.one("#stat-sessions").textContent = String(totals.sessions);
		Dom.one("#stat-active").textContent = String(totals.active);
		Dom.one("#stat-idle").textContent = String(totals.idle);
		Dom.one("#stat-dead").textContent = String(totals.dead);
		Dom.one("#stat-cpu").textContent = `${totals.cpuPercent.toFixed(1)}%`;
		Dom.one("#stat-mem").textContent = `${Format.mib(totals.rssBytes)} MiB`;
		Dom.one("#stat-procs").textContent = String(totals.processes);
		Dom.one("#stat-consumers").textContent = String(totals.clients);
		/** @type {Record<string,Record<string,string>>} */
		const patches = {
			sessions: {},
			active: {
				state: Filters.live
			},
			idle: {
				state: "IDLE"
			},
			dead: {
				state: "DEAD"
			},
			cpu: {
				sort: "cpu"
			},
			mem: {
				sort: "memory"
			},
			procs: {
				sort: "processes"
			},
			consumers: {
				clients: "1"
			}
		};
		Object.entries(patches).forEach(([name, patch]) => {
			const tile = Dom.one(`#stat-${name}`).parentNode;
			if (tile instanceof HTMLAnchorElement) tile.href = Route.href("sessions", "", {
				...scope,
				...patch
			});
		});
	};
	/**
	 * Render an activity feed; agents and sessions link to their screens.
	 * @param {string} selector The `ol` to fill.
	 * @param {ActivityEvent[]} events Events, newest first.
	 * @returns {void}
	 */
	static renderEvents = (selector, events) => {
		Dashboard.reconcile(Dom.one(selector), events.slice(0, 30), event => event.id, event => {
			const item = document.createElement("li");
			const time = document.createElement("time");
			time.textContent = Format.clock(event.occurred_at);
			const type = Dashboard.span("event-type", event.event_type.toLowerCase().replaceAll("_", " ")); /** @type {CardData} */
			(type.dataset).kind = event.event_type.split("_")[0] ?? "EVENT";
			const who = Dashboard.span("event-actor");
			who.append(Dashboard.anchor(Filters.agent(event), Route.href("agent", Filters.agent(event)), "agent-link"));
			if (event.session_id !== "") who.append(document.createTextNode(" · "), Dashboard.anchor(event.session_id, Route.href("session", event.session_id), "id-link"));
			const detail = Dashboard.span("event-detail", event.command || event.state || (event.exit_code === null ? "" : `exit ${event.exit_code}`));
			item.append(time, type, who, detail);
			return item;
		}, () => undefined);
	};
	/**
	 * Render a command feed, newest first, as one record per row.
	 * @param {string} selector The `ol` to fill.
	 * @param {CommandRecord[]} commands Commands, newest first.
	 * @returns {void}
	 */
	static renderCommands = (selector, commands) => {
		Dashboard.reconcile(Dom.one(selector), commands.slice(0, 30), record => record.command_id, record => {
			const item = document.createElement("li");
			const command = Dashboard.span("command-text", record.command);
			const meta = Dashboard.span("command-meta");
			const who = Dashboard.span("command-who");
			who.append(Dashboard.anchor(Filters.agent(record), Route.href("agent", Filters.agent(record)), "agent-link"), document.createTextNode(" · "), Dashboard.anchor(record.session_id, Route.href("session", record.session_id), "id-link"));
			meta.append(Dashboard.span("command-time", Format.clock(record.started_at)), who, Dashboard.span("command-exit"), Dashboard.span("command-duration"));
			item.append(command, meta);
			return item;
		}, (item, record) => {
			const exit = item.querySelector(".command-exit");
			const duration = item.querySelector(".command-duration");
			if (exit === null || duration === null) return;
			const running = record.exit_code === null;
			Dashboard.put(exit, running ? "running" : `exit ${record.exit_code}`);
			/** @type {CardData} */
			(item.dataset).exit = running ? "running" : record.exit_code === 0 ? "ok" : "failed";
			Dashboard.put(duration, Format.duration(record.duration_seconds ?? null));
		});
	};
	/**
	 * Sort sessions in place.
	 * @param {SessionSnapshot[]} sessions Visible sessions.
	 * @param {string} order One of newest, cpu, memory, processes or agent.
	 * @returns {void}
	 */
	static sort = (sessions, order) => {
		const ranks = /** @type {Record<string,RankFn>} */ ({
			cpu: (a, b) => b.cpuPercent - a.cpuPercent,
			memory: (a, b) => b.rssBytes - a.rssBytes,
			processes: (a, b) => b.processCount - a.processCount,
			agent: (a, b) => (a.agent || a.id).localeCompare(b.agent || b.id),
			newest: (a, b) => a.ageSeconds - b.ageSeconds
		});
		sessions.sort(ranks[order] ?? ((a, b) => a.ageSeconds - b.ageSeconds));
	};
	/**
	 * * Handle updateCard.
	 *
	 * @param {SessionSnapshot} session Session snapshot.
	 * @returns {void}
	 *
	 * @throws {Error} When the operation cannot complete.
	 */
	updateCard(session) {
		let view = this.cards.get(session.id);
		if (view === undefined) view = this.createCard(session);
		/** @type {CardData} */
		(view.card.dataset).state = session.state;
		const title = Dom.one(".agent", view.card);
		if (Dashboard.changed(title, JSON.stringify([session.agent, session.id]))) title.replaceChildren(Dashboard.anchor(Filters.agent(session), Route.href("agent", Filters.agent(session)), "agent-link"), document.createTextNode(" · "), Dashboard.anchor(session.id, Route.href("session", session.id), "id-link"));
		Dom.one(".state", view.card).textContent = session.state;
		Dashboard.badge(Dom.one(".badge-age", view.card), `age ${Format.age(session.ageSeconds)}`);
		Dashboard.badge(Dom.one(".badge-clients", view.card), `${session.clients} ${session.clients === 1 ? "client" : "clients"}`);
		Dashboard.badge(Dom.one(".badge-provider", view.card), session.agentSession ? `${session.agent}@${session.agentSession}` : "");
		Dashboard.badge(Dom.one(".badge-editor", view.card), session.editor);
		Dom.one(".metadata", view.card).textContent = `${session.surface || "cli"} · ${session.backend} · ${session.cwd || "—"}`;
		Dom.one(".command", view.card).textContent = session.command || "idle";
		const meter = /** @type {HTMLMeterElement} */ (Dom.one(".cpu-meter", view.card));
		meter.value = Math.min(100, session.cpuPercent);
		Dom.one(".cpu-value", view.card).textContent = `${session.cpuPercent.toFixed(1)}%`;
		Dom.one(".rss-value", view.card).textContent = `${Format.mib(session.rssBytes)} MiB`;
		Dom.one(".io-text", view.card).textContent = `IO ↓${Format.mib(session.readBytes)}/↑${Format.mib(session.writeBytes)} MiB · ${session.processCount} proc${session.partialResources ? " · partial" : ""}`;
		Sparkline.render(Dom.one(".cpu-spark", view.card), session.cpuHistory);
		Sparkline.render(Dom.one(".rss-spark", view.card), session.rssHistory);
		const ports = /** @type {HTMLElement} */ (Dom.one(".ports", view.card));
		if (Dashboard.changed(ports, session.ports.join(","))) ports.replaceChildren(...session.ports.map(port => {
			const chip = document.createElement("span");
			chip.className = "port";
			chip.textContent = `:${port}`;
			return chip;
		}));
		ports.hidden = session.ports.length === 0;
		Dom.one(".resources", view.card).textContent = `${session.processCount} processes · ${session.connections.length} sockets · network bytes ${session.networkBytesSupported ? "available" : "unavailable"}${session.lastActivityAt ? ` · last activity ${Format.clock(session.lastActivityAt)}` : ""}`;
		Dom.one(".process-tree", view.card).textContent = session.processes.map(process => `${process.pid} ← ${process.parentPid}  ${process.cpuPercent.toFixed(1)}%  ${Format.mib(process.rssBytes)} MiB  ${process.name}  ${process.command}`).join("\n") + (session.connections.length ? `\n\nSockets\n${session.connections.join("\n")}` : "");
		const attach = Dom.one(".attach", view.card);
		if (attach instanceof HTMLButtonElement) attach.disabled = session.state === "DEAD";
		const command = Dom.one(".attach-command", view.card);
		if (command instanceof HTMLButtonElement) command.disabled = !session.reconnectable;
		if (view.terminal.text !== session.output) view.terminal.render(session.output);
	}
	/**
	 * Build a session card with wired actions.
	 * @param {SessionSnapshot} session Session snapshot.
	 * @returns {SessionView} Card view.
	 * @throws {Error} When the template is invalid.
	 */
	createCard(session) {
		const template = Dom.one("#session-template");
		if (!(template instanceof HTMLTemplateElement)) throw new Error("Invalid session template");
		const source = template.content.firstElementChild;
		if (!(source instanceof HTMLElement)) throw new Error("Missing session card");
		const card = /** @type {HTMLElement} */ (source.cloneNode(true));
		const terminalElement = Dom.one(".terminal", card);
		if (!(terminalElement instanceof HTMLElement)) throw new Error("Invalid terminal");
		const view = {
			card,
			terminal: new AnsiTerminal(terminalElement)
		};
		Dom.one("#sessions").append(card);
		const more = /** @type {HTMLElement & {open?:boolean}} */ (Dom.one(".more", card));
		const copy = Dom.one(".copy", card);
		if (!(copy instanceof HTMLButtonElement)) throw new Error("Invalid copy button");
		copy.addEventListener("click", () => {
			navigator.clipboard.writeText(Dom.one(".command", card).textContent ?? "");
			Dashboard.confirm(copy);
			more.open = false;
		});
		Dom.one(".attach", card).addEventListener("click", () => {
			window.open(`/console?session=${encodeURIComponent(session.id)}`, "district-console");
		});
		[
			["view", "view"],
			["history", "history"],
			["attach-command", "attach"]
		].forEach(([selector, action]) => {
			Dom.one(`.${selector}`, card).addEventListener("click", async () => {
				const response = await fetch(`/api/sessions/${session.id}/${action}`, {
					method: "POST"
				});
				const payload =
					/**
					 * @type {{command?:string}}
					 */
					(await response.json());
				if (payload.command !== undefined) {
					await navigator.clipboard.writeText(payload.command);
					Dashboard.toast(`${action} command copied: ${payload.command}`);
				} else Dashboard.toast(`${action} failed for ${session.id}`, "bad");
				more.open = false;
			});
		});
		Dom.one(".kill", card).addEventListener("click", async () => {
			const response = await fetch(`/api/sessions/${session.id}/kill`, {
				method: "POST"
			});
			Dashboard.toast(response.ok ? `Killed ${session.id}` : `Kill failed for ${session.id}`, response.ok ? "info" : "bad");
		});
		const form = Dom.one(".exec", card);
		const input = /** @type {HTMLInputElement} */ (Dom.one(".exec-input", card));
		const run = /** @type {HTMLButtonElement} */ (Dom.one(".exec-run", card));
		const output = /** @type {HTMLElement} */ (Dom.one(".exec-output", card));
		form.addEventListener("submit", event => {
			event.preventDefault();
			Dashboard.exec(session.id, input, run, output);
		});
		const typeInput = /** @type {HTMLInputElement} */ (Dom.one(".type-input", card));
		const typeSend = /** @type {HTMLButtonElement} */ (Dom.one(".type-send", card));
		Dom.one(".type", card).addEventListener("submit", event => {
			event.preventDefault();
			const text = typeInput.value.trim();
			if (text === "") return;
			typeInput.value = "";
			Dashboard.type(session.id, {
				text
			}, typeSend);
		});
		const keysMenu = /** @type {HTMLElement & {open?:boolean}} */ (Dom.one(".type-keys", card));
		const keys = /** @type {[string, string][]} */ ([
			["Enter", "key-enter"],
			["Escape", "key-escape"],
			["Tab", "key-tab"],
			["C-c", "key-interrupt"],
			["Up", "key-up"],
			["Down", "key-down"]
		]);
		keys.forEach(([name, cls]) => {
			const chip = /** @type {HTMLButtonElement} */ (Dom.one(`.${cls}`, card));
			chip.addEventListener("click", () => {
				Dashboard.type(session.id, {
					key: name
				}, chip);
				keysMenu.open = false;
			});
		});
		this.cards.set(session.id, view);
		return view;
	}
	/**
	 * Run one command inside a session and show the captured result.
	 * @param {string} id Session id.
	 * @param {HTMLInputElement} input Command input.
	 * @param {HTMLButtonElement} run Run button.
	 * @param {HTMLElement} output Output element.
	 * @returns {Promise<void>}
	 */
	static exec = async (id, input, run, output) => {
		const command = input.value.trim();
		if (command === "") return;
		run.disabled = true;
		input.value = "";
		try {
			const response = await fetch(`/api/sessions/${id}/exec`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json"
				},
				body: JSON.stringify({
					command
				})
			});
			const payload =
				/**
				 * @type {{detail?:string,output?:string,exitStatus?:number}}
				 */
				(await response.json());
			if (!response.ok) {
				Dashboard.toast(`Command failed: ${payload.detail ?? response.status}`, "bad");
				return;
			}
			output.hidden = false;
			/** @type {CardData} */
			(output.dataset).exit = String(payload.exitStatus);
			output.textContent = `$ ${command}\n${(payload.output ?? "").trimEnd()}\n[exit ${payload.exitStatus}]`;
		} catch {
			Dashboard.toast(`Command failed: network error`, "bad");
		} finally {
			run.disabled = false;
		}
	};
	/**
	 * Inject literal text or one key press into a session pane.
	 * @param {string} id Session id.
	 * @param {{text?:string,key?:string}} body Send payload.
	 * @param {HTMLButtonElement} button Triggering button.
	 * @returns {Promise<void>}
	 */
	static type = async (id, body, button) => {
		button.disabled = true;
		try {
			const response = await fetch(`/api/sessions/${id}/send`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json"
				},
				body: JSON.stringify(body)
			});
			const payload =
				/**
				 * @type {{detail?:string}}
				 */
				(await response.json());
			if (!response.ok) {
				Dashboard.toast(`Send failed: ${payload.detail ?? response.status}`, "bad");
				return;
			}
			Dashboard.confirm(button, "Sent");
		} catch {
			Dashboard.toast("Send failed: network error", "bad");
		} finally {
			button.disabled = false;
		}
	};
	/**
	 * Set badge text and visibility.
	 * @param {HTMLElement} badge Badge element.
	 * @param {string} text Badge text.
	 * @returns {void}
	 */
	static badge = (badge, text) => {
		badge.textContent = text;
		badge.hidden = text === "";
	};
	/**
	 * Flash a successful inline confirmation on a button.
	 * @param {HTMLButtonElement} button Button.
	 * @param {string} [label] Confirmation text.
	 * @returns {void}
	 */
	static confirm = (button, label = "Copied") => {
		const previous = button.textContent;
		button.textContent = label;
		setTimeout(() => {
			button.textContent = previous;
		}, 1500);
	};
	/**
	 * Append a transient status toast.
	 * @param {string} message Message.
	 * @param {string} [kind] Tone.
	 * @returns {void}
	 */
	static toast = (message, kind = "info") => {
		const toast = document.createElement("div");
		toast.className = "toast"; /** @type {CardData} */
		(toast.dataset).kind = kind;
		toast.textContent = message;
		Dom.one("#toasts").append(toast);
		setTimeout(() => toast.remove(), 4000);
	};
	/**
	 * Update the last-update freshness chip.
	 * @param {number} receivedAt Last message timestamp.
	 * @returns {void}
	 */
	static freshness = receivedAt => {
		Dom.one("#updated-ago").textContent = `updated ${Format.age(Math.max(0, (Date.now() - receivedAt) / 1000))} ago`;
	};
	/**
	 * @param {number} value Byte count.
	 * @returns {string} MiB display value.
	 */
	static mib = value => Format.mib(value);
	/**
	 * @param {boolean} connected Connection state.
	 * @returns {void}
	 */
	static connection = connected => {
		const status = Dom.one(".connection");
		if (!(status instanceof HTMLElement)) throw new Error("Invalid status");
		const dataset =
			/**
			 * @type {{connected?:string}}
			 */
			(status.dataset);
		dataset.connected = String(connected);
		Dom.one("#connection-label").textContent = connected ? "Live" : "Reconnecting";
	};
	/**
	 * Explain that the server refused this browser because it was never paired.
	 * @returns {void}
	 */
	static unpaired = () => {
		Dom.one("#connection-label").textContent = "Not paired — scan the QR code";
	};
}
if (typeof window !== "undefined") Reflect.construct(Dashboard, []);
export {
	AnsiTerminal,
	Dashboard,
	Format,
	Sparkline
};
