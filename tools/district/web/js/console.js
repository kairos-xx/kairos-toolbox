/** @typedef {{live?: string}} LiveData */
/** @typedef {{active?: string, state?: string}} TabData */
/** @typedef {{flow?: string, wide?: string}} ScreenData */
/** @typedef {{key?: string, repeat?: string}} KeyData */
/** @typedef {{kind?: string}} NoticeData */
/**
 * Full-screen, tabbed shells for a paired device: live output, typed input,
 * special keys, and a font that scales to the screen and follows rotation.
 */
import {
	AnsiTerminal
} from "./ansi.js";
/**
 * @typedef {{id:string,agent:string,state:string,output:string,command:string,cwd:string,backend:string,surface:string}} Shell
 */
/**
 * @typedef {{command_id:string,session_id:string,finished_at:string,exit_code:number|null}} Run
 */
/**
 * @typedef {{command_id:string,command:string,cwd:string,started_at:string,finished_at:string,exit_code:number|null,duration_seconds:number,output:string}} Entry
 */
/**
 * @typedef {{sessions:Shell[],commands:Run[]}} ConsolePayload
 */
/**
 * Look up a required element.
 * @param {string} selector CSS selector.
 * @returns {HTMLElement} The element.
 * @throws {Error} When it is missing.
 */
const one = selector => {
	const element = document.querySelector(selector);
	if (!(element instanceof HTMLElement)) throw new Error(`Missing ${selector}`);
	return element;
};
/**
 * Pure helpers, kept apart from the DOM so they can be tested alone.
 */
class Layout {
	/** Smallest and largest font the screen may use, in pixels. */
	static minimum = 8;
	static maximum = 18;
	/** Font size of the readable mode, in pixels. */
	static reading = 13;
	/** Narrowest terminal worth fitting, so a near-empty pane is not blown up. */
	static narrowest = 40;
	/** Step between font sizes, in pixels. */
	static step = 0.5;
	/**
	 * Widest line of a rendered pane.
	 * @param {string} text Rendered text.
	 * @returns {number} Columns.
	 */
	static columns = text => text.split("\n").reduce((widest, line) => Math.max(widest, line.trimEnd().length), 0);
	/**
	 * Font size at which `columns` monospace cells fill `width` pixels.
	 * @param {number} width Available width in pixels.
	 * @param {number} columns Columns to fit.
	 * @param {number} ratio Glyph width as a fraction of the font size.
	 * @returns {{size:number,wide:boolean}} Font size, and whether it is still too wide at the minimum.
	 */
	static fit = (width, columns, ratio) => {
		if (!(width > 0) || !(ratio > 0)) return {
			size: Layout.maximum,
			wide: false
		};
		const exact = width / (Math.max(columns, Layout.narrowest) * ratio);
		const size = Math.min(Layout.maximum, Math.max(Layout.minimum, Math.floor(exact / Layout.step) * Layout.step));
		return {
			size,
			wide: exact < Layout.minimum
		};
	};
	/**
	 * The readable mode: a fixed comfortable size, panning sideways when a line is wider.
	 * @param {number} width Available width in pixels.
	 * @param {number} columns Columns in the widest line.
	 * @param {number} ratio Glyph width as a fraction of the font size.
	 * @returns {{size:number,wide:boolean}} Font size, and whether lines overflow it.
	 */
	static read = (width, columns, ratio) => ({
		size: Layout.reading,
		wide: columns * ratio * Layout.reading > width
	});
	/**
	 * Whether a scroller is at, or within `slack` pixels of, its end.
	 * @param {{scrollHeight:number,scrollTop:number,clientHeight:number}} box Scroller metrics.
	 * @param {number} slack Tolerance in pixels.
	 * @returns {boolean} True when following the newest output.
	 */
	static atEnd = (box, slack) => box.scrollHeight - box.scrollTop - box.clientHeight <= slack;
}
/**
 * What a hook-backed shell has run, laid out like a terminal session: each
 * command after a prompt, its output, then how it ended.
 */
