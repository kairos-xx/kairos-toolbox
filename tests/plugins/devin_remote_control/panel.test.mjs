import assert from "node:assert/strict";
import test from "node:test";
test("panel synchronizes host theme variables", async () => {
  /** @type {Array<[string, string]>} */
  const values = [];
  globalThis.document = /** @type {Document} */ (/** @type {unknown} */ ({
    hidden: false,
    documentElement: {
      style: {
        setProperty: (/** @type {string} */ name, /** @type {string} */ value) => {
          values.push([name, value]);
        },
      },
    },
    addEventListener: () => {},
  }));
  globalThis.getComputedStyle = /** @type {typeof getComputedStyle} */ (() =>
    /** @type {CSSStyleDeclaration} */ (/** @type {unknown} */ ({
      getPropertyValue: (/** @type {string} */ name) =>
        name === "--vscode-foreground" ? " white " : "",
    }))
  );
  const panel = await import("../../../plugins/devin_remote_control/src/media/panel/panel.js");
  panel.syncThemeVariables();
  assert.deepEqual(values, [
    ["--dynamic-vscode-foreground", "white"],
    ["--dynamic-vscode-foreground", "white"],
  ]);
});
test("panel renders complete project state and interactions", async () => {
  /** Fake DOM element used by the project-panel interaction harness. */
  class FakeElement {
    constructor() {
      this.textContent = "";
      this.value = "";
      this.selected = false;
      this.listenerCount = 0;
      this.className = "";
      /** @type {Map<string, string>} */
      this.attributes = new Map();
      /** @type {unknown[]} */
      this.children = [];
      /** @type {unknown[]} */
      this.queryResults = [];
    }
    /** Read a fake element attribute.
     * @param {string} name Attribute name.
     * @returns {string | null} Stored attribute value, or null when absent.
     */
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    /** Store a fake element attribute.
     * @param {string} name Attribute name.
     * @param {string} value Attribute value.
     */
    setAttribute(name, value) { this.attributes.set(name, value); }
    /** Append child nodes to the fake element.
     * @param {...unknown} children Child nodes.
     */
    append(...children) { this.children.push(...children); }
    /** Replace all child nodes on the fake element.
     * @param {...unknown} children Replacement child nodes.
     */
    replaceChildren(...children) { this.children = children; }
    /** Accept an event listener. */
    addEventListener() { this.listenerCount += 1; }
    /** Return the configured fake query results.
     * @returns {unknown[]} Configured query results.
     */
    querySelectorAll() { return this.queryResults; }
    /** Return the closest fake actionable element.
     * @returns {FakeElement} This fake element.
     */
    closest() { return this; }
  }
  /** @type {Map<string, FakeElement>} */
  const slots = new Map();
  const names = ["gateway-state","metric-active","metric-acu","metric-devices","tunnel-summary","tunnel-dot","ngrok-state","devin-state","openai-state","anthropic-state","local-url","toggle-gateway","toggle-tunnel","project-select","project-url","qr","pair-code","pair-expiry","routes"];
  for (const name of names) slots.set(name, new FakeElement());
  const root = new FakeElement();
  root.queryResults = [...slots.entries()].map(([name, element]) => { element.setAttribute("data-slot", name); return element; });
  globalThis.Element = /** @type {typeof Element} */ (/** @type {unknown} */ (FakeElement));
  globalThis.document = /** @type {Document} */ (/** @type {unknown} */ ({
    hidden: false,
    documentElement: {style: {setProperty: () => {}}},
    addEventListener: () => {},
    createDocumentFragment: () => new FakeElement(),
    createElement: () => new FakeElement(),
    importNode: () => new FakeElement(),
  }));
  globalThis.window = /** @type {Window & typeof globalThis} */ (/** @type {unknown} */ ({addEventListener: () => {}}));
  globalThis.acquireVsCodeApi = () => ({
    postMessage: () => {},
    getState: () => undefined,
    setState: (/** @type {unknown} */ state) => { Reflect.set(globalThis, "__panelState", state); },
  });
  Reflect.set(globalThis, "QrSvg", {render: () => "<svg/>"});
  globalThis.DOMParser = /** @type {typeof DOMParser} */ (/** @type {unknown} */ (class { parseFromString() { return {documentElement: new FakeElement()}; } }));
  const {ProjectPanel} = await import("../../../plugins/devin_remote_control/src/media/panel/panel.js");
  const panel = new ProjectPanel(/** @type {HTMLElement} */ (/** @type {unknown} */ (root)));
  panel.onMessage(/** @type {MessageEvent} */ (/** @type {unknown} */ ({data: {type: "ignored"}})));
  panel.onMessage(/** @type {MessageEvent} */ (/** @type {unknown} */ ({data: {type: "state", state: {
    gateway: {running: true, port: 4321, localUrl: "http://127.0.0.1:4321"},
    tunnel: {state: "online", publicUrl: "https://example.test", error: ""},
    projects: [{id: "p", name: "Project", url: "https://example.test/p/p/", pairing: {code: "123456", expiresAt: Date.now() + 60000}}],
    activeProjectId: "p",
    metrics: {activeAgents: 2, acu: 3.5, pairedDevices: 1},
    connections: {devin: true, openAI: true, anthropic: false},
  }}})));
  assert.equal(slots.get("gateway-state")?.textContent, "Gateway 4321 · online");
  assert.equal(slots.get("pair-code")?.textContent, "123456");
  panel.selectedProjectId = "missing";
  panel.render();
  panel.state.tunnel = {state: "error", publicUrl: "", error: "failed"};
  panel.state.gateway = {running: false, port: 0, localUrl: ""};
  panel.render();
  assert.equal(slots.get("gateway-state")?.textContent, "Gateway stopped");
});
