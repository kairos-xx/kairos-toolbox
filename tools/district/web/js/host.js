/**
 * The overview's picture of the machine a district runs on: processor, memory and
 * disk as gauges, the busiest processes, the network and the state of the district
 * itself. It replaces a strip of totals that the lists below already repeated.
 */
import {
	Kit
} from "./kit.js";
import {
	Widgets
} from "./widgets.js";
/**
 * @typedef {{hostname:string,system:string,systemVersion:string,architecture:string,python:string,uptimeSeconds:number,cpu:{logical:number,physical:number|null,percent:number,perCore:number[],load:number[]|null},memory:{totalBytes:number,usedBytes:number,availableBytes:number,percent:number,swapTotalBytes:number,swapUsedBytes:number},disk:{path:string,totalBytes:number,usedBytes:number,freeBytes:number,percent:number},battery:{percent:number,plugged:boolean|null,secondsLeft:number|null}|null,networks:{interface:string,address:string,network:string}[],processes:{pid:number,name:string,rssBytes:number}[],district:{version:string,daemonRunning:boolean,databaseBytes:number,schemaVersion:number,stateDirectory:string}}} Host
 * @typedef {{sessions:number,active:number,idle:number,dead:number,clients:number}} Fleet
 */
/** Draw the host panel. */
class HostPanel {
	/** Share of a gauge from which it warns. */
	static warnFrom = 75;
	/** Share of a gauge from which it is critical. */
	static criticalFrom = 90;
	/** @type {WeakMap<HTMLElement,string>} What each panel last drew, so an unchanged host is not redrawn. */
	static drawn = new WeakMap();
	/**
	 * How worried a share of capacity should make a person.
	 * @param {number} percent Share in use, 0 to 100.
	 * @returns {string} `neutral`, `warn` or `bad`.
	 */
	static tone = percent => percent >= HostPanel.criticalFrom ? "bad" : percent >= HostPanel.warnFrom ? "warn" : "neutral";
	/**
	 * Describe the host as widgets.
	 * @param {Host} host The snapshot.
	 * @returns {import("./widgets.js").Widget[]} Gauges first, then the supporting facts.
	 */
	static widgets = host => {
		const {
			cpu,
			memory,
			disk,
			battery,
			district
		} = host;
		const cores = `${cpu.logical} ${cpu.logical === 1 ? "core" : "cores"}`;
		const load = cpu.load === null ? "" : ` · load ${cpu.load.map(value => value.toFixed(1)).join(" ")}`;
		/** @type {import("./widgets.js").MetricItem[]} */
		const gauges = [{
			label: "CPU",
			value: Kit.percent(cpu.percent),
			detail: `${cores}${load}`,
			tone: HostPanel.tone(cpu.percent),
			percent: cpu.percent
		}, {
			label: "Memory",
			value: Kit.percent(memory.percent),
			detail: `${Kit.bytes(memory.usedBytes)} of ${Kit.bytes(memory.totalBytes)} · swap ${Kit.bytes(memory.swapUsedBytes)}`,
			tone: HostPanel.tone(memory.percent),
			percent: memory.percent
		}, {
			label: "Disk",
			value: Kit.percent(disk.percent),
			detail: `${Kit.bytes(disk.freeBytes)} free`,
			tone: HostPanel.tone(disk.percent),
			percent: disk.percent
		}];
		if (battery !== null) gauges.push({
			label: "Battery",
			value: Kit.percent(battery.percent),
			detail: battery.plugged === true ? "plugged in" : battery.secondsLeft === null ? "on battery" : `${Kit.span(battery.secondsLeft)} left`,
			tone: battery.plugged === true || battery.percent > 20 ? "neutral" : "bad",
			percent: battery.percent
		});
		return [{
			type: "metrics",
			items: gauges
		}, {
			type: "facts",
			title: "Busiest processes",
			items: host.processes.map(item => ({
				label: `${item.name} · ${item.pid}`,
				value: Kit.bytes(item.rssBytes),
				tone: "neutral",
				href: ""
			}))
		}, {
			type: "facts",
			title: "Network",
			items: host.networks.map(item => ({
				label: item.interface,
				value: item.address,
				tone: "neutral",
				href: ""
			}))
		}, {
			type: "facts",
			title: "This district",
			items: [{
				label: "Version",
				value: district.version,
				tone: "neutral",
				href: ""
			}, {
				label: "Daemon",
				value: district.daemonRunning ? "running" : "stopped",
				tone: district.daemonRunning ? "good" : "warn",
				href: ""
			}, {
				label: "Database",
				value: Kit.bytes(district.databaseBytes),
				tone: "neutral",
				href: ""
			}, {
				label: "State",
				value: district.stateDirectory,
				tone: "neutral",
				href: ""
			}]
		}];
	};
	/**
	 * Draw the panel, leaving it untouched when nothing changed.
	 * @param {HTMLElement} root The panel's container.
	 * @param {Host} host The snapshot.
	 * @param {Fleet} fleet Counts of lakes.
	 * @param {number} now Current time in milliseconds.
	 * @returns {void}
	 */
	static render = (root, host, fleet, now) => {
		const signature = JSON.stringify([host, fleet]);
		if (HostPanel.drawn.get(root) === signature) return;
		HostPanel.drawn.set(root, signature);
		const head = Kit.el("header", "h-head");
		head.append(Kit.el("h2", "h-name", host.hostname), Kit.el("p", "h-meta", `${host.system} ${host.systemVersion} · ${host.architecture} · up ${Kit.span(host.uptimeSeconds)}`), Kit.link(`${fleet.sessions} ${fleet.sessions === 1 ? "district" : "lakes"} · ${fleet.active} live · ${fleet.clients} attached`, "#/sessions", "h-fleet"));
		const [gauges, ...facts] = HostPanel.widgets(host);
		if (gauges === undefined) throw new Error("Host gauges are missing.");
		const cores = Kit.el("ul", "h-cores");
		cores.setAttribute("aria-label", "Load on each core");
		host.cpu.perCore.forEach((percent, index) => {
			const core = Kit.el("li", "h-core");
			core.setAttribute("title", `Core ${index + 1}: ${Kit.percent(percent)}`);
			core.style.setProperty("--runtime-bar-level", String(Math.max(0, Math.min(100, percent)) / 100));
			cores.append(core);
		});
		const groups = Kit.el("div", "h-groups");
		groups.append(...facts.map(fact => /** @type {HTMLElement} */ (Widgets.widget(fact, now))));
		root.replaceChildren(head, /** @type {HTMLElement} */ (Widgets.widget(gauges, now) ?? Kit.el("div")), cores, groups);
	};
}
export {
	HostPanel
};
