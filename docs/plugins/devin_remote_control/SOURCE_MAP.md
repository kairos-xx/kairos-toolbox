# Devin Remote Control — Source Map and API Groups

Complete code map for the extension host and remote web application. Paths
are relative to `plugins/devin_remote_control/src/`.

## Entry points

| Path | Responsibility |
| --- | --- |
| `extension/extension.js` | VS Code activation, status-bar command, settings panel, SecretStorage credentials, shared gateway/ngrok lifecycle. |
| `extension/lib/gateway-server.js` | Single loopback HTTP server, project routing, pairing/authentication, APIs, SSE, terminal streams, PWA assets, CSP headers. |
| `media/web/index.html` | Mobile IDE shell: Dashboard, Agents, Explorer, Editor, Review/diff, Console, Settings and SVG icon sprite. |
| `media/web/app.js` | Phone UI controller, agent/chat workflows, Explorer/Editor workbenches, unified/split diff review, terminals/tasks, models and device/session management. |
| `media/web/app.css` | Responsive design system for phone/tablet/desktop, Explorer split layout, editor/status surfaces, side-by-side diff review, terminal console, dark/light modes and accessibility states. |
| `media/web/service-worker.js` | Project-scoped installable PWA cache/network strategy. |
| `media/panel/` | Extension-side project settings panel: gateway state, project endpoint, QR code, pairing code, credential actions. |
| `media/shared/qr.js` | Local QR encoder; pairing material never reaches a QR SaaS. |

## Backend services

| Path | Responsibility |
| --- | --- |
| `extension/lib/devin-client.js` | Devin v3 organization sessions, messages, attachments, insights, tags, create/archive/terminate. |
| `extension/lib/file-service.js` | Sandboxed tree/read/write/search/stats/inspect/symbols/copy/rename/delete/upload/download with traversal/symlink protections. |
| `extension/lib/git-service.js` | Status, unified diffs, revision comparison, file history/revision reads, branches, stage/unstage, commit, pull, push, discard. |
| `extension/lib/shell-session.js` | Multiple long-lived project shell sessions and streamed output. |
| `extension/lib/provider-service.js` | OpenAI-compatible/Anthropic project chat, model override, request/token/latency usage history. |
| `extension/lib/security.js` | One-time pairing, scoped HttpOnly sessions, CSRF rotation, rate limiting, device listing/revocation. |
| `extension/lib/project-registry.js` | Stable project IDs, multi-root discovery, live VS Code diagnostics and open-file desktop navigation. |
| `extension/lib/task-service.js` | Detect runnable package/Python/Make tasks without executing discovery code. |
| `extension/lib/activity-service.js` | Bounded per-project remote-operation timeline. |
| `extension/lib/ngrok-manager.js` | One shared ngrok tunnel and public URL discovery. |
| `extension/lib/constants.js` | Shared configuration keys and protocol constants. |
| `extension/lib/errors.js` | Structured error types and HTTP status mapping. |

## Tests and release tooling

| Path | Responsibility |
| --- | --- |
| `test_original/*.test.mjs` | Service unit tests and real-loopback gateway integration tests. |
| `tools/check.mjs` | JavaScript syntax checks and structural web rules (manifest, assets, CSP, no runtime dependencies). |
| `tools/package.mjs` | Deterministic source ZIP + VSIX assembly into `../vsix/`. |
| `AGENTS.md` | Canonical engineering policy embedded with the source. |
| `VALIDATION.md` | Release validation record and manual checklist. |

## Remote API groups

The browser talks only to its project endpoint `/p/<project-id>/api/...`:

- `session`, `bootstrap`, `events`, `security/sessions`
- `agents`, `agents/<id>/messages`, `details`, `tags`, `insights/generate`
- `tree`, `file`, `file/raw`, `upload`, `directory`, `rename`, `copy`,
  `search`, `diagnostics`, `vscode/open`
- `workspace/stats`, `activity`, `tasks`, `system`
- `git/status`, `diff`, `compare`, `file-history`, `file-revision`, `log`,
  `commit` detail, `blame`, `branches`, `stashes`, `stash`, `branch`,
  `checkout`, `commit`, `pull`, `push`, `discard`, stage operations
- `terminal`, `terminal/<id>/events`, `terminal/<id>/input`
- `providers`, `llm/chat`

All mutating routes require the project-scoped session plus its CSRF token.
