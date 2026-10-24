/**
 * ANSI terminal emulation shared by the dashboard and the full-screen console.
 */
/**
 * @typedef {{char:string,classes:string[],link:string}} Cell
 */
/**
 * Represent AnsiTerminal.
 */
class AnsiTerminal {
	/** Pixels from the bottom within which the terminal still follows new output. */
	static followSlack = 24;
	/**
	 * @param {HTMLElement} element Output element.
	 */
	constructor(element) {
		this.element = element;
		this.text = "";
	}
	/**
	 * Emulate common ANSI cursor/erase/SGR/OSC-8 behavior.
	 * @param {string} input Raw ANSI stream.
	 * @returns {Node[]} Safe DOM nodes.
	 */
	static nodes = input => {
		/** @type {Cell[][]} */
		const lines = [
			[]
		];
		/** @type {string[]} */
		let classes = [];
		let row = 0;
		let column = 0;
		let savedRow = 0;
		let savedColumn = 0;
		let link = "";
		const ensure = () => {
			while (lines.length <= row) lines.push([]);
		};
		/**
		 * @param {string} char Character.
		 * @returns {void}
		 */
		const write = char => {
			ensure();
			const line = lines[row];
			if (line === undefined) return;
			while (line.length < column) line.push({
				char: " ",
				classes: [],
				link: ""
			});
			line[column] = {
				char,
				classes: [...classes],
				link
			};
			column += 1;
		};
		/**
		 * @param {string} raw Parameters.
		 * @param {string} command Command.
		 * @returns {void}
		 */
		const csi = (raw, command) => {
			const values = raw.split(";").map(value => Number(value || "0"));
			const first = values[0] ?? 0;
			const amount = first || 1;
			if (command === "m") {
				classes = AnsiTerminal.sgr(classes, values);
				return;
			}
			if (command === "A") row = Math.max(0, row - amount);
			else if (command === "B") row += amount;
			else if (command === "C") column += amount;
			else if (command === "D") column = Math.max(0, column - amount);
			else if (command === "G") column = Math.max(0, amount - 1);
			else if (command === "H" || command === "f") {
				row = Math.max(0, (values[0] || 1) - 1);
				column = Math.max(0, (values[1] || 1) - 1);
			} else if (command === "J") {
				if (first === 2) {
					lines.splice(0, lines.length, []);
					row = 0;
					column = 0;
				}
			} else if (command === "K") {
				ensure();
				const line = lines[row];
				if (line !== undefined) {
					if (first === 2) line.splice(0);
					else line.splice(column);
				}
			} else if (command === "s") {
				savedRow = row;
				savedColumn = column;
			} else if (command === "u") {
				row = savedRow;
				column = savedColumn;
			}
		};
		const esc = String.fromCharCode(27);
		const bell = String.fromCharCode(7);
		const backspace = String.fromCharCode(8);
		const pattern = new RegExp(`${esc}\\]8;;([^${esc}${bell}]*)(?:${bell}|${esc}\\\\)|${esc}\\]8;;(?:${bell}|${esc}\\\\)|${esc}\\[([0-9;?]*)([A-Za-z])|\\r|\\n|${backspace}`, "g");
		let index = 0; /** @type {RegExpExecArray|null} */
		let match;
		/**
		 * @param {string} text Literal text.
		 * @returns {void}
		 */
		const literal = text => [...text].forEach(write);
		while ((match = pattern.exec(input)) !== null) {
			literal(input.slice(index, match.index));
			index = pattern.lastIndex;
			if (match[0] === "\r") column = 0;
			else if (match[0] === "\n") {
				row += 1;
				column = 0;
				ensure();
			} else if (match[0] === "\x08") column = Math.max(0, column - 1);
			else if (match[1] !== undefined) link = match[1];
			else if (match[0].startsWith("\x1b]8;;")) link = "";
			else csi(match[2] ?? "", match[3] ?? "");
		}
		literal(input.slice(index));
		return AnsiTerminal.dom(lines);
	};
	/**
	 * @param {string[]} current Existing classes.
	 * @param {number[]} params SGR codes.
	 * @returns {string[]} Updated classes.
	 */
	static sgr = (current, params) => {
		let result = [...current];
		const colors = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
		params.forEach(code => {
			if (code === 0) result = [];
			else if (code === 1) result.push("ansi-bold");
			else if (code === 2) result.push("ansi-dim");
			else if (code === 3) result.push("ansi-italic");
			else if (code === 4) result.push("ansi-underline");
			else if (code >= 30 && code <= 37) {
				const color = colors[code - 30];
				if (color !== undefined) result = result.filter(value => !value.startsWith("ansi-fg-")).concat(`ansi-fg-${color}`);
			} else if (code === 39) result = result.filter(value => !value.startsWith("ansi-fg-"));
		});
		return [...new Set(result)];
	};
	/**
	 * @param {Cell[][]} lines Terminal cells.
	 * @returns {Node[]} Rendered nodes.
	 */
	static dom = lines => {
		/** @type {Node[]} */
		const nodes = [];
		lines.forEach((line, row) => {
			/** @type {{key:string,node:HTMLElement,text:string}|null} */
			let run = null;
			const flush = () => {
				if (run !== null) run.node.textContent = run.text;
				run = null;
			};
			line.forEach(cell => {
				const key = `${cell.link}\u0000${cell.classes.join(" ")}`;
				if (run !== null && run.key === key) {
					run.text += cell.char;
					return;
				}
				flush();
				/** @type {HTMLElement} */
				const node = cell.link ? document.createElement("a") : document.createElement("span");
				if (node instanceof HTMLAnchorElement) {
					node.href = cell.link;
					node.rel = "noreferrer";
					node.target = "_blank";
				}
				node.className = cell.classes.join(" ");
				nodes.push(node);
				run = {
					key,
					node,
					text: cell.char
				};
			});
			flush();
			if (row < lines.length - 1) nodes.push(document.createTextNode("\n"));
		});
		return nodes;
	};
	/**
	 * @param {string} input Raw terminal output.
	 * @returns {void}
	 */
	render(input) {
		this.text = input;
		const element = this.element;
		const follow = element.scrollHeight - element.scrollTop - element.clientHeight <= AnsiTerminal.followSlack;
		const place = element.scrollTop;
		element.replaceChildren(...AnsiTerminal.nodes(input));
		element.scrollTop = follow ? element.scrollHeight : place;
	}
}
export {
	AnsiTerminal
};
