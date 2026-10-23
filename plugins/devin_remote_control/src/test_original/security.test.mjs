import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { SecurityService } = require("../extension/lib/security.js");
test("pairing creates a project-scoped authenticated session", () => {
  const security = new SecurityService(12);
  const pairing = security.createPairing("abcdef123456");
  const paired = security.pair("abcdef123456", pairing.code, "127.0.0.1");
  const session = security.authenticate("abcdef123456", paired.token);
  SecurityService.authorizeMutation(session, paired.csrf);
  assert.equal(session.projectId, "abcdef123456");
});
test("a session cannot cross project boundaries", () => {
  const security = new SecurityService(12);
  const pairing = security.createPairing("abcdef123456");
  const paired = security.pair("abcdef123456", pairing.code, "127.0.0.1");
  assert.throws(
    () => security.authenticate("111111111111", paired.token),
    /not valid for this project/u
  );
});
test("csrf can be rotated after reload", () => {
  const security = new SecurityService(12);
  const pairing = security.createPairing("abcdef123456");
  const paired = security.pair("abcdef123456", pairing.code, "127.0.0.1");
  const session = security.authenticate("abcdef123456", paired.token);
  const next = SecurityService.issueCsrf(session);
  SecurityService.authorizeMutation(session, next);
  assert.throws(() => SecurityService.authorizeMutation(session, paired.csrf), /Invalid CSRF/u);
});
