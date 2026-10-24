# Devin Remote Control — Release Validation Record

Validation performed for release `5.0.0` from `src/` with Node.js.

## Automated gates

| Gate | Command | Result |
| --- | --- | --- |
| Syntax + structural rules | `npm run check` (`tools/check.mjs`) | PASS — 25 owned JS files, structural web rules OK |
| Unit + loopback integration | `npm test` (`node --test test_original/*.test.mjs`) | PASS — 15/15 tests including authenticated gateway, pairing, CSRF rotation, project isolation |
| Deterministic packaging | `npm run package` (`tools/package.mjs`) | PASS — `../vsix/devin-remote-control-5.0.0-src.zip` and `devin-remote-control-5.0.0.vsix`, byte-identical on repeat |

## Manual release checklist

- [ ] Install the generated VSIX in a VS Code-compatible editor.
- [ ] Status bar shows **Devin Remote**; the project panel opens.
- [ ] Gateway binds `127.0.0.1` only (no external listener without ngrok).
- [ ] `Devin Remote: Set Devin Credentials` stores the token in
      `SecretStorage` — the browser never receives it.
- [ ] ngrok charter produces one shared hostname; each workspace folder maps
      to an isolated `/p/<project-id>/` route.
- [ ] Phone pairing: QR opens the project URL only; the six-digit code is
      single-use and short-lived.
- [ ] Mutating API calls fail without the session cookie and CSRF token.
- [ ] Paired-device listing and session revocation work from the phone UI.
- [ ] File writes through symlinked paths are rejected.
