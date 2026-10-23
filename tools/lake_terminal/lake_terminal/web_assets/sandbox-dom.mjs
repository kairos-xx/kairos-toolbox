/** Minimal deterministic DOM sandbox for dashboard behavioral tests. */
class FakeClassList {
	/** @param {FakeElement} element Owning element. */
	constructor(element) {
		this.element = element;
	}
	/**
	 * Handle add.
	 * @param {...string} names Classes to add.
	 * @returns {void}
	 */
	add(...names) {
		this.element.className = [...new Set([...this.values(), ...names])].join(" ");
	}
	/**
	 * Handle contains.
	 * @param {string} name Class name.
	 * @returns {boolean} Membership.
	 */
	contains(name) {
		return this.values().includes(name);
	}
	/**
	 * Handle values.
	 * @returns {string[]} Current classes.
	 */
	values() {
		return this.element.className.split(/\s+/u).filter(Boolean);
	}
}
/**
 * Represent FakeNode.
 */
class FakeNode extends EventTarget {
	/** @type {FakeElement|null} */
	parentNode;
	/** @type {string} */
	textContent;
	constructor() {
		super();
		this.parentNode = null;
		this.textContent = "";
	}
	/**
	 * Handle cloneNode.
	 * @returns {FakeNode} Deep clone.
	 */
	cloneNode() {
		const clone = new FakeNode();
		clone.textContent = this.textContent;
		return clone;
	}
	/** Remove this node from its parent. @returns {void} */
	remove() {
		if (this.parentNode !== null && "children" in this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this);
	}
}
/**
 * Represent FakeText.
 */
class FakeText extends FakeNode {
	/** @param {string} text Text value. */
	constructor(text) {
		super();
		this.textContent = text;
	}
	/**
	 * Handle cloneNode.
	 * @override
	 * @returns {FakeText} Cloned text.
	 */
	cloneNode() {
		return new FakeText(this.textContent);
	}
}
/**
 * Represent FakeElement.
 */
class FakeElement extends FakeNode {
	/** @type {FakeNode[]} */
	children;
	/** @type {Record<string, string>} */
	dataset;
	/** @param {string} tagName Element tag. */
	constructor(tagName = "div") {
		super();
		this.tagName = tagName.toUpperCase();
		this.children = [];
		this.className = "";
		this.id = "";
		this.dataset = {};
		this.disabled = false;
		this.href = "";
		this.rel = "";
		this.target = "";
		this.scrollTop = 0;
		this.scrollHeight = 0;
		this.classList = new FakeClassList(this);
	}
	/**
	 * Handle append.
	 * @param {...FakeNode} nodes Children.
	 * @returns {void}
	 */
	append(...nodes) {
		nodes.forEach(node => {
			node.parentNode = this;
			this.children.push(node);
		});
	}
	/**
	 * Handle replaceChildren.
	 * @param {...FakeNode} nodes Replacement children.
	 * @returns {void}
	 */
	replaceChildren(...nodes) {
		this.children = [];
		this.append(...nodes);
	}
	/**
	 * Handle querySelector.
	 * @param {string} selector Selector.
	 * @returns {FakeElement|null} Match.
	 */
	querySelector(selector) {
		const match = this.matches(selector) ? this : null;
		if (match !== null) return match;
		for (const child of this.children)
			if (child instanceof FakeElement) {
				const found = child.querySelector(selector);
				if (found !== null) return found;
			}
		return null;
	}
	/**
	 * Handle matches.
	 * @param {string} selector Selector.
	 * @returns {boolean} Whether this element matches.
	 */
	matches(selector) {
		if (selector.startsWith("#")) return this.id === selector.slice(1);
		if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
		return this.tagName.toLowerCase() === selector.toLowerCase();
	}
	/**
	 * Handle cloneNode.
	 * @override
	 * @returns {FakeElement} Deep clone.
	 */
	cloneNode() {
		const clone = new FakeElement(this.tagName);
		clone.className = this.className;
		clone.id = this.id;
		clone.dataset = {
			...this.dataset
		};
		clone.disabled = this.disabled;
		clone.textContent = this.textContent;
		this.children.forEach(child => {
			clone.append(child.cloneNode());
		});
		return clone;
	}
}
/**
 * Represent FakeAnchorElement.
 */
class FakeAnchorElement extends FakeElement {
	constructor() {
		super("a");
	}
	/**
	 * Handle cloneNode.
	 * @override
	 * @returns {FakeAnchorElement} Clone.
	 */
	cloneNode() {
		const clone = new FakeAnchorElement();
		clone.className = this.className;
		clone.textContent = this.textContent;
		return clone;
	}
}
/**
 * Represent FakeButtonElement.
 */
class FakeButtonElement extends FakeElement {
	constructor() {
		super("button");
	}
	/**
	 * Handle cloneNode.
	 * @override
	 * @returns {FakeButtonElement} Clone.
	 */
	cloneNode() {
		const clone = new FakeButtonElement();
		clone.className = this.className;
		clone.textContent = this.textContent;
		clone.disabled = this.disabled;
		return clone;
	}
}
/**
 * Represent FakeDocumentFragment.
 */
class FakeDocumentFragment extends FakeElement {
	constructor() {
		super("fragment");
	}
	/**
	 * Handle firstElementChild.
	 * @returns {FakeElement|null} First element child.
	 */
	get firstElementChild() {
		return this.children.find(child => child instanceof FakeElement) ?? null;
	}
}
/**
 * Represent FakeTemplateElement.
 */
class FakeTemplateElement extends FakeElement {
	constructor() {
		super("template");
		this.content = new FakeDocumentFragment();
	}
}
/**
 * Represent FakeDocument.
 */
class FakeDocument extends FakeElement {
	constructor() {
		super("document");
		this.body = new FakeElement("body");
		this.append(this.body);
	}
	/**
	 * Handle createElement.
	 * @param {string} tag Tag name.
	 * @returns {FakeElement} Element.
	 */
	createElement(tag) {
		if (tag === "a") return new FakeAnchorElement();
		if (tag === "button") return new FakeButtonElement();
		if (tag === "template") return new FakeTemplateElement();
		return new FakeElement(tag);
	}
	/**
	 * Handle createTextNode.
	 * @param {string} text Text.
	 * @returns {FakeText} Text node.
	 */
	createTextNode(text) {
		return new FakeText(text);
	}
}
/**
 * Install fake DOM globals.
 * @returns {{document: FakeDocument}} Sandbox handles.
 */
const buildSandbox = () => {
	const document = new FakeDocument();
	Object.assign(globalThis, {
		document,
		Node: FakeNode,
		HTMLElement: FakeElement,
		HTMLAnchorElement: FakeAnchorElement,
		HTMLButtonElement: FakeButtonElement,
		HTMLTemplateElement: FakeTemplateElement
	});
	return {
		document
	};
};
export {
	buildSandbox,
	FakeElement
};
