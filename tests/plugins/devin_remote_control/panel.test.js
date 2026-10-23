"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
test("panel synchronizes host theme variables", () => {
  /** @type {Array<[string, string]>} */
  const values = [];
  const document = {
    hidden: false,
    documentElement: {
      style: {
        setProperty: (/** @type {string} */ name, /** @type {string} */ value) => {
          values.push([name, value]);
        },
      },
    },
    addEventListener: () => {},
    querySelectorAll: () => [],
  };
  const context = /** @type {vm.Context} */ ({
    document,
    window: {addEventListener: () => {}},
    getComputedStyle: () => ({
      getPropertyValue: (/** @type {string} */ name) =>
        name === "--vscode-foreground" ? " white " : "",
    }),
    acquireVsCodeApi: () => ({postMessage: () => {}}),
    QrSvg: {render: () => "<svg/>"},
    DOMParser: class {},
    Date,
    Math,
    String,
    Object,
    Reflect,
    console,
    setInterval: () => 0,
  });
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../../../plugins/devin_remote_control/src/media/panel/panel.js"),
      "utf8",
    ),
    context,
  );
  assert.deepEqual(values, [["--dynamic-vscode-foreground", "white"]]);
});
