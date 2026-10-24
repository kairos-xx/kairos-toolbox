/** Tests for the overview's host panel. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	HostPanel
} from "../../../../tools/district/web/js/host.js";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
const {
	document
} = buildSandbox();
const gib = 1024 ** 3;
/** @typedef {{label:string,value:string,detail:string,tone:string,percent:number|null}} MetricItem */
/** @typedef {{label:string,value:string,tone:string,href:string}} FactItem */
/**
 * A host snapshot with sensible values.
 * @param {object} [extra] Fields to override.
 * @returns {import("../../../../tools/district/web/js/host.js").Host} The snapshot.
 */
const host = (extra = {}) => ({
	hostname: "Mac",
	system: "macos",
	systemVersion: "26.3",
	architecture: "arm64",
	python: "3.13.1",
	uptimeSeconds: 2 * 86400 + 3 * 3600,
	cpu: {
		logical: 8,
		physical: 4,
		percent: 23.4,
		perCore: [10, 20, 30, 40, 5, 15, 25, 42],
		load: [2.1, 1.8, 1.6]
	},
	memory: {
		totalBytes: 16 * gib,
		usedBytes: 10 * gib,
		availableBytes: 6 * gib,
		percent: 62.5,
		swapTotalBytes: 4 * gib,
		swapUsedBytes: 1.5 * gib
	},
	disk: {
		path: "/Users/me",
		totalBytes: 500 * gib,
		usedBytes: 400 * gib,
		freeBytes: 100 * gib,
		percent: 80
	},
	battery: null,
	networks: [{
		interface: "en0",
		address: "192.168.1.5",
		network: "192.168.1.0/24"
	}],
	processes: [{
		pid: 10,
		name: "node",
		rssBytes: 2 * gib
	}, {
		pid: 20,
		name: "python",
		rssBytes: gib / 2
	}],
	district: {
		version: "1.0.1",
		daemonRunning: true,
		databaseBytes: 13 * 1024 * 1024,
		schemaVersion: 7,
		stateDirectory: "/Users/me/.district"
	},
	...extra
});
const fleet = {
	sessions: 18,
	active: 5,
	idle: 12,
	dead: 1,
	clients: 2
};
test("processor, memory and disk are gauges with their own words", () => {
	const gauges = HostPanel.widgets(host())[0];
	assert.ok(gauges !== undefined);
	assert.equal(gauges.type, "metrics");
	if (gauges.type !== "metrics")
		throw new Error("Expected metrics widget.");
	const [cpu, memory, disk] = /** @type {MetricItem[]} */ (gauges.items ?? []);
	assert.ok(cpu !== undefined);
	assert.ok(memory !== undefined);
	assert.ok(disk !== undefined);
	assert.equal(cpu.label, "CPU");
	assert.equal(cpu.value, "23%");
	assert.equal(cpu.detail, "8 cores · load 2.1 1.8 1.6");
	assert.equal(cpu.percent, 23.4);
	assert.equal(memory.value, "63%");
	assert.equal(memory.detail, "10 GiB of 16 GiB · swap 1.5 GiB");
	assert.equal(disk.detail, "100 GiB free");
});
test("a gauge warns from three quarters full and turns critical from nine tenths", () => {
	assert.equal(HostPanel.tone(50), "neutral");
	assert.equal(HostPanel.tone(75), "warn");
	assert.equal(HostPanel.tone(89.9), "warn");
	assert.equal(HostPanel.tone(90), "bad");
	const gauges = HostPanel.widgets(host())[0];
	assert.ok(gauges !== undefined);
	assert.equal(gauges.type, "metrics");
	if (gauges.type !== "metrics")
		throw new Error("Expected metrics widget.");
	assert.equal(/** @type {MetricItem[]} */ (gauges.items ?? [])[2]?.tone, "warn");
});
test("a battery appears only on a machine that has one", () => {
	assert.equal(HostPanel.widgets(host())[0]?.items?.length, 3);
	const withBattery = host({
		battery: {
			percent: 80,
			plugged: false,
			secondsLeft: 7800
		}
	});
	const batteryWidget = HostPanel.widgets(withBattery)[0];
	assert.ok(batteryWidget !== undefined);
	assert.equal(batteryWidget.type, "metrics");
	if (batteryWidget.type !== "metrics")
		throw new Error("Expected metrics widget.");
	const items = /** @type {MetricItem[]} */ (batteryWidget.items ?? []);
	assert.equal(items.length, 4);
	assert.equal(items[3]?.label, "Battery");
	assert.equal(items[3]?.detail, "2h 10m left");
	assert.equal(HostPanel.widgets(host({
		battery: {
			percent: 100,
			plugged: true,
			secondsLeft: null
		}
	}))[0]?.type === "metrics" ? /** @type {MetricItem[]} */ (HostPanel.widgets(host({battery: {percent: 100, plugged: true, secondsLeft: null}}))[0]?.items ?? [])[3]?.detail : undefined, "plugged in");
});
test("the busiest processes read name on the left and size on the right", () => {
	const widgets = HostPanel.widgets(host());
	const processes = widgets.find(widget => widget.type === "facts" && widget.title === "Busiest processes");
	assert.ok(processes !== undefined);
	if (processes.type !== "facts")
		throw new Error("Expected facts widget.");
	assert.deepEqual(/** @type {FactItem[]} */ (processes.items ?? []).map(item => [item.label, item.value]), [
		["node · 10", "2.0 GiB"],
		["python · 20", "512 MiB"]
	]);
});
test("the district block says whether the daemon runs and where the state lives", () => {
	const widgets = HostPanel.widgets(host());
	const district = widgets.find(widget => widget.type === "facts" && widget.title === "This district");
	assert.ok(district !== undefined);
	if (district.type !== "facts")
		throw new Error("Expected facts widget.");
	const byLabel = new Map(/** @type {FactItem[]} */ (district.items ?? []).map(item => [item.label, item]));
	assert.equal(byLabel.get("Version")?.value, "1.0.1");
	assert.equal(byLabel.get("Daemon")?.value, "running");
	assert.equal(byLabel.get("Daemon")?.tone, "good");
	assert.equal(byLabel.get("Database")?.value, "13 MiB");
	const stopped = HostPanel.widgets(host({
		district: {
			...host().district,
			daemonRunning: false
		}
	})).find(widget => widget.type === "facts" && widget.title === "This district");
	assert.ok(stopped !== undefined);
	if (stopped.type !== "facts")
		throw new Error("Expected facts widget.");
	assert.equal(/** @type {FactItem[]} */ (stopped.items ?? []).find(item => item.label === "Daemon")?.value, "stopped");
});
test("every private network address is listed with its interface", () => {
	const network = HostPanel.widgets(host()).find(widget => widget.type === "facts" && widget.title === "Network");
	assert.ok(network !== undefined);
	if (network.type !== "facts")
		throw new Error("Expected facts widget.");
	assert.deepEqual(/** @type {FactItem[]} */ (network.items ?? []).map(item => [item.label, item.value]), [
		["en0", "192.168.1.5"]
	]);
});
test("the header names the machine and sums up the lakes in one line", () => {
	const root = document.createElement("section");
	HostPanel.render(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), host(), fleet, 0);
	assert.equal(root.querySelector(".h-name")?.textContent, "Mac");
	assert.equal(root.querySelector(".h-meta")?.textContent, "macos 26.3 · arm64 · up 2d 3h");
	const summary = root.querySelector(".h-fleet");
	assert.equal(summary?.textContent, "18 lakes · 5 live · 2 attached");
	assert.equal(summary?.href, "#/sessions");
	assert.equal(root.querySelectorAll(".h-core").length, 8);
});
test("drawing the same facts again leaves the page untouched", () => {
	const root = document.createElement("section");
	HostPanel.render(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), host(), fleet, 0);
	const first = root.children[0];
	HostPanel.render(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), host(), fleet, 0);
	assert.equal(root.children[0], first);
	HostPanel.render(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), host({
		hostname: "Other"
	}), fleet, 0);
	assert.equal(root.querySelector(".h-name")?.textContent, "Other");
});
test("a single district is not pluralised", () => {
	const root = document.createElement("section");
	HostPanel.render(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)), host(), {
		sessions: 1,
		active: 1,
		idle: 0,
		dead: 0,
		clients: 0
	}, 0);
	assert.equal(root.querySelector(".h-fleet")?.textContent, "1 district · 1 live · 0 attached");
});
