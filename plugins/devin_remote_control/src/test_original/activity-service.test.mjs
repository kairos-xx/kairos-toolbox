import assert from "node:assert/strict";
import test from "node:test";
import {
	createRequire
} from "node:module";
const require = createRequire(import.meta.url);
const {
	ActivityService
} = require("../extension/lib/activity-service.js");
test("activity service keeps bounded per-project history", () => {
	const activity = new ActivityService(2);
	activity.record("a", "files", "one");
	activity.record("a", "git", "two");
	activity.record("a", "agent", "three");
	activity.record("b", "files", "other");
	assert.deepEqual(activity.list("a").map(item => item.title), ["three", "two"]);
	assert.deepEqual(activity.list("b").map(item => item.title), ["other"]);
});
