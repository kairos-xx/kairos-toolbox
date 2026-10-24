/**
 * Turns the typed widgets a district command returns (metrics, facts, tables,
 * checks, console output, notices) into DOM, so every command shows a real
 * interface instead of JSON. Districts and agents in a result link to their screens.
 */
import {
	Kit
} from "./kit.js";
/**
 * @typedef {{label:string,value:string,detail:string,tone:string,percent:number|null}} MetricItem
 * @typedef {{label:string,value:string,tone:string,href:string}} FactItem
 * @typedef {{key:string,label:string,kind:string}} ColumnSpec
 * @typedef {{name:string,ok:boolean,detail:string}} CheckItem
 * @typedef {{type:string,items?:MetricItem[]|FactItem[]|CheckItem[],title?:string,columns?:ColumnSpec[],rows?:Record<string,string|number|boolean|null>[],empty?:string,text?:string,tone?:string}} Widget
 * @typedef {{command:string,title:string,ok:boolean,widgets:Widget[]}} CommandResult
 * @typedef {string|number|boolean|null|undefined} Cell
 */
/** Render command results as widgets. */
class Widgets {
	/** Text shown for a value that is missing. */
	static none = "—";
	/**
	 * Show a command result in a container, replacing what was there.
	 * @param {HTMLElement} container Where to draw.
	 * @param {CommandResult} result The result.
	 * @param {number} now Current time in milliseconds, for "ago" cells.
	 * @returns {void}
	 */
	static render = (container, result, now) => {
		const head = Kit.el("header", "w-result-head");
		head.append(Kit.el("h2", "w-result-title", result.title));
		const state = Kit.el("span", "w-result-state", result.ok ? "Done" : "Failed");
		state.setAttribute("data-tone", String(result.ok ? "good" : "bad"));
		head.append(state);
		container.replaceChildren(head, ...result.widgets.flatMap(widget => {
			const built = Widgets.widget(widget, now);
			return built === null ? [] : [built];
		}));
	};
	/**
	 * Build one widget.
	 * @param {Widget} widget The widget.
	 * @param {number} now Current time in milliseconds.
	 * @returns {HTMLElement|null} Its element, or null for a type this page does not know.
	 */
	static widget = (widget, now) => {
		if (widget.type === "metrics") return Widgets.metrics(/** @type {MetricItem[]} */ (widget.items ?? []));
		if (widget.type === "facts") return Widgets.facts(widget.title ?? "", /** @type {FactItem[]} */ (widget.items ?? []));
		if (widget.type === "table") return Widgets.table(widget, now);
		if (widget.type === "checks") return Widgets.checks(widget.title ?? "", /** @type {CheckItem[]} */ (widget.items ?? []));
		if (widget.type === "console") return Widgets.console(widget.title ?? "", widget.text ?? "");
		if (widget.type === "notice") return Widgets.notice(widget.tone ?? "neutral", widget.text ?? "");
		return null;
	};
	/**
	 * A row of headline numbers; a value that is a share also draws a gauge.
	 * @param {MetricItem[]} items The metrics.
	 * @returns {HTMLElement} The widget.
	 */
	static metrics = items => {
		const list = Kit.el("ul", "w-metrics");
		items.forEach(item => {
			const tile = Kit.el("li", "w-metric");
			tile.setAttribute("data-tone", String(item.tone));
			tile.append(Kit.el("span", "w-metric-value", item.value), Kit.el("span", "w-metric-label", item.label));
			if (item.detail !== "") tile.append(Kit.el("span", "w-metric-detail", item.detail));
			if (item.percent !== null) tile.append(Widgets.gauge(item.percent));
			list.append(tile);
		});
		return list;
	};
	/**
	 * A bar that shows how full something is.
	 * @param {number} percent Fill, 0 to 100.
	 * @returns {HTMLElement} The gauge.
	 */
	static gauge = percent => {
		const gauge = Kit.el("span", "w-gauge");
		gauge.setAttribute("role", "meter");
		gauge.setAttribute("aria-valuemin", "0");
		gauge.setAttribute("aria-valuemax", "100");
		gauge.setAttribute("aria-valuenow", String(Math.round(percent)));
		gauge.style.setProperty("--runtime-gauge-fill", `${Math.max(0, Math.min(100, percent))}%`);
		return gauge;
	};
	/**
	 * A titled list of labelled values, labels on the left and values on the right.
	 * @param {string} title Heading.
	 * @param {FactItem[]} items The facts.
	 * @returns {HTMLElement} The widget.
	 */
	static facts = (title, items) => {
		const section = Kit.el("section", "w-facts");
		section.append(Kit.el("h3", "w-title", title));
		const list = Kit.el("dl", "w-fact-list");
		items.forEach(item => {
			const row = Kit.el("div", "w-fact");
			row.append(Kit.el("dt", "w-fact-label", item.label));
			const value = Kit.el("dd", "w-fact-value");
			value.setAttribute("data-tone", String(item.tone));
			value.append(item.href === "" ? document.createTextNode(item.value) : Kit.link(item.value, item.href));
			row.append(value);
			list.append(row);
		});
		section.append(list);
		return section;
	};
	/**
	 * A titled table whose cells present each kind of value; empty tables say why.
	 * @param {Widget} widget The table widget.
	 * @param {number} now Current time in milliseconds.
	 * @returns {HTMLElement} The widget.
	 */
	static table = (widget, now) => {
		const section = Kit.el("section", "w-table");
		section.append(Kit.el("h3", "w-title", widget.title ?? ""));
		const rows = widget.rows ?? [];
		if (rows.length === 0) {
			section.append(Kit.el("p", "w-empty", widget.empty ?? "Nothing to show."));
			return section;
		}
		const columns = widget.columns ?? [];
		const table = Kit.el("table", "w-grid");
		const headings = Kit.el("tr");
		columns.forEach(column => {
			const cell = Kit.el("th", "", column.label);
			cell.setAttribute("scope", "col");
			headings.append(cell);
		});
		const head = Kit.el("thead");
		head.append(headings);
		const body = Kit.el("tbody");
		rows.forEach(row => {
			const line = Kit.el("tr");
			columns.forEach(column => {
				const cell = Kit.el("td");
				cell.append(Kit.el("span", "w-cell-label", column.label), Widgets.cell(column.kind, row[column.key], now));
				line.append(cell);
			});
			body.append(line);
		});
		table.append(head, body);
		section.append(table);
		return section;
	};
	/**
	 * One table value, presented the way its kind should be read.
	 * @param {string} kind How to present it.
	 * @param {Cell} value The value.
	 * @param {number} now Current time in milliseconds.
	 * @returns {HTMLElement} The cell content.
	 */
	static cell = (kind, value, now) => {
		const missing = value === null || value === undefined || value === "";
		if (kind === "session" && !missing) return Kit.link(String(value), `#/session/${encodeURIComponent(String(value))}`, "w-cell-value w-link");
		if (kind === "agent" && !missing) return Kit.link(String(value), `#/agent/${encodeURIComponent(String(value))}`, "w-cell-value w-link");
		if (kind === "mono" && !missing) {
			const wrap = Kit.el("span", "w-cell-value");
			wrap.append(Kit.el("code", "", String(value)));
			return wrap;
		}
		if (kind === "exit" && typeof value === "number") {
			const exit = Kit.el("span", "w-cell-value", value === 0 ? "ok" : `exit ${value}`);
			exit.setAttribute("data-ok", String(String(value === 0)));
			return exit;
		}
		if (kind === "status" && !missing) {
			const badge = Kit.el("span", "w-cell-value w-badge", String(value).toLowerCase().replaceAll("_", " "));
			badge.setAttribute("data-state", String(String(value).toLowerCase()));
			return badge;
		}
		return Kit.el("span", "w-cell-value", missing ? Widgets.none : Widgets.plain(kind, value, now));
	};
	/**
	 * Format a plain-text kind of value.
	 * @param {string} kind Kind of value.
	 * @param {Cell} value The value.
	 * @param {number} now Current time in milliseconds.
	 * @returns {string} Display text.
	 */
	static plain = (kind, value, now) => {
		if (kind === "time") return Kit.ago(String(value), now);
		if (kind === "duration") return Kit.duration(Number(value));
		if (kind === "bytes") return Kit.bytes(Number(value));
		return String(value);
	};
	/**
	 * A pass or fail list; each line says so in words, not only with a mark.
	 * @param {string} title Heading.
	 * @param {CheckItem[]} items The checks.
	 * @returns {HTMLElement} The widget.
	 */
	static checks = (title, items) => {
		const section = Kit.el("section", "w-checks");
		section.append(Kit.el("h3", "w-title", title));
		const list = Kit.el("ul", "w-check-list");
		items.forEach(item => {
			const line = Kit.el("li", "w-check");
			line.setAttribute("data-ok", String(String(item.ok)));
			const mark = Kit.el("span", "w-check-mark", item.ok ? "✓" : "✕");
			mark.setAttribute("aria-hidden", "true");
			line.append(mark, Kit.el("span", "w-check-name", item.name), Kit.el("span", "w-check-word", item.ok ? "passed" : "failed"));
			if (item.detail !== "") line.append(Kit.el("span", "w-check-detail", item.detail));
			list.append(line);
		});
		section.append(list);
		return section;
	};
	/**
	 * A block of terminal output that keeps its text exactly.
	 * @param {string} title What produced it.
	 * @param {string} text The output.
	 * @returns {HTMLElement} The widget.
	 */
	static console = (title, text) => {
		const section = Kit.el("section", "w-console");
		section.append(Kit.el("h3", "w-title w-mono", title));
		const output = Kit.el("pre", "w-pre", text);
		output.setAttribute("tabindex", "0");
		section.append(output);
		return section;
	};
	/**
	 * A one-line message that is announced to assistive technology.
	 * @param {string} tone Emphasis.
	 * @param {string} text The message.
	 * @returns {HTMLElement} The widget.
	 */
	static notice = (tone, text) => {
		const notice = Kit.el("p", "w-notice", text);
		notice.setAttribute("data-tone", String(tone));
		notice.setAttribute("role", "status");
		return notice;
	};
}
export {
	Widgets
};
