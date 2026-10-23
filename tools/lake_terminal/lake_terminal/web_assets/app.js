/**
   * Live Lake Terminal dashboard with a stateful ANSI terminal emulator and observability widgets.
   */
/**
   * @typedef {{pid:number,parentPid:number,name:string,command:string,cpuPercent:number,rssBytes:number,readBytes:number,writeBytes:number,ports:number[]}} ProcessSnapshot
   */
/**
   * @typedef {{id:string,agent:string,agentSession:string,agentState:string,state:string,command:string,cwd:string,surface:string,editor:string,backend:string,workspace:string,attribution:string,hostId:string,output:string,clients:number,ageSeconds:number,lastActivityAt:string,reconnectable:boolean,processCount:number,cpuPercent:number,rssBytes:number,vmsBytes:number,readBytes:number,writeBytes:number,ports:number[],connections:string[],networkBytesSupported:boolean,partialResources:boolean,processes:ProcessSnapshot[],cpuHistory:number[],rssHistory:number[]}} SessionSnapshot
   */
/**
   * @typedef {{char:string,classes:string[],link:string}} Cell
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
   * Represent AnsiTerminal.
   */
class AnsiTerminal {
  /**
   * @param {HTMLElement} element Output element.
   */
  constructor(element) { this.element = element; this.text = ""; }
  /**
   * Emulate common ANSI cursor/erase/SGR/OSC-8 behavior.
   * @param {string} input Raw ANSI stream.
   * @returns {Node[]} Safe DOM nodes.
   */
  static nodes = input => {
    /** @type {Cell[][]} */ const lines = [[]];
    /** @type {string[]} */ let classes = [];
    let row = 0; let column = 0; let savedRow = 0; let savedColumn = 0; let link = "";
    const ensure = () => { while (lines.length <= row) lines.push([]); };
    /**
   * @param {string} char Character.
   * @returns {void}
   */
    const write = char => {
      ensure(); const line = lines[row]; if (line === undefined) return;
      while (line.length < column) line.push({char: " ", classes: [], link: ""});
      line[column] = {char, classes: [...classes], link}; column += 1;
    };
    /**
   * @param {string} raw Parameters.
   * @param {string} command Command.
   * @returns {void}
   */
    const csi = (raw, command) => {
      const values = raw.split(";").map(value => Number(value || "0")); const first = values[0] ?? 0; const amount = first || 1;
      if (command === "m") { classes = AnsiTerminal.sgr(classes, values); return; }
      if (command === "A") row = Math.max(0, row - amount);
      else if (command === "B") row += amount;
      else if (command === "C") column += amount;
      else if (command === "D") column = Math.max(0, column - amount);
      else if (command === "G") column = Math.max(0, amount - 1);
      else if (command === "H" || command === "f") { row = Math.max(0, (values[0] || 1) - 1); column = Math.max(0, (values[1] || 1) - 1); }
      else if (command === "J") { if (first === 2) { lines.splice(0, lines.length, []); row = 0; column = 0; } }
      else if (command === "K") { ensure(); const line = lines[row]; if (line !== undefined) { if (first === 2) line.splice(0); else line.splice(column); } }
      else if (command === "s") { savedRow = row; savedColumn = column; }
      else if (command === "u") { row = savedRow; column = savedColumn; }
    };
    const esc = String.fromCharCode(27); const bell = String.fromCharCode(7); const backspace = String.fromCharCode(8);
    const pattern = new RegExp(`${esc}\\]8;;([^${esc}${bell}]*)(?:${bell}|${esc}\\\\)|${esc}\\]8;;(?:${bell}|${esc}\\\\)|${esc}\\[([0-9;?]*)([A-Za-z])|\\r|\\n|${backspace}`, "g");
    let index = 0; /** @type {RegExpExecArray|null} */ let match;
    /**
   * @param {string} text Literal text.
   * @returns {void}
   */
    const literal = text => [...text].forEach(write);
    while ((match = pattern.exec(input)) !== null) {
      literal(input.slice(index, match.index)); index = pattern.lastIndex;
      if (match[0] === "\r") column = 0;
      else if (match[0] === "\n") { row += 1; column = 0; ensure(); }
      else if (match[0] === "\x08") column = Math.max(0, column - 1);
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
    let result = [...current]; const colors = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
    params.forEach(code => {
      if (code === 0) result = [];
      else if (code === 1) result.push("ansi-bold");
      else if (code === 2) result.push("ansi-dim");
      else if (code === 3) result.push("ansi-italic");
      else if (code === 4) result.push("ansi-underline");
      else if (code >= 30 && code <= 37) { const color = colors[code - 30]; if (color !== undefined) result = result.filter(value => !value.startsWith("ansi-fg-")).concat(`ansi-fg-${color}`); }
      else if (code === 39) result = result.filter(value => !value.startsWith("ansi-fg-"));
    });
    return [...new Set(result)];
  };
  /**
   * @param {Cell[][]} lines Terminal cells.
   * @returns {Node[]} Rendered nodes.
   */
  static dom = lines => {
    /** @type {Node[]} */ const nodes = [];
    lines.forEach((line, row) => {
      line.forEach(cell => {
        /** @type {HTMLElement} */ const node = cell.link ? document.createElement("a") : document.createElement("span");
        if (node instanceof HTMLAnchorElement) { node.href = cell.link; node.rel = "noreferrer"; node.target = "_blank"; }
        node.className = cell.classes.join(" "); node.textContent = cell.char; nodes.push(node);
      });
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
    this.element.replaceChildren(...AnsiTerminal.nodes(input));
    this.element.scrollTop = this.element.scrollHeight;
  }
}
/**
   * Represent Dashboard.
   */
class Dashboard {
  /**
   * Initialize dashboard state.
   */
  constructor() {
    /** @type {Map<string,SessionView>} */ this.cards = new Map();
    /** @type {SessionSnapshot[]} */ this.sessions = [];
    /** @type {WebSocket|null} */ this.socket = null;
    this.receivedAt = Date.now();
    this.connect();
    this.controls();
    setInterval(() => Dashboard.freshness(this.receivedAt), 1000);
  }
  /**
   * Connect to live updates.
   * @returns {void}
   */
  connect() {
    const scheme = location.protocol === "https:" ? "wss" : "ws"; this.socket = new WebSocket(`${scheme}://${location.host}/ws`);
    this.socket.addEventListener("open", () => Dashboard.connection(true));
    this.socket.addEventListener("close", () => { Dashboard.connection(false); setTimeout(() => this.connect(), 1000); });
    this.socket.addEventListener("message", event => { this.receivedAt = Date.now(); this.update(/** @type {DashboardPayload} */ (JSON.parse(String(event.data)))); });
  }
  /**
   * Bind filter and sort controls.
   * @returns {void}
   */
  controls() {
    ["#filter", "#state-filter", "#sort"].forEach(selector => { Dom.one(selector).addEventListener("input", () => this.renderSessions()); });
  }
  /**
   * Render all payload sections.
   * @param {DashboardPayload} payload Dashboard payload.
   * @returns {void}
   */
  update(payload) {
    this.sessions = payload.sessions;
    Dashboard.renderHeader(payload.server);
    Dashboard.renderStats(payload.totals);
    Dashboard.renderEvents(payload.events);
    Dashboard.renderCommands(payload.commands);
    this.renderSessions();
  }
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
   * Update the fleet stat strip.
   * @param {FleetTotals} totals Aggregate totals.
   * @returns {void}
   */
  static renderStats = totals => {
    Dom.one("#stat-sessions").textContent = String(totals.sessions);
    Dom.one("#stat-active").textContent = String(totals.active);
    Dom.one("#stat-idle").textContent = String(totals.idle);
    Dom.one("#stat-dead").textContent = String(totals.dead);
    Dom.one("#stat-cpu").textContent = `${totals.cpuPercent.toFixed(1)}%`;
    Dom.one("#stat-mem").textContent = `${Format.mib(totals.rssBytes)} MiB`;
    Dom.one("#stat-procs").textContent = String(totals.processes);
    Dom.one("#stat-consumers").textContent = String(totals.clients);
  };
  /**
   * Render the activity event feed.
   * @param {ActivityEvent[]} events Events.
   * @returns {void}
   */
  static renderEvents = events => {
    const feed = Dom.one("#activity-feed");
    feed.replaceChildren(...events.slice(-30).reverse().map(event => {
      const item = document.createElement("li");
      const time = document.createElement("time"); time.textContent = Format.clock(event.occurred_at); item.append(time);
      const type = document.createElement("span"); type.className = "event-type";  /** @type {CardData} */ (type.dataset).kind = event.event_type.split("_")[0] ?? "EVENT"; type.textContent = event.event_type.toLowerCase().replaceAll("_", " "); item.append(type);
      const who = document.createElement("span"); who.className = "event-actor"; who.textContent = [event.agent || "shell", event.session_id].filter(Boolean).join(" · "); item.append(who);
      const detail = document.createElement("span"); detail.className = "event-detail";
      detail.textContent = event.command || event.state || (event.exit_code === null ? "" : `exit ${event.exit_code}`);
      item.append(detail);
      return item;
    }));
  };
  /**
   * Render the persisted command history table.
   * @param {CommandRecord[]} commands Command records.
   * @returns {void}
   */
  static renderCommands = commands => {
    const body = Dom.one("#command-history");
    body.replaceChildren(...commands.slice(-30).reverse().map(record => {
      const row = document.createElement("tr");
      const time = document.createElement("td"); time.textContent = Format.clock(record.started_at); row.append(time);
      const session = document.createElement("td"); session.textContent = record.session_id; row.append(session);
      const agent = document.createElement("td"); agent.textContent = record.agent || "shell"; row.append(agent);
      const command = document.createElement("td"); command.className = "command-cell"; command.textContent = record.command; row.append(command);
      const exit = document.createElement("td");
      const exitData = /** @type {CardData} */ (exit.dataset);
      if (record.exit_code === null) { exit.textContent = "running"; exitData.state = "running"; }
      else { exit.textContent = String(record.exit_code); exitData.state = record.exit_code === 0 ? "ok" : "failed"; }
      row.append(exit);
      const duration = document.createElement("td"); duration.textContent = Format.duration(record.duration_seconds ?? null); row.append(duration);
      return row;
    }));
  };
  /**
   * Apply filters, sorting, and empty-state rendering.
   * @returns {void}
   */
  renderSessions() {
    const visible = this.sessions.filter(session => Dashboard.matches(session));
    Dashboard.sort(visible);
    const live = new Set(visible.map(session => session.id));
    [...this.cards.keys()].filter(id => !live.has(id)).forEach(id => { const view = this.cards.get(id); if (view !== undefined) view.card.remove(); this.cards.delete(id); });
    const grid = Dom.one("#sessions");
    visible.forEach(session => { this.updateCard(session); });
    visible.forEach(session => { const view = this.cards.get(session.id); if (view !== undefined) grid.append(view.card); });
    /** @type {HTMLElement} */ (Dom.one("#empty-state")).hidden = visible.length > 0;
  }
  /**
   * Test whether a session passes the active filters.
   * @param {SessionSnapshot} session Session.
   * @returns {boolean} Whether visible.
   */
  static matches = session => {
    const state = /** @type {HTMLSelectElement} */ (Dom.one("#state-filter")).value;
    if (state !== "" && session.state !== state) return false;
    const query = /** @type {HTMLInputElement} */ (Dom.one("#filter")).value.trim().toLowerCase();
    if (query === "") return true;
    return [session.id, session.agent, session.agentSession, session.cwd, session.command, session.workspace].join(" ").toLowerCase().includes(query);
  };
  /**
   * Sort sessions in place by the active sort order.
   * @param {SessionSnapshot[]} sessions Visible sessions.
   * @returns {void}
   */
  static sort = sessions => {
    const order = /** @type {HTMLSelectElement} */ (Dom.one("#sort")).value;
    const ranks = /** @type {{cpu:RankFn,memory:RankFn,agent:RankFn,newest:RankFn}} */ ({
      cpu: (a, b) => b.cpuPercent - a.cpuPercent,
      memory: (a, b) => b.rssBytes - a.rssBytes,
      agent: (a, b) => (a.agent || a.id).localeCompare(b.agent || b.id),
      newest: (a, b) => a.ageSeconds - b.ageSeconds
    });
    const rank = order === "cpu" ? ranks.cpu : order === "memory" ? ranks.memory : order === "agent" ? ranks.agent : ranks.newest;
    sessions.sort(rank);
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
     /** @type {CardData} */ (view.card.dataset).state = session.state;
    Dom.one(".agent", view.card).textContent = `${session.agent || "shell"} · ${session.id}`;
    Dom.one(".state", view.card).textContent = session.state;
    Dashboard.badge(Dom.one(".badge-age", view.card), `age ${Format.age(session.ageSeconds)}`);
    Dashboard.badge(Dom.one(".badge-clients", view.card), `${session.clients} ${session.clients === 1 ? "client" : "clients"}`);
    Dashboard.badge(Dom.one(".badge-provider", view.card), session.agentSession ? `${session.agent}@${session.agentSession}` : "");
    Dashboard.badge(Dom.one(".badge-editor", view.card), session.editor);
    Dom.one(".metadata", view.card).textContent = `${session.surface || "cli"} · ${session.backend} · ${session.cwd || "—"}`;
    Dom.one(".command", view.card).textContent = session.command || "idle";
    const meter = /** @type {HTMLMeterElement} */ (Dom.one(".cpu-meter", view.card)); meter.value = Math.min(100, session.cpuPercent);
    Dom.one(".cpu-value", view.card).textContent = `${session.cpuPercent.toFixed(1)}%`;
    Dom.one(".rss-value", view.card).textContent = `${Format.mib(session.rssBytes)} MiB`;
    Dom.one(".io-text", view.card).textContent = `IO ↓${Format.mib(session.readBytes)}/↑${Format.mib(session.writeBytes)} MiB · ${session.processCount} proc${session.partialResources ? " · partial" : ""}`;
    Sparkline.render(Dom.one(".cpu-spark", view.card), session.cpuHistory);
    Sparkline.render(Dom.one(".rss-spark", view.card), session.rssHistory);
    const ports = /** @type {HTMLElement} */ (Dom.one(".ports", view.card));
    ports.replaceChildren(...session.ports.map(port => { const chip = document.createElement("span"); chip.className = "port"; chip.textContent = `:${port}`; return chip; }));
    ports.hidden = session.ports.length === 0;
    Dom.one(".resources", view.card).textContent = `${session.processCount} processes · ${session.connections.length} sockets · network bytes ${session.networkBytesSupported ? "available" : "unavailable"}${session.lastActivityAt ? ` · last activity ${Format.clock(session.lastActivityAt)}` : ""}`;
    Dom.one(".process-tree", view.card).textContent = session.processes.map(process => `${process.pid} ← ${process.parentPid}  ${process.cpuPercent.toFixed(1)}%  ${Format.mib(process.rssBytes)} MiB  ${process.name}  ${process.command}`).join("\n") + (session.connections.length ? `\n\nSockets\n${session.connections.join("\n")}` : "");
    const attach = Dom.one(".attach", view.card); if (attach instanceof HTMLButtonElement) attach.disabled = !session.reconnectable;
    if (view.terminal.text !== session.output) view.terminal.render(session.output);
  }
  /**
   * Build a session card with wired actions.
   * @param {SessionSnapshot} session Session snapshot.
   * @returns {SessionView} Card view.
   * @throws {Error} When the template is invalid.
   */
  createCard(session) {
    const template = Dom.one("#session-template"); if (!(template instanceof HTMLTemplateElement)) throw new Error("Invalid session template");
    const source = template.content.firstElementChild; if (!(source instanceof HTMLElement)) throw new Error("Missing session card");
    const card = /** @type {HTMLElement} */ (source.cloneNode(true)); const terminalElement = Dom.one(".terminal", card); if (!(terminalElement instanceof HTMLElement)) throw new Error("Invalid terminal");
    const view = {card, terminal: new AnsiTerminal(terminalElement)}; Dom.one("#sessions").append(card);
    const more = /** @type {HTMLElement & {open?:boolean}} */ (Dom.one(".more", card));
    const copy = Dom.one(".copy", card); if (!(copy instanceof HTMLButtonElement)) throw new Error("Invalid copy button");
    copy.addEventListener("click", () => { navigator.clipboard.writeText(Dom.one(".command", card).textContent ?? ""); Dashboard.confirm(copy); more.open = false; });
    ["view", "history", "attach"].forEach(action => { Dom.one(`.${action}`, card).addEventListener("click", async () => { const response = await fetch(`/api/sessions/${session.id}/${action}`, {method: "POST"}); const payload = /**
   * @type {{command?:string}}
   */ (await response.json()); if (payload.command !== undefined) { await navigator.clipboard.writeText(payload.command); Dashboard.toast(`${action} command copied: ${payload.command}`); } else Dashboard.toast(`${action} failed for ${session.id}`, "bad"); more.open = false; }); });
    Dom.one(".kill", card).addEventListener("click", async () => { const response = await fetch(`/api/sessions/${session.id}/kill`, {method: "POST"}); Dashboard.toast(response.ok ? `Killed ${session.id}` : `Kill failed for ${session.id}`, response.ok ? "info" : "bad"); });
    const form = Dom.one(".exec", card); const input = /** @type {HTMLInputElement} */ (Dom.one(".exec-input", card)); const run = /** @type {HTMLButtonElement} */ (Dom.one(".exec-run", card)); const output = /** @type {HTMLElement} */ (Dom.one(".exec-output", card));
    form.addEventListener("submit", event => { event.preventDefault(); Dashboard.exec(session.id, input, run, output); });
    const typeInput = /** @type {HTMLInputElement} */ (Dom.one(".type-input", card)); const typeSend = /** @type {HTMLButtonElement} */ (Dom.one(".type-send", card));
    Dom.one(".type", card).addEventListener("submit", event => { event.preventDefault(); const text = typeInput.value.trim(); if (text === "") return; typeInput.value = ""; Dashboard.type(session.id, {text}, typeSend); });
    const keysMenu = /** @type {HTMLElement & {open?:boolean}} */ (Dom.one(".type-keys", card));
    const keys = /** @type {[string, string][]} */ ([["Enter", "key-enter"], ["Escape", "key-escape"], ["Tab", "key-tab"], ["C-c", "key-interrupt"], ["Up", "key-up"], ["Down", "key-down"]]);
    keys.forEach(([name, cls]) => { const chip = /** @type {HTMLButtonElement} */ (Dom.one(`.${cls}`, card)); chip.addEventListener("click", () => { Dashboard.type(session.id, {key: name}, chip); keysMenu.open = false; }); });
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
    const command = input.value.trim(); if (command === "") return;
    run.disabled = true; input.value = "";
    try {
      const response = await fetch(`/api/sessions/${id}/exec`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({command})});
      const payload = /**
   * @type {{detail?:string,output?:string,exitStatus?:number}}
   */ (await response.json());
      if (!response.ok) { Dashboard.toast(`Command failed: ${payload.detail ?? response.status}`, "bad"); return; }
      output.hidden = false;
       /** @type {CardData} */ (output.dataset).exit = String(payload.exitStatus);
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
      const response = await fetch(`/api/sessions/${id}/send`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      const payload = /**
   * @type {{detail?:string}}
   */ (await response.json());
      if (!response.ok) { Dashboard.toast(`Send failed: ${payload.detail ?? response.status}`, "bad"); return; }
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
  static badge = (badge, text) => { badge.textContent = text; badge.hidden = text === ""; };
  /**
   * Flash a successful inline confirmation on a button.
   * @param {HTMLButtonElement} button Button.
   * @param {string} [label] Confirmation text.
   * @returns {void}
   */
  static confirm = (button, label = "Copied") => { const previous = button.textContent; button.textContent = label; setTimeout(() => { button.textContent = previous; }, 1500); };
  /**
   * Append a transient status toast.
   * @param {string} message Message.
   * @param {string} [kind] Tone.
   * @returns {void}
   */
  static toast = (message, kind = "info") => {
    const toast = document.createElement("div"); toast.className = "toast";  /** @type {CardData} */ (toast.dataset).kind = kind; toast.textContent = message;
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
  static connection = connected => { const status = Dom.one(".connection"); if (!(status instanceof HTMLElement)) throw new Error("Invalid status"); const dataset = /**
   * @type {{connected?:string}}
   */ (status.dataset); dataset.connected = String(connected); Dom.one("#connection-label").textContent = connected ? "Live" : "Reconnecting"; };
}
if (typeof window !== "undefined") Reflect.construct(Dashboard, []);
export {AnsiTerminal, Dashboard, Format, Sparkline};
