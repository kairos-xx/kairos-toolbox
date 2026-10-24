/**
 * Builds an input form from a command's parameter specification, so the
 * palette and the URI screen can offer every district command without a hand-made
 * form for each one.
 */
import {
	Kit
} from "./kit.js";
/**
 * @typedef {{name:string,label:string,kind:string,required:boolean,default:string,choices:string[],placeholder:string,minimum:number,maximum:number}} Param
 * @typedef {{id:string,agent:string,cwd:string,command:string}} District
 * @typedef {{form:HTMLFormElement,read:()=>Record<string,string>}} Built
 */
/** Build and read command forms. */
class Forms {
	/**
	 * Build a form with one labelled control per parameter.
	 * @param {Param[]} params The parameters.
	 * @param {District[]} lakes Live lakes, for district parameters.
	 * @returns {Built} The form and a function that reads its values.
	 */
	static build = (params, lakes) => {
		const form = /** @type {HTMLFormElement} */ (Kit.el("form", "f-form"));
		/** @type {Map<string,HTMLInputElement>} */
		const controls = new Map();
		params.forEach(param => {
			const field = Kit.el("label", `f-field f-${param.kind}`);
			const control = Forms.control(param, lakes);
			controls.set(param.name, control);
			if (param.kind === "flag") field.append(control, Kit.el("span", "f-name", param.label));
			else field.append(Kit.el("span", "f-name", param.label), control);
			if (param.kind === "session" && lakes.length === 0) field.append(Kit.el("span", "f-hint", "No live district yet. Start one with New district."));
			form.append(field);
		});
		return {
			form,
			read: () => Object.fromEntries([...controls].map(([name, control]) => [name, Forms.value(control)]))
		};
	};
	/**
	 * Build the control for one parameter.
	 * @param {Param} param The parameter.
	 * @param {District[]} lakes Live lakes.
	 * @returns {HTMLInputElement} The control.
	 */
	static control = (param, lakes) => {
		if (param.kind === "choice") return Forms.select(param, param.choices.map(choice => [choice, choice]));
		if (param.kind === "session") return Forms.select(param, [
			["", "Choose a district"],
			...lakes.map(district => [district.id, `${district.id} · ${district.agent === "" ? "your own" : district.agent} · ${district.cwd}`])
		]);
		const input = /** @type {HTMLInputElement} */ (Kit.el("input", "f-input"));
		input.setAttribute("name", param.name);
		input.setAttribute("type", param.kind === "flag" ? "checkbox" : param.kind === "number" ? "number" : "text");
		if (param.kind === "text") {
			input.setAttribute("placeholder", param.placeholder);
			input.setAttribute("autocapitalize", "off");
			input.setAttribute("autocomplete", "off");
			input.setAttribute("autocorrect", "off");
			input.setAttribute("spellcheck", "false");
		}
		if (param.kind === "number") {
			input.setAttribute("inputmode", "numeric");
			if (param.maximum > 0) {
				input.setAttribute("min", String(param.minimum));
				input.setAttribute("max", String(param.maximum));
			}
		}
		if (param.required) input.setAttribute("aria-required", "true");
		input.value = param.default;
		return input;
	};
	/**
	 * Build a drop-down.
	 * @param {Param} param The parameter.
	 * @param {string[][]} options Value and text of each option.
	 * @returns {HTMLInputElement} The drop-down, typed like the other controls.
	 */
	static select = (param, options) => {
		const select = /** @type {HTMLInputElement} */ (Kit.el("select", "f-input"));
		select.setAttribute("name", param.name);
		if (param.required) select.setAttribute("aria-required", "true");
		options.forEach(([value, text]) => {
			const option = Kit.el("option", "", text);
			option.setAttribute("value", value ?? "");
			select.append(option);
		});
		select.value = param.default;
		return select;
	};
	/**
	 * Read one control as text.
	 * @param {HTMLInputElement} control The control.
	 * @returns {string} Its value; a switch is `1` when on and empty when off.
	 */
	static value = control => control.getAttribute("type") === "checkbox" ? control.checked ? "1" : "" : control.value;
	/**
	 * Find the first required field that was left empty.
	 * @param {Param[]} params The parameters.
	 * @param {Record<string,string>} values What the form holds.
	 * @returns {string} Its label, or an empty string when every required field has a value.
	 */
	static missing = (params, values) => params.find(param => param.required && (values[param.name] ?? "").trim() === "")?.label ?? "";
}
export {
	Forms
};
