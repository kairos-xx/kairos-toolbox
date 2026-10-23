# Devin Remote Control

> A secure, phone-first remote IDE and Devin operations console — delivered
> as a VS Code-compatible extension. Version 5.0.

Click **Devin Remote** in the editor status bar, scan the QR code, pair once,
and your workspace — projects, agents, files, diffs, git, terminals — is in
your pocket.

## Architecture

```text
VS Code window ── extension.js ── gateway-server.js (loopback HTTP)
                                       │              │
                              media/panel (QR,     ngrok tunnel
                              pairing, state)          │
                                                       ▼
                                    https://<assigned>.ngrok.app/p/<project-id>/
                                                       │
                              ┌────────────────────────┘
                              ▼
                    media/web — installable PWA:
                    Dashboard · Agents · Explorer · Editor ·
                    Review/diff · Console · Settings
```

**One gateway, one tunnel, isolated per-project routes.** Every open
workspace folder gets its own `/p/<project-id>/` endpoint sharing a single
loopback HTTP server and a single ngrok tunnel.

## What's inside

### Devin operations

- Organization session list with project/running/waiting filters + search.
- Full cursor-paginated conversation history, live state refresh.
- Send instructions; resume suspended sessions via Devin's API.
- Create sessions (project tag, title, repo, ACU limit); archive/terminate.
- Attachments, editable tags, insights + generation, classification,
  action items/issues, session size/mode, parent/child, PR context.
- ACU/message metrics and mobile notifications on agent state transitions.

### Workspace intelligence

- Sandboxed file service: tree/read/write/search/stats/inspect/symbols,
  copy/rename/delete, upload/download — with traversal & symlink protection.
- Full git: status, unified diffs, revision compare, file history, branches,
  stage/unstage, commit, pull, push, discard, stashes, blame.
- Multiple long-lived shell sessions with streamed output.
- Provider service: OpenAI-compatible/Anthropic project chat with
  model override and request/token/latency history.
- Task detection (package/Python/Make) without executing discovery code.
- Live VS Code diagnostics; open-file desktop navigation.

### Mobile UI

- Phone-first agent workflow, responsive from 390px to desktop.
- Explorer split layout, unified/split diff review, terminal console.
- Dark/light themes and accessibility states.
- Installable PWA with project-scoped service-worker caching.

## Security model

Not "obscure URL" security:

- **One-time pairing codes** — scoped HttpOnly sessions afterwards.
- **Rotating CSRF tokens** — every mutating route requires the
  project-scoped session + CSRF token.
- **Rate limiting** and device listing/revocation.
- **CSP headers** emitted by the gateway; no remote script/style loads.
- **Local QR encoding** (`media/shared/qr.js`) — pairing material never
  goes to a QR SaaS.
- **Loopback binding** — the gateway never listens on a public interface.

## Engineering

- **Zero runtime npm dependencies** — plain Node/CommonJS + browser JS.
- **Deterministic packaging** — `tools/package.mjs` emits byte-identical
  VSIX + source ZIP on every run; `tools/check.mjs` enforces syntax and
  structural rules (CSP header present, no runtime deps, no remote assets).
- Tests: `node --test test_original/*.test.mjs` inside `src/` plus the
  repo-level suites under `tests/plugins/devin_remote_control/`.

## Install

```bash
python plugins/devin_remote_control/install.py          # discover editors
python plugins/devin_remote_control/install.py --uninstall
```

Or sideload the deterministic VSIX built by `tools/package.mjs`.

## vs. code-server / Remote Tunnels

code-server moves the *whole IDE* into the browser; tunnels move *your
terminal* to another machine. Devin Remote Control leaves the desktop IDE
exactly as it is and gives your **phone** an agent-first console over it —
dashboard, Devin ops, diff review, console — designed for a 390px screen,
not a transplanted desktop.

📖 Long-form docs: `docs/plugins/devin_remote_control/DOCS.md`
