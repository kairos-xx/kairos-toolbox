# Devin Remote Control — Engineering Standard

Canonical rules for contributions to this extension and web application.

## Dependency policy

- No runtime npm dependencies. The extension host code is plain
  Node.js/CommonJS; the remote web application is browser JavaScript, HTML
  and CSS.
- Never vendor third-party browser libraries, CDNs, analytics, or iframe
  content. The Content-Security-Policy in `extension/lib/gateway-server.js`
  must keep blocking anything outside `self`.
- `package.json` must not grow a `dependencies` field;
  `tools/check.mjs` enforces this.

## Layout

- `extension/` — VS Code host code (`extension.js`, `lib/` services).
- `media/web/` — phone-first remote application shell, controller, styles,
  service worker.
- `media/panel/` — VS Code project panel webview.
- `media/shared/` — shared browser modules (e.g. the local QR encoder).
- `test_original/` — `node --test` service and loopback gateway tests.
- `tools/` — `check.mjs` (syntax + structural rules) and `package.mjs`
  (deterministic source ZIP + VSIX into `../vsix/`).

## Security rules

- The gateway binds `127.0.0.1` only; ngrok is the sole external ingress.
- Devin tokens, provider keys and the ngrok authtoken live exclusively in
  VS Code `SecretStorage`; they are never sent to the browser, the web app,
  or QR payloads.
- All mutating routes require the project-scoped `Secure; HttpOnly;
  SameSite=Strict` session cookie plus the rotating CSRF token.
- Filesystem operations must perform lexical containment and realpath
  containment; symlinked writes and symlink mutation are rejected.
- Git and shell spawning never pass through a shell interpreter; all
  commands are argv arrays.

## Change checklist

1. `npm run check` — syntax and structural rules green.
2. `npm test` — all `test_original/*.test.mjs` tests pass, including the
   real-loopback gateway integration tests.
3. `npm run package` — deterministic `../vsix/` artifacts regenerate
   byte-identically on repeat runs.
4. Update `docs/plugins/devin_remote_control/` and this file whenever a
   command, API group, service, or security property changes.