class Transcript {
	/** Longest stretch of one command's output shown, in characters; the end is kept. */
	static outputLimit = 4000;
	/** Most lines of one command shown before the rest is summarised. */
	static commandLines = 6;
	/**
	 * @param {Entry[]} entries Commands, oldest first.
	 * @returns {string} ANSI text for the terminal renderer.
	 */
	static text = entries => entries.map(Transcript.entry).join("\n\n");
	/**
	 * @param {Entry} entry One command.
	 * @returns {string} Prompt line, output and a status line.
	 */
	static entry = entry => {
		const lines = Transcript.clean(entry.command).split("\n");
		const shown = lines.slice(0, Transcript.commandLines);
		if (lines.length > shown.length) shown.push(`… ${lines.length - shown.length} more lines`);
		let text = `\x1b[1;32m$\x1b[0m \x1b[1m${shown.join("\n  ")}\x1b[0m`;
		const output = entry.output.replace(/\n+$/u, "");
		if (output !== "") {
			const hidden = output.length - Transcript.outputLimit;
			text += `\n${hidden > 0 ? `\x1b[2m… ${hidden} earlier characters not shown\x1b[0m\n${output.slice(hidden)}` : output}`;
		}
		return `${text}\n${Transcript.status(entry)}`;
	};
	/**
	 * @param {Entry} entry One command.
	 * @returns {string} How the command ended, or that it is still running.
	 */
	static status = entry => {
		if (entry.finished_at === "") return "\x1b[33m… running\x1b[0m";
		const seconds = entry.duration_seconds;
		const took = seconds >= 1 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds * 1000)}ms`;
		const colour = entry.exit_code === 0 ? "32" : "31";
		return `\x1b[2m[\x1b[0m\x1b[${colour}mexit ${entry.exit_code ?? "?"}\x1b[0m\x1b[2m · ${took}]\x1b[0m`;
	};
	/**
	 * Remove the escape character, so a command cannot restyle the screen.
	 * @param {string} text Command text.
	 * @returns {string} Text without control characters other than newline and tab.
	 */
	static clean = text => [...text].filter(character => {
		const code = character.codePointAt(0) ?? 0;
		return code === 9 || code === 10 || (code >= 32 && code !== 127);
	}).join("");
	/**
	 * Fingerprint of a session's runs, so the transcript is fetched only when one starts or ends.
	 * @param {Run[]} runs Every command in the payload.
	 * @param {string} session Session id.
	 * @returns {string} Signature.
	 */
	static signature = (runs, session) => JSON.stringify(runs.filter(run => run.session_id === session).map(run => [run.command_id, run.finished_at, run.exit_code]));
}
/**
 * Keys a hardware keyboard may send, by `KeyboardEvent.key`.
 */
class Keyboard {
	/** @type {Record<string,string>} */
	static named = {
		Enter: "Enter",
		Escape: "Escape",
		Tab: "Tab",
		Backspace: "BSpace",
		ArrowUp: "Up",
		ArrowDown: "Down",
		ArrowLeft: "Left",
		ArrowRight: "Right",
		Home: "Home",
		End: "End",
		PageUp: "PageUp",
		PageDown: "PageDown",
		Delete: "DC"
	};
	/**
	 * Translate a key press into what the session expects.
	 * @param {{key:string,ctrlKey:boolean,metaKey:boolean,altKey:boolean}} event Key event.
	 * @returns {{key?:string,text?:string}|null} A named key, literal text, or nothing to send.
	 */
	static translate = event => {
		if (event.metaKey || event.altKey) return null;
		if (event.ctrlKey) return /^[a-z]$/u.test(event.key.toLowerCase()) && event.key.length === 1 ? {
			key: `C-${event.key.toLowerCase()}`
		} : null;
		const named = Keyboard.named[event.key];
		if (named !== undefined) return {
			key: named
		};
		return [...event.key].length === 1 ? {
			text: event.key
		} : null;
	};
}
/**
 * Remembers which shells are open in this browser.
 */
class Tabs {
	static storageKey = "district.console.tabs";
	/**
	 * @returns {string[]} Saved shell ids; empty when storage is unavailable.
	 */
	static load = () => {
		try {
			const saved = JSON.parse(localStorage.getItem(Tabs.storageKey) ?? "[]");
			return Array.isArray(saved) ? saved.filter(id => typeof id === "string") : [];
		} catch {
			return [];
		}
	};
	static modeKey = "district.console.mode";
	/**
	 * @returns {"fit"|"read"} The remembered way of sizing text; fit by default.
	 */
	static loadMode = () => {
		try {
			return localStorage.getItem(Tabs.modeKey) === "read" ? "read" : "fit";
		} catch {
			return "fit";
		}
	};
	/**
	 * @param {"fit"|"read"} mode Way of sizing text to remember.
	 * @returns {void}
	 */
	static saveMode = mode => {
		try {
			localStorage.setItem(Tabs.modeKey, mode);
		} catch {
			// Without storage the choice lasts for this page only.
		}
	};
	/**
	 * @param {string[]} ids Shell ids to remember.
	 * @returns {void}
	 */
	static save = ids => {
		try {
			localStorage.setItem(Tabs.storageKey, JSON.stringify(ids));
		} catch {
			// Private windows may refuse storage; tabs then last for this page only.
		}
	};
}
/**
 * The console page.
 */
class Console {
	constructor() {
		this.screen = one("#screen");
		this.terminal = new AnsiTerminal(this.screen);
		/** @type {Map<string,Shell>} */
		this.shells = new Map();
		const requested = new URLSearchParams(location.search).get("session");
		this.ids = Tabs.load();
		if (requested !== null && requested !== "" && !this.ids.includes(requested)) this.ids.push(requested);
		this.active = requested !== null && requested !== "" ? requested : this.ids.at(-1) ?? "";
		this.following = true;
		this.opened = false;
		this.retry = 1000;
		this.signature = "";
		this.stale = true;
		this.readOnlyShown = false;
		this.mode = Tabs.loadMode();
		/** @type {Map<string,{signature:string,text:string,saved:boolean}>} */
		this.transcripts = new Map();
		/** @type {Set<string>} */
		this.loading = new Set();
		/** @type {Map<string,number>} */
		this.holdOff = new Map();
		this.ratio = 0.6;
		/** @type {number|undefined} */
		this.repeater = undefined;
		Tabs.save(this.ids);
		this.bind();
		this.showMode();
		this.measure();
		this.resize();
		this.connect();
		this.render();
	}
	/**
	 * Wire every control.
	 * @returns {void}
	 */
	bind() {
		one("#line").addEventListener("submit", event => {
			event.preventDefault();
			this.submit();
		});
		one("#keys").addEventListener("mousedown", event => event.preventDefault());
		one("#keys").addEventListener("pointerdown", event => this.press(event));
		["pointerup", "pointerleave", "pointercancel"].forEach(name => {
			one("#keys").addEventListener(name, () => this.release());
		});
		one("#keys").addEventListener("click", event => {
			if (event instanceof PointerEvent && event.detail === 0) this.press(event);
		});
		one("#add").addEventListener("click", () => this.pick());
		one("#zoom").addEventListener("click", () => this.toggle());
		one("#picker-close").addEventListener("click", () => /** @type {HTMLDialogElement} */ (one("#picker")).close());
		one("#latest").addEventListener("click", () => this.toEnd());
		this.screen.addEventListener("scroll", () => this.scrolled(), {
			passive: true
		});
		this.screen.addEventListener("keydown", event => this.type(event));
		window.addEventListener("resize", () => this.resize());
		window.addEventListener("orientationchange", () => setTimeout(() => this.resize(), 250));
		if (window.visualViewport) window.visualViewport.addEventListener("resize", () => this.resize());
		if (typeof ResizeObserver === "function") new ResizeObserver(() => this.fit()).observe(this.screen);
		if (document.fonts) document.fonts.ready.then(() => {
			this.measure();
			this.fit();
		});
	}
	/**
	 * Open the live feed, reconnecting with a growing delay.
	 * @returns {void}
	 */
	connect() {
		const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
		socket.addEventListener("open", () => {
			this.opened = true;
			this.retry = 1000;
			Console.live(true);
		});
		socket.addEventListener("message", message => {
			/** @type {ConsolePayload} */
			const payload = JSON.parse(String(message.data));
			this.shells = new Map(payload.sessions.map(shell => [shell.id, shell]));
			this.watch(payload.commands ?? []);
			this.render();
		});
		socket.addEventListener("close", () => {
			Console.live(false);
			if (!this.opened) one("#link-label").textContent = "Not paired";
			setTimeout(() => this.connect(), this.retry);
			this.retry = Math.min(this.retry * 2, 5000);
		});
	}
	/**
	 * @param {boolean} connected Whether the feed is up.
	 * @returns {void}
	 */
	static live = connected => {
		/** @type {LiveData} */
		(one("#link").dataset).live = String(connected);
		one("#link-label").textContent = connected ? "Live" : "Reconnecting";
	};
	/**
	 * Redraw whatever changed.
	 * @returns {void}
	 */
	render() {
		this.tabs();
		this.output();
		this.dock();
	}
	/**
	 * Rebuild the tab strip only when its content changed, so it never jumps.
	 * @returns {void}
	 */
	tabs() {
		const rows = this.ids.map(id => ({
			id,
			shell: this.shells.get(id)
		}));
		const signature = JSON.stringify([this.active, rows.map(row => [row.id, row.shell?.state ?? "MISSING", row.shell?.agent ?? ""])]);
		if (signature === this.signature) return;
		this.signature = signature;
		const strip = one("#tabs");
		const place = strip.scrollLeft;
		strip.replaceChildren(...rows.map(row => this.tab(row.id, row.shell)));
		strip.scrollLeft = place;
		strip.querySelector('[data-active="true"]')?.scrollIntoView({
			block: "nearest",
			inline: "nearest"
		});
	}
	/**
	 * Build one shell tab and its close control.
	 * @param {string} id Shell id.
	 * @param {Shell|undefined} shell Live data, if the shell still exists.
	 * @returns {HTMLElement} One tab.
	 */
	tab(id, shell) {
		const wrap = document.createElement("div");
		wrap.className = "tab-group";
		wrap.setAttribute("role", "presentation");
		/** @type {TabData} */
		(wrap.dataset).active = String(id === this.active); /** @type {TabData} */
		(wrap.dataset).state = shell?.state ?? "MISSING";
		const select = document.createElement("button");
		select.type = "button";
		select.className = "tab";
		select.setAttribute("role", "tab");
		select.setAttribute("aria-selected", String(id === this.active));
		const dot = document.createElement("span");
		dot.className = "tab-dot";
		dot.setAttribute("aria-hidden", "true");
		const name = document.createElement("span");
		name.className = "tab-name";
		name.textContent = shell?.agent || "shell";
		const code = document.createElement("span");
		code.className = "tab-id";
		code.textContent = id;
		select.append(dot, name, code);
		select.addEventListener("click", () => this.activate(id));
		const close = document.createElement("button");
		close.type = "button";
		close.className = "tab-close";
		close.setAttribute("aria-label", `Close ${shell?.agent || "shell"} ${id}`);
		close.textContent = "×";
		close.addEventListener("click", () => this.close(id));
		wrap.append(select, close);
		return wrap;
	}
	/**
	 * Show the active shell's output, keeping the reader's place.
	 * @returns {void}
	 */
	output() {
		const shell = this.shells.get(this.active);
		const streamed = shell !== undefined && Console.streams(shell);
		const kept = this.transcripts.get(this.active);
		const text = streamed ? kept?.text ?? "" : shell?.output ?? "";
		/** @type {ScreenData} */
		(this.screen.dataset).flow = streamed ? "wrap" : "grid";
		const empty = one("#empty");
		const note = Console.note(shell, this.active, this.shells.size, kept);
		empty.hidden = note === "";
		empty.textContent = note;
		if (!this.stale && this.terminal.text === text) return;
		this.stale = false;
		this.terminal.render(text);
		this.fit();
		if (this.following) this.toEnd();
	}
	/**
	 * Say why the screen is empty, when it is.
	 * @param {Shell|undefined} shell The active shell, if it exists.
	 * @param {string} active Active shell id.
	 * @param {number} known How many shells the server has listed.
	 * @param {{text:string}|undefined} kept The fetched transcript, for streamed shells.
	 * @returns {string} A note, or an empty string when there is output to show.
	 */
	static note = (shell, active, known, kept) => {
		if (active === "") return "No shell open. Tap + to choose one.";
		if (shell === undefined) return known === 0 ? "Connecting to the shell list…" : "This shell has ended.";
		if (Console.streams(shell)) return kept === undefined ? "Loading what this agent has run…" : kept.text === "" ? "Nothing run yet. Commands and their output appear here as this agent runs them." : "";
		return shell.output === "" ? "No output yet." : "";
	};
	/** Wait after a failed transcript fetch before trying again, in milliseconds. */
	static retryAfter = 5000;
	/**
	 * Explain a stream-only shell: why it cannot be typed into, and why its output may be missing.
	 * @param {boolean|undefined} saved Whether the privacy policy saves command output; undefined until known.
	 * @returns {string} Status line.
	 */
	static readOnlyNote = saved => `Read-only: this agent runs commands outside a terminal pane, so you can watch them but not type.${saved === false ? " Output is not saved by default; set persist_output to true in ~/.district/privacy.json to see it." : ""}`;
	/**
	 * Whether a shell is shown as a stream of commands rather than a terminal pane.
	 * Hook-backed agents run their commands out of process, so there is no pane.
	 * @param {Shell} shell Shell.
	 * @returns {boolean} True for anything that is not a tmux session.
	 */
	static streams = shell => shell.backend !== "tmux";
	/**
	 * Whether a shell can be read from and typed into: only tmux panes can.
	 * @param {Shell} shell Shell.
	 * @returns {boolean} True for a live tmux session.
	 */
	static interactive = shell => shell.backend === "tmux" && shell.state !== "DEAD";
	/**
	 * Disable the dock when there is nothing to type into.
	 * @returns {void}
	 */
	dock() {
		const shell = this.shells.get(this.active);
		const usable = shell !== undefined && Console.interactive(shell);
		document.querySelectorAll("#keys button, #line input, #line button").forEach(control => {
			/** @type {HTMLButtonElement} */
			(control).disabled = !usable;
		});
		const readOnly = shell !== undefined && Console.streams(shell);
		if (readOnly) Console.say(Console.readOnlyNote(this.transcripts.get(this.active)?.saved), "info");
		else if (this.readOnlyShown) Console.say("");
		this.readOnlyShown = readOnly;
	}
	/**
	 * Choose a tab.
	 * @param {string} id Shell id.
	 * @returns {void}
	 */
	activate(id) {
		this.active = id;
		this.following = true;
		this.signature = "";
		this.stale = true;
		history.replaceState(null, "", id === "" ? location.pathname : `?session=${encodeURIComponent(id)}`);
		this.render();
		this.toEnd();
	}
	/**
	 * Close a tab, moving to its neighbour.
	 * @param {string} id Shell id.
	 * @returns {void}
	 */
	close(id) {
		const index = this.ids.indexOf(id);
		this.ids = this.ids.filter(other => other !== id);
		Tabs.save(this.ids);
		if (id === this.active) this.activate(this.ids[Math.min(index, this.ids.length - 1)] ?? "");
		else {
			this.signature = "";
			this.render();
		}
	}
	/**
	 * Offer the shells that are not open yet.
	 * @returns {void}
	 */
	pick() {
		const list = one("#picker-list");
		const rest = [...this.shells.values()].filter(shell => !this.ids.includes(shell.id));
		list.replaceChildren(...(rest.length === 0 ? [Object.assign(document.createElement("li"), {
			className: "picker-none",
			textContent: "Every shell is already open."
		})] : rest.map(shell => {
			const item = document.createElement("li");
			const choose = document.createElement("button");
			choose.type = "button";
			choose.className = "picker-item";
			const name = document.createElement("span");
			name.className = "picker-name";
			name.textContent = `${shell.agent || "shell"} · ${shell.state.toLowerCase()}`;
			const meta = document.createElement("span");
			meta.className = "picker-meta";
			meta.textContent = `${shell.id} ${shell.cwd || shell.command}`;
			choose.append(name, meta);
			choose.addEventListener("click", () => {
				this.ids.push(shell.id);
				Tabs.save(this.ids); /** @type {HTMLDialogElement} */
				(one("#picker")).close();
				this.activate(shell.id);
			});
			item.append(choose);
			return item;
		})));
		/** @type {HTMLDialogElement} */
		(one("#picker")).showModal();
	}
	/**
	 * Send the typed line followed by Enter.
	 * @returns {void}
	 */
	submit() {
		const input = /** @type {HTMLInputElement} */ (one("#text"));
		const text = input.value;
		if (text === "") {
			this.send({
				key: "Enter"
			});
			return;
		}
		this.send({
			text,
			enter: true
		}).then(sent => {
			if (sent) input.value = "";
		});
		input.focus();
	}
	/**
	 * Send the virtual key selected from the key row.
	 * @param {PointerEvent} event Press on the key row.
	 * @returns {void}
	 */
	press(event) {
		const button = event.target instanceof Element ? event.target.closest("button[data-key]") : null;
		if (!(button instanceof HTMLButtonElement) || button.disabled) return;
		event.preventDefault();
		const key = /** @type {KeyData} */ (button.dataset).key ?? "";
		this.send({
			key
		});
		this.release();
		if ( /** @type {KeyData} */ (button.dataset).repeat !== undefined) this.repeater = window.setTimeout(() => {
			this.repeater = window.setInterval(() => this.send({
				key
			}), 80);
		}, 400);
	}
	/**
	 * Stop a held key from repeating.
	 * @returns {void}
	 */
	release() {
		if (this.repeater === undefined) return;
		clearTimeout(this.repeater);
		clearInterval(this.repeater);
		this.repeater = undefined;
	}
	/**
	 * Forward a hardware key press made while the screen has focus.
	 * @param {KeyboardEvent} event Key event.
	 * @returns {void}
	 */
	type(event) {
		const action = Keyboard.translate(event);
		if (action === null) return;
		event.preventDefault();
		this.send(action);
	}
	/**
	 * Send input to the active shell.
	 * @param {{text?:string,key?:string,enter?:boolean}} body Request body.
	 * @returns {Promise<boolean>} Whether the shell accepted it.
	 */
	async send(body) {
		if (this.active === "") return false;
		try {
			const response = await fetch(`/api/sessions/${encodeURIComponent(this.active)}/send`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json"
				},
				body: JSON.stringify(body)
			});
			if (response.ok) {
				Console.say("");
				return true;
			}
			const detail = await response.json().catch(() => ({
				detail: ""
			}));
			Console.say(`Not sent: ${detail.detail || response.status}`);
		} catch {
			Console.say("Not sent: the connection dropped");
		}
		return false;
	}
	/**
	 * @param {string} message Status line; empty clears it.
	 * @param {"bad"|"info"} [kind] A failure, or a plain note.
	 * @returns {void}
	 */
	static say = (message, kind = "bad") => {
		const notice = one("#notice");
		if (notice.textContent === message) return;
		notice.textContent = message; /** @type {NoticeData} */
		(notice.dataset).kind = message === "" ? "" : kind;
	};
	/**
	 * Fetch the active shell's transcript when it has started or finished a command.
	 * @param {Run[]} runs Every command in the latest update.
	 * @returns {void}
	 */
	watch(runs) {
		const shell = this.shells.get(this.active);
		if (shell === undefined || !Console.streams(shell)) return;
		const signature = Transcript.signature(runs, this.active);
		if (this.transcripts.get(this.active)?.signature === signature || this.loading.has(this.active)) return;
		if ((this.holdOff.get(this.active) ?? 0) > Date.now()) return;
		this.load(this.active, signature);
	}
	/**
	 * Fetch and cache one shell transcript for the current run signature.
	 * @param {string} id Shell id.
	 * @param {string} signature Fingerprint of the runs this fetch answers.
	 * @returns {Promise<void>} Nothing.
	 */
	async load(id, signature) {
		this.loading.add(id);
		try {
			const response = await fetch(`/api/sessions/${encodeURIComponent(id)}/transcript`);
			if (!response.ok) {
				this.holdOff.set(id, Date.now() + Console.retryAfter);
				return;
			}
			/** @type {{commands:Entry[],persistOutput:boolean}} */
			const body = await response.json();
			this.transcripts.set(id, {
				signature,
				text: Transcript.text(body.commands),
				saved: body.persistOutput
			});
			if (id === this.active) {
				this.stale = true;
				this.render();
			}
			this.holdOff.delete(id);
		} catch {
			this.holdOff.set(id, Date.now() + Console.retryAfter);
		} finally {
			this.loading.delete(id);
		}
	}
	/**
	 * Track whether the reader is following the newest output.
	 * @returns {void}
	 */
	scrolled() {
		this.following = Layout.atEnd(this.screen, AnsiTerminal.followSlack);
		one("#latest").hidden = this.following;
	}
	/**
	 * Scroll the terminal screen to its newest output and resume following.
	 * @returns {void} Nothing.
	 */
	toEnd() {
		this.screen.scrollTop = this.screen.scrollHeight;
		this.following = true;
		one("#latest").hidden = true;
	}
	/**
	 * Measure how wide one glyph is, as a fraction of the font size.
	 * @returns {void}
	 */
	measure() {
		const probe = document.createElement("span");
		probe.textContent = "0".repeat(40);
		probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:100px ${getComputedStyle(this.screen).fontFamily}`;
		document.body.append(probe);
		const width = probe.getBoundingClientRect().width;
		probe.remove();
		if (width > 0) this.ratio = width / 40 / 100;
	}
	/**
	 * Scale the font so the widest line fits the screen, which is also what
	 * makes rotation work: a new width gives a new size.
	 * @returns {void}
	 */
	fit() {
		const style = getComputedStyle(this.screen);
		const width = this.screen.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
		const columns = Layout.columns(this.screen.textContent ?? "");
		const wrapped = /** @type {ScreenData} */ (this.screen.dataset).flow === "wrap";
		const {
			size,
			wide
		} = wrapped ? {
			size: Layout.reading,
			wide: false
		} : this.mode === "read" ? Layout.read(width, columns, this.ratio) : Layout.fit(width, columns, this.ratio);
		const follow = this.following;
		this.screen.style.fontSize = `${size}px`;
		/** @type {ScreenData} */
		(this.screen.dataset).wide = String(wide);
		if (follow) this.screen.scrollTop = this.screen.scrollHeight;
	}
	/**
	 * Switch between fitting whole lines and reading at a comfortable size.
	 * @returns {void}
	 */
	toggle() {
		this.mode = this.mode === "fit" ? "read" : "fit";
		Tabs.saveMode(this.mode);
		this.showMode();
		this.fit();
	}
	/**
	 * Label the toggle with what a tap will do not, what is on.
	 * @returns {void}
	 */
	showMode() {
		const button = one("#zoom");
		const fitting = this.mode === "fit";
		button.textContent = fitting ? "Fit" : "Read";
		button.setAttribute("aria-pressed", String(fitting));
		button.setAttribute("aria-label", fitting ? "Showing whole lines. Switch to a readable size." : "Showing a readable size. Switch to fit whole lines.");
	}
	/**
	 * Fit the page to the visible area, which shrinks while the keyboard is up.
	 * @returns {void}
	 */
	resize() {
		const height = window.visualViewport ? window.visualViewport.height : window.innerHeight;
		document.documentElement.style.setProperty("--primary-app-height", `${height}px`);
		this.fit();
	}
}
if (typeof window !== "undefined") Reflect.construct(Console, []);
export {
	Console,
	Keyboard,
	Layout,
	Tabs,
	Transcript
};
