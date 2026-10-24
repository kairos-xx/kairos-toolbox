"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
	QrCode,
	QrSvg
} = require("../../../../plugins/devin_remote_control/src/media/shared/qr.js");
test("QR encoder covers capacity and rendering", () => {
	assert.equal(QrCode.versionFor(QrCode.utf8("hello")), 1);
	assert.throws(() => QrCode.versionFor(new Uint8Array(10000)), RangeError);
	assert.ok(QrCode.dataBytes(QrCode.utf8("hello"), 1).length > 0);
	assert.ok(QrCode.codewords(QrCode.dataBytes(QrCode.utf8("hello"), 1), 1).length > 0);
	assert.match(QrSvg.render("https://example.test"), /^<svg/u);
});
