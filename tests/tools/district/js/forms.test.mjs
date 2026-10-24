/** Tests for the forms built from a command's parameter specification. */
import assert from "node:assert/strict";
import test from "node:test";
import {
	Forms
} from "../../../../tools/district/web/js/forms.js";
import {
	buildSandbox
} from "./sandbox-dom.mjs";
buildSandbox();
/**
 * Describe a parameter.
 * @param {object} extra Fields to override.
 * @returns {import("../../../../tools/district/web/js/forms.js").Param} The parameter.
 */
const param = extra => ({
	name: "x",
	label: "X",
	kind: "text",
	required: false,
	default: "",
	choices: [],
	placeholder: "",
	minimum: 0,
	maximum: 0,
	...extra
});
const lakes = [{
	id: "ab12cd",
	agent: "devin",
	cwd: "/work",
	command: "vim"
}, {
	id: "ef34ab",
	agent: "",
	cwd: "/home",
	command: ""
}];
test("each parameter becomes a labelled control wrapped by its own label", () => {
	const built = Forms.build([param({
		name: "text",
		label: "Text",
		placeholder: "words"
	})], lakes);
	const label = built.form.querySelector("label");
	assert.equal(label?.querySelector(".f-name")?.textContent, "Text");
	const input = label?.querySelector("input");
	assert.equal(input?.getAttribute("placeholder"), "words");
	assert.equal(input?.getAttribute("name"), "text");
});
test("a district parameter offers every live district and says what each one is", () => {
	const built = Forms.build([param({
		name: "session",
		label: "District",
		kind: "session",
		required: true
	})], lakes);
	const options = built.form.querySelectorAll("option");
	assert.deepEqual(Array.from(options).map(option => option.getAttribute("value")), ["", "ab12cd", "ef34ab"]);
	assert.ok(options[1] !== undefined);
	assert.ok(options[2] !== undefined);
	assert.equal(options[1].textContent, "ab12cd · devin · /work");
	assert.equal(options[2].textContent, "ef34ab · your own · /home");
});
test("a choice offers exactly its choices and starts on its default", () => {
	const built = Forms.build([param({
		name: "kind",
		label: "Show",
		kind: "choice",
		choices: ["all", "agents", "own"],
		default: "agents"
	})], lakes);
	const select = built.form.querySelector("select");
	assert.deepEqual(Array.from(built.form.querySelectorAll("option")).map(option => option.getAttribute("value")), ["all", "agents", "own"]);
	assert.equal(select?.value, "agents");
});
test("reading the form returns text for every parameter, with a switch as 1 or empty", () => {
	const built = Forms.build([param({
		name: "text",
		label: "Text"
	}), param({
		name: "enter",
		label: "Enter",
		kind: "flag"
	}), param({
		name: "limit",
		label: "Rows",
		kind: "number",
		default: "25"
	})], lakes);
	built.form.querySelector("input")?.setAttribute("value", "ignored");
	const [text, enter, limit] = built.form.querySelectorAll("input");
	assert.ok(text !== undefined);
	assert.ok(enter !== undefined);
	assert.ok(limit !== undefined);
	text.value = "  ls -la  ";
	enter.checked = true;
	assert.deepEqual(built.read(), {
		text: "  ls -la  ",
		enter: "1",
		limit: "25"
	});
	enter.checked = false;
	const {enter: readEnter} = built.read();
	assert.equal(readEnter, "");
	assert.equal(limit.getAttribute("min"), null);
});
test("a number control carries its bounds so the keyboard is numeric", () => {
	const built = Forms.build([param({
		name: "limit",
		label: "Rows",
		kind: "number",
		default: "25",
		minimum: 1,
		maximum: 200
	})], lakes);
	const input = built.form.querySelector("input");
	assert.equal(input?.getAttribute("inputmode"), "numeric");
	assert.equal(input?.getAttribute("min"), "1");
	assert.equal(input?.getAttribute("max"), "200");
});
test("the first empty required field is named so the message can point at it", () => {
	const params = [param({
		name: "session",
		label: "District",
		kind: "session",
		required: true
	}), param({
		name: "command",
		label: "Command",
		required: true
	})];
	assert.equal(Forms.missing(params, {
		session: "",
		command: "ls"
	}), "District");
	assert.equal(Forms.missing(params, {
		session: "ab12cd",
		command: "  "
	}), "Command");
	assert.equal(Forms.missing(params, {
		session: "ab12cd",
		command: "ls"
	}), "");
});
test("with no live district the picker says so instead of staying silently empty", () => {
	const built = Forms.build([param({
		name: "session",
		label: "District",
		kind: "session",
		required: true
	})], []);
	assert.equal(built.form.querySelector(".f-hint")?.textContent, "No live district yet. Start one with New district.");
});
