/**
 * Small shared helpers for the dashboard's newer screens: DOM builders and
 * the formatters that turn counts, sizes and moments into short text.
 */
/** Build elements and format values. */
class Kit {
	/** Binary size units, smallest first. */
	static units = ["B", "KiB", "MiB", "GiB", "TiB"];
	/** Seconds in a minute, an hour and a day. */
	static spans = {
		minute: 60,
		hour: 3600,
		day: 86400
	};
	/**
	 * Build an element.
	 * @param {string} tag Tag name.
	 * @param {string} [className] Space-separated classes.
	 * @param {string} [text] Text content.
	 * @returns {HTMLElement} The element.
	 */
	static el = (tag, className = "", text = "") => {
		const node = document.createElement(tag);
		if (className !== "") node.className = className;
		if (text !== "") node.textContent = text;
		return node;
	};
	/**
	 * Build a link that navigates within the dashboard.
	 * @param {string} text Link text.
	 * @param {string} href Destination.
	 * @param {string} [className] Space-separated classes.
	 * @returns {HTMLAnchorElement} The link.
	 */
	static link = (text, href, className = "") => {
		const node = /** @type {HTMLAnchorElement} */ (document.createElement("a"));
		node.href = href;
		node.textContent = text;
		if (className !== "") node.className = className;
		return node;
	};
	/**
	 * Copy text for the person to paste elsewhere.
	 * @param {string} text What to copy.
	 * @param {Navigator} [nav] The browser, injected for tests.
	 * @returns {Promise<boolean>} Whether it was copied. A page opened over plain HTTP has no clipboard API, so the legacy path is tried too.
	 */
	static copy = async (text, nav = navigator) => {
		if (nav.clipboard !== undefined)
			try {
				await nav.clipboard.writeText(text);
				return true;
			} catch {
				return Kit.legacyCopy(text);
			}
		return Kit.legacyCopy(text);
	};
	/**
	 * Copy through a hidden, selected field.
	 * @param {string} text What to copy.
	 * @returns {boolean} Whether the browser copied it.
	 */
	static legacyCopy = text => {
		if (typeof document.execCommand !== "function") return false;
		const field = /** @type {HTMLTextAreaElement} */ (Kit.el("textarea", "visually-hidden"));
		field.value = text;
		field.setAttribute("readonly", "");
		document.body.append(field);
		field.select();
		const copied = document.execCommand("copy");
		field.remove();
		return copied;
	};
	/**
	 * Show a byte count with a binary unit.
	 * @param {number} value Bytes.
	 * @returns {string} Such as `1.5 KiB`; one decimal only while the number is small.
	 */
	static bytes = value => {
		let size = value;
		let unit = 0;
		while (size >= 1024 && unit < Kit.units.length - 1) {
			size /= 1024;
			unit += 1;
		}
		if (unit === 0) return `${Math.round(size)} B`;
		return `${size < 10 ? size.toFixed(1) : Math.round(size)} ${Kit.units[unit]}`;
	};
	/**
	 * Join a larger unit with its remainder, leaving the remainder out when it is zero.
	 * @param {number} big Whole larger units.
	 * @param {string} bigName Their suffix.
	 * @param {number} rest Remainder in the next unit.
	 * @param {string} restName Its suffix.
	 * @returns {string} Such as `2h 5m`, or `2h` when nothing remains.
	 */
	static pair = (big, bigName, rest, restName) => rest === 0 ? `${big}${bigName}` : `${big}${bigName} ${rest}${restName}`;
	/**
	 * Show a length of time with its two largest units.
	 * @param {number} seconds Seconds.
	 * @returns {string} Such as `2d 3h`, `1h 5m`, `4m` or `9s`.
	 */
	static span = seconds => {
		const {
			minute,
			hour,
			day
		} = Kit.spans;
		const whole = Math.floor(seconds);
		if (whole >= day) return Kit.pair(Math.floor(whole / day), "d", Math.floor(whole % day / hour), "h");
		if (whole >= hour) return Kit.pair(Math.floor(whole / hour), "h", Math.floor(whole % hour / minute), "m");
		if (whole >= minute) return `${Math.floor(whole / minute)}m`;
		return `${whole}s`;
	};
	/**
	 * Say how long ago, or how long from now, a moment is.
	 * @param {string} iso ISO timestamp.
	 * @param {number} now Current time in milliseconds, passed in so the answer is reproducible.
	 * @returns {string} Such as `3m ago` or `in 10m`; a dash when the timestamp is missing or invalid.
	 */
	static ago = (iso, now) => {
		const moment = Date.parse(iso);
		if (Number.isNaN(moment)) return "—";
		const delta = Math.round((now - moment) / 1000);
		return delta >= 0 ? `${Kit.span(delta)} ago` : `in ${Kit.span(-delta)}`;
	};
	/**
	 * Show how long a command took.
	 * @param {number|null} seconds Seconds.
	 * @returns {string} Milliseconds below a second, seconds above; a dash when unknown.
	 */
	static duration = seconds => {
		if (seconds === null) return "—";
		return seconds >= 1 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds * 1000)}ms`;
	};
	/**
	 * Show a percentage.
	 * @param {number} value Percent, 0 to 100.
	 * @returns {string} One decimal below ten, whole numbers above.
	 */
	static percent = value => `${value < 10 ? value.toFixed(1) : Math.round(value)}%`;
}
export {
	Kit
};
