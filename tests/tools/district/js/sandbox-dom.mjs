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
/** Dataset fields used by the deterministic dashboard tests. */
class FakeDataset {
	constructor() {
		this.key = "";
		this.exit = "";
		this.tab = "";
		this.state = "";
		this.connected = "";
		this.flow = "";
		this.repeat = "";
		this.tone = "";
		this.danger = "";
		this.ok = "";
		this.enter = "";
	}
}
/**
 * Reflect an element's `data-*` attributes as camelCase properties, the way a
 * real `DOMStringMap` does, so `dataset.tone = "good"` is visible to
 * `getAttribute("data-tone")` and vice versa.
 * @param {FakeElement} element Owning element.
 * @returns {FakeDataset} The live view.
 */
const datasetOf = element => {
	/**
	 * Translate a dataset key into its attribute name.
	 * @param {string|symbol} key Property key.
	 * @returns {string} The `data-*` attribute name.
	 */
	const attribute = key => `data-${String(key).replace(/[A-Z]/gu, letter => `-${letter.toLowerCase()}`)}`;
	return new Proxy(new FakeDataset(), {
		set: (target, key, value) => {
			element.attributes[attribute(key)] = String(value);
			return Reflect.set(target, key, String(value));
		}
	});
};
/** Minimal mutable style declaration used by controller tests. */
class FakeStyle {
	constructor() {
		this.cssText = "";
		this.fontSize = "";
		/** @type {Record<string, string>} */
		this.properties = {};
	}
	/**
	 * Store a CSS custom property.
	 * @param {string} name Property name.
	 * @param {string} value Property value.
	 * @returns {void}
	 */
	setProperty(name, value) {
		this.properties[name] = value;
	}
}
/**
 * Represent FakeNode.
 */
class FakeNode extends EventTarget {
	/** @type {FakeElement|null} */
	parentNode;
	/** @type {string} */
	#text = "";
	constructor() {
		super();
		this.parentNode = null;
		/** @type {FakeNode[]} */
		this.children = [];
		this.href = "";
		this.hidden = false;
		this.ariaCurrent = "";
		this.dataset = new FakeDataset();
	}
	/**
	 * Read this node's own text content.
	 * @returns {string} Own text.
	 */
	get textContent() {
		return this.#text;
	}
	/**
	 * Replace this node's own text content.
	 * @param {string} value Own text.
	 */
	set textContent(value) {
		this.#text = value;
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
	/** @type {string|undefined} */
	#value;
	/** @param {string} tagName Element tag. */
	constructor(tagName = "div") {
		super();
		this.tagName = tagName.toUpperCase();
		this.className = "";
		this.id = "";
		this.disabled = false;
		this.href = "";
		this.rel = "";
		this.target = "";
		this.scrollTop = 0;
		this.scrollHeight = 0;
		this.clientHeight = 0;
		this.clientWidth = 0;
		this.style = new FakeStyle();
		/** @type {Record<string, string>} */
		this.attributes = {};
		this.dataset = datasetOf(this);
		this.classList = new FakeClassList(this);
	}
	/**
	 * Read descendant text, falling back to this element's own text.
	 * @override
	 * @returns {string} Text of all descendants, or own text when childless.
	 */
	get textContent() {
		return this.children.length > 0 ? this.children.map(child => child.textContent).join("") : super.textContent;
	}
	/**
	 * Replace element text and discard existing child nodes.
	 * @override
	 * @param {string} value Replacement text; drops existing children.
	 */
	set textContent(value) {
		this.children = [];
		super.textContent = value;
	}
	/**
	 * Read the control's current value, reflecting the `value` attribute
	 * until a script assigns the property, like a real unfilled control.
	 * @returns {string} Current value.
	 */
	get value() {
		return this.#value ?? Reflect.get(this.attributes, "value") ?? "";
	}
	/**
	 * Set the control's current value.
	 * @param {string} value New value.
	 */
	set value(value) {
		this.#value = value;
	}
	/**
	 * Store an element attribute.
	 * @param {string} name Attribute name.
	 * @param {string} value Attribute value.
	 * @returns {void}
	 */
	setAttribute(name, value) {
		this.attributes[name] = value;
		if (name === "value") this.#value = value;
	}
	/**
	 * Read an element attribute.
	 * @param {string} name Attribute name.
	 * @returns {string|null} Its value, or null when absent.
	 */
	getAttribute(name) {
		return this.attributes[name] ?? null;
	}
	/**
	 * Remove an element attribute.
	 * @param {string} name Attribute name.
	 * @returns {void}
	 */
	removeAttribute(name) {
		delete this.attributes[name];
	}
	/** Select this element's text in the fake DOM. @returns {void} */
	select() {}
	/** Scroll this element into view in the fake DOM. @returns {void} */
	scrollIntoView() {}
	/**
	 * Return deterministic geometry for text measurement.
	 * @returns {{width: number}} Element geometry.
	 */
	getBoundingClientRect() {
		return {
			width: 240
		};
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
	 * Move or add a child in front of a reference child.
	 * @param {FakeNode} node Child to place.
	 * @param {FakeNode|null} reference Child to precede, or null to append.
	 * @returns {FakeNode} The placed child.
	 */
	insertBefore(node, reference) {
		const owner = node.parentNode;
		if (owner !== null) owner.children = owner.children.filter(child => child !== node);
		node.parentNode = this;
		const index = reference === null ? -1 : this.children.indexOf(reference);
		if (index < 0) this.children.push(node);
		else this.children.splice(index, 0, node);
		return node;
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
	 * Every descendant that matches a simple selector.
	 * @param {string} selector Id, class or tag selector.
	 * @returns {FakeElement[]} Matches in document order.
	 */
	querySelectorAll(selector) {
		/** @type {FakeElement[]} */
		const found = [];
		/** @param {FakeElement} node Node to search below. */
		const walk = node => node.children.forEach(child => {
			if (!(child instanceof FakeElement)) return;
			if (child.matches(selector)) found.push(child);
			walk(child);
		});
		walk(this);
		return found;
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
		clone.attributes = {
			...this.attributes
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
		const child = this.children.find(item => item instanceof FakeElement);
		return child instanceof FakeElement ? child : null;
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
		this.documentElement = new FakeElement("html");
		this.body = new FakeElement("body");
		this.documentElement.append(this.body);
		this.append(this.documentElement);
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
