import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { SecurityService } = require("../../../plugins/devin_remote_control/src/extension/lib/security.js");
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
test("pairing rejects expired codes, wrong codes and rate-limited attempts", () => {
  const security = new SecurityService(12);
  security.createPairing("proj-a");
  const stored = security.pairings.get("proj-a");
  assert.ok(stored);
  stored.expiresAt = Date.now() - 1;
  assert.throws(() => security.pair("proj-a", "000000", "10.0.0.1"), /expired/u);
  assert.equal(security.pairings.has("proj-a"), false);
  const other = new SecurityService(12);
  other.createPairing("proj-b");
  assert.throws(() => other.pair("proj-b", "000000", "10.0.0.2"), /Invalid pairing code/u);
  assert.throws(() => other.pair("missing", "000000", "10.0.0.3"), /expired/u);
  const limited = new SecurityService(12);
  limited.createPairing("proj-c");
  for (let attempt = 0; attempt < 8; attempt += 1)
    assert.throws(() => limited.pair("proj-c", "000000", "10.0.0.4"), /Invalid pairing code/u);
  assert.throws(() => limited.pair("proj-c", "000000", "10.0.0.4"), /Too many pairing attempts/u);
});
test("sessions expire, can be revoked per device or per project, and clean up", () => {
  const security = new SecurityService(12);
  const paired = security.pair(
    "proj-a",
    security.createPairing("proj-a").code,
    "10.0.0.1",
    "device/1"
  );
  assert.throws(() => security.authenticate("proj-a", ""), /Pair this device/u);
  const key = SecurityService.hash(paired.token);
  const stored = security.sessions.get(key);
  assert.ok(stored);
  stored.expiresAt = Date.now() - 1;
  assert.throws(() => security.authenticate("proj-a", paired.token), /expired/u);
  assert.equal(security.sessions.has(key), false);
  const live = security.pair("proj-a", security.createPairing("proj-a").code, "10.0.0.2");
  const devices = security.listProjectSessions("proj-a");
  assert.equal(devices.length, 1);
  assert.equal(devices[0]?.remoteAddress, "10.0.0.2");
  assert.throws(() => security.revokeSession("proj-a", "missing"), /not found/u);
  security.revokeSession("proj-a", String(devices[0]?.id ?? ""));
  assert.equal(security.listProjectSessions("proj-a").length, 0);
  assert.throws(() => security.authenticate("proj-a", live.token), /expired/u);
  const again = security.pair("proj-a", security.createPairing("proj-a").code, "10.0.0.3");
  security.revokeProject("proj-a");
  assert.throws(() => security.authenticate("proj-a", again.token), /expired/u);
  security.pairings.set("old-project", { hash: "h", expiresAt: 0 });
  security.attempts.set("192.0.2.1", { count: 9, resetAt: 0 });
  const stale = security.pair("proj-b", security.createPairing("proj-b").code, "10.0.0.4");
  const staleKey = SecurityService.hash(stale.token);
  const staleSession = security.sessions.get(staleKey);
  assert.ok(staleSession);
  staleSession.expiresAt = 0;
  security.cleanup();
  assert.equal(security.pairings.has("old-project"), false);
  assert.equal(security.attempts.has("192.0.2.1"), false);
  assert.equal(security.sessions.has(staleKey), false);
});
