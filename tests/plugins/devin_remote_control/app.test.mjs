import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
const require = createRequire(import.meta.url);
test("web app static presentation helpers", () => {
  globalThis.document = /** @type {Document} */ (/** @type {unknown} */ ({
    createElement: (/** @type {string} */ tag) => ({
      tag,
      className: "",
      textContent: "",
    }),
    createElementNS: () => ({
      classList: {add: () => {}},
      setAttribute: () => {},
      append: () => {},
    }),
  }));
  const { DevinRemoteApp } = require("../../../plugins/devin_remote_control/src/media/web/app.js");
  const element = DevinRemoteApp.element("div", "card", "hello");
  assert.equal(element.className, "card");
  assert.equal(element.textContent, "hello");
  assert.equal(DevinRemoteApp.statusClass("running"), "running");
  assert.equal(DevinRemoteApp.statusClass("paused"), "waiting");
  assert.equal(DevinRemoteApp.statusClass("error"), "failed");
  assert.equal(DevinRemoteApp.text(null), "");
});
