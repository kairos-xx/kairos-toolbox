/**
 * A layered sheet: the current screen stays visible behind it, it holds one
 * focused sub-task (a command's form), and closing it returns to where the
 * person was rather than navigating away.
 */
import {
	Kit
} from "./kit.js";
/**
 * @typedef {{label:string,tone:"primary"|"quiet"|"danger",run:(()=>Promise<void>)|null}} SheetAction
 * @typedef {{title:string,summary:string,body:HTMLElement,actions:SheetAction[],onClose:()=>void}} SheetContent
 */
/** One dialog that shows a titled form and its actions. */
class Sheet {
	/**
	 * @param {HTMLElement} host Where the sheet's dialog lives in the page.
	 */
	constructor(host) {
		this.dialog = Kit.el("dialog", "sheet");
		this.dialog.setAttribute("aria-labelledby", "sheet-title");
		this.onClose = () => {};
		this.dialog.addEventListener("close", () => this.closed());
		host.append(this.dialog);
	}
	/**
	 * Show a form and its actions.
	 * @param {SheetContent} content What to show.
	 * @returns {void}
	 */
	open(content) {
		this.onClose = content.onClose;
		const title = Kit.el("h2", "sheet-title", content.title);
		title.id = "sheet-title";
		const error = Kit.el("p", "sheet-error");
		error.setAttribute("role", "alert");
		error.hidden = true;
		const buttons = content.actions.map(action => Sheet.button(action, actionButtons => this.perform(action, actionButtons, error)));
		const foot = Kit.el("footer", "sheet-actions");
		foot.append(...buttons);
		this.dialog.replaceChildren(title, Kit.el("p", "sheet-summary", content.summary), content.body, error, foot);
		this.dialog.removeAttribute("aria-busy");
		if (typeof /** @type {HTMLDialogElement} */ (this.dialog).showModal === "function") /** @type {HTMLDialogElement} */ (this.dialog).showModal();
		else this.dialog.setAttribute("open", "");
	}
	/**
	 * Build one action button; the sheet supplies the others it must disable.
	 * @param {SheetAction} action The action.
	 * @param {(buttons:HTMLButtonElement[])=>Promise<void>} perform What pressing it does.
	 * @returns {HTMLButtonElement} The button.
	 */
	static button = (action, perform) => {
		const button = /** @type {HTMLButtonElement} */ (Kit.el("button", "sheet-button", action.label));
		button.setAttribute("type", "button");
		button.setAttribute("data-tone", String(action.tone));
		button.addEventListener("click", () => {
			const all = /** @type {HTMLButtonElement[]} */ (button.parentNode === null ? [button] : button.parentNode.querySelectorAll("button"));
			perform(all);
		});
		return button;
	};
	/**
	 * Run an action, keeping the sheet open and explaining when it fails.
	 * @param {SheetAction} action The action.
	 * @param {HTMLButtonElement[]} buttons Every button, to disable while busy.
	 * @param {HTMLElement} error Where to explain a failure.
	 * @returns {Promise<void>} Settles when the action is over.
	 */
	async perform(action, buttons, error) {
		if (action.run === null) {
			this.close();
			return;
		}
		error.hidden = true;
		error.textContent = "";
		buttons.forEach(button => {
			button.disabled = true;
		});
		this.dialog.setAttribute("aria-busy", "true");
		try {
			await action.run();
		} catch (failure) {
			error.textContent = failure instanceof Error ? failure.message : "Something went wrong.";
			error.hidden = false;
			buttons.forEach(button => {
				button.disabled = false;
			});
			this.dialog.removeAttribute("aria-busy");
			return;
		}
		this.dialog.removeAttribute("aria-busy");
		this.close();
	}
	/**
	 * Close the sheet and tell whoever opened it.
	 * @returns {void}
	 */
	close() {
		if (typeof /** @type {HTMLDialogElement} */ (this.dialog).close === "function") /** @type {HTMLDialogElement} */ (this.dialog).close();
		else this.dialog.removeAttribute("open");
		this.closed();
	}
	/**
	 * Report a closing once, whichever way it happened.
	 * @returns {void}
	 */
	closed() {
		const notify = this.onClose;
		this.onClose = () => {};
		notify();
	}
}
export {
	Sheet
};
