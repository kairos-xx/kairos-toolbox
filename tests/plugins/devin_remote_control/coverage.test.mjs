import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const modules = ["activity-service","constants","devin-client","errors","file-service","git-service","security","task-service"];
test("all service modules load", () => { for (const name of modules) assert.ok(require(`../../../plugins/devin_remote_control/src/extension/lib/${name}.js`)); });
