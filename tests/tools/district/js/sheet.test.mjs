/** Tests for the layered sheet that holds a command's form. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
import {
	Sheet
} from "../../../../tools/district/web/js/sheet.js";
const {
	document
} = buildSandbox();
/**
 * Press a button.
 * @param {EventTarget|null|undefined} button The button.
 * @returns {Promise<void>} Settles after the handler's microtasks.
 */
const press = async button => {
	button?.dispatchEvent(new Event("click"));
	await new Promise(resolve => {
	setTimeout(resolve, 0);
});
};
/**
 * Open a sheet with one action.
 * @param {() => Promise<void>} run What the action does.
 * @returns {{sheet:Sheet,closed:string[]}} The sheet and a log of closings.
 */
const opened = run => {
	/** @type {string[]} */
	const closed = [];
	const sheet = new Sheet(/** @type {HTMLElement} */ (/** @type {unknown} */ (document.createElement("div"))));
	sheet.open({
		title: "Stop a district",
		summary: "End a district and everything running in it.",
		body: /** @type {HTMLElement} */ (/** @type {unknown} */ (document.createElement("div"))),
		actions: [{
			label: "Cancel",
			tone: "quiet",
			run: null
		}, {
			label: "Stop district",
			tone: "danger",
			run
		}],
		onClose: () => closed.push("closed")
	});
	return {
		sheet,
		closed
	};
};
test("the sheet shows what it is for and how to leave it", () => {
	const {
		sheet
	} = opened(async () => {});
	assert.equal(sheet.dialog.querySelector("h2")?.textContent, "Stop a district");
	assert.equal(sheet.dialog.querySelector(".sheet-summary")?.textContent, "End a district and everything running in it.");
	assert.equal(sheet.dialog.getAttribute("open"), "");
	const buttons = sheet.dialog.querySelectorAll("button");
	assert.deepEqual(Array.from(buttons).map(button => button.textContent), ["Cancel", "Stop district"]);
	assert.equal(buttons[1]?.getAttribute("data-tone"), "danger");
});
test("an action that succeeds closes the sheet", async () => {
	let ran = 0;
	const {
		sheet,
		closed
	} = opened(async () => {
		ran += 1;
	});
	await press(sheet.dialog.querySelectorAll("button")[1]);
	assert.equal(ran, 1);
	assert.equal(sheet.dialog.getAttribute("open"), null);
	assert.deepEqual(closed, ["closed"]);
});
test("an action that fails keeps the sheet open and says why", async () => {
	const {
		sheet,
		closed
	} = opened(async () => {
		throw new Error("District ab12cd ended.");
	});
	await press(sheet.dialog.querySelectorAll("button")[1]);
	assert.equal(sheet.dialog.getAttribute("open"), "");
	assert.equal(sheet.dialog.querySelector(".sheet-error")?.textContent, "District ab12cd ended.");
	assert.equal(sheet.dialog.querySelector(".sheet-error")?.getAttribute("role"), "alert");
	assert.deepEqual(closed, []);
	const retry = sheet.dialog.querySelectorAll("button")[1];
	assert.ok(retry !== undefined);
	assert.equal(retry.disabled, false);
});
test("a second try clears the previous complaint", async () => {
	let attempts = 0;
	const {
		sheet
	} = opened(async () => {
		attempts += 1;
		if (attempts === 1) throw new Error("first");
	});
	const stop = sheet.dialog.querySelectorAll("button")[1];
	assert.ok(stop !== undefined);
	await press(stop);
	assert.equal(sheet.dialog.querySelector(".sheet-error")?.textContent, "first");
	await press(stop);
	assert.equal(sheet.dialog.getAttribute("open"), null);
});
test("cancelling closes the sheet without doing anything", async () => {
	const {
		sheet,
		closed
	} = opened(async () => {
		assert.fail("must not run");
	});
	await press(sheet.dialog.querySelectorAll("button")[0]);
	assert.equal(sheet.dialog.getAttribute("open"), null);
	assert.deepEqual(closed, ["closed"]);
});
test("buttons are disabled while an action is under way so it cannot run twice", async () => {
	/**
	 * Finish the pending test action.
	 * @type {() => void}
	 */
	let finish = () => {};
	const {
		sheet
	} = opened(() => new Promise(resolve => {
		finish = () => resolve();
	}));
	const buttons = sheet.dialog.querySelectorAll("button");
	buttons[1]?.dispatchEvent(new Event("click"));
	assert.equal(buttons[0]?.disabled, true);
	assert.equal(buttons[1]?.disabled, true);
	assert.equal(sheet.dialog.getAttribute("aria-busy"), "true");
	finish();
	await new Promise(resolve => {
	setTimeout(resolve, 0);
});
	assert.equal(sheet.dialog.getAttribute("aria-busy"), null);
});
