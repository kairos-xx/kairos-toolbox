# Devin Remote Control — Technical Documentation

# Source map

The release is deliberately dependency-light: the extension host and remote web
application are plain Node.js/CommonJS plus browser JavaScript, HTML, and CSS.
No runtime `npm install` is required.

## Entry points

| Path | Responsibility |
| --- | --- |
| `extension/extension.js` | VS Code activation, status-bar command, settings panel, SecretStorage credentials, shared gateway/ngrok lifecycle. |
| `extension/lib/gateway-server.js` | Single loopback HTTP server, project routing, pairing/authentication, APIs, SSE, terminal streams, PWA assets. |
| `media/web/index.html` | Mobile IDE shell: Dashboard, Agents, Explorer, Editor, Review/diff, Console, Settings and SVG icon sprite. |
| `media/web/app.js` | Phone UI controller, agent/chat workflows, Explorer/Editor workbenches, unified/split diff review, terminals/tasks, models and device/session management. |
| `media/web/app.css` | Responsive design system for phone/tablet/desktop, Explorer split layout, editor/status surfaces, side-by-side diff review, terminal console, dark/light modes and accessibility states. |
| `media/web/service-worker.js` | Project-scoped installable PWA cache/network strategy. |

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

## VS Code panel

`media/panel/` contains the extension-side project settings panel. It displays
shared-gateway state, project endpoint, QR code, one-time pairing code, and
credential/configuration actions. `media/shared/qr.js` is a local QR encoder;
project URLs and pairing material are not sent to a QR SaaS.

## Tests and release tooling

- `test/*.test.mjs`: service and real-loopback gateway integration tests.
- `tools/check.mjs`: JavaScript syntax and canonical structural web checks.
- `tools/package.mjs`: deterministic source ZIP + VSIX assembly.
- `AGENTS.md`: complete canonical engineering policy embedded with the source.
- `VALIDATION.md`: release validation record.

## Main remote API groups

The browser talks only to its project endpoint `/p/<project-id>/api/...`.
Important groups are:

- `session`, `bootstrap`, `events`, `security/sessions`
- `agents`, `agents/<id>/messages`, `details`, `tags`, `insights/generate`
- `tree`, `file`, `file/raw`, `upload`, `directory`, `rename`, `copy`, `search`, `diagnostics`, `vscode/open`
- `workspace/stats`, `activity`, `tasks`, `system`
- `git/status`, `diff`, `compare`, `file-history`, `file-revision`, `log`, `commit` detail, `blame`, `branches`, `stashes`, `stash`, `branch`, `checkout`, `commit`, `pull`, `push`, `discard`, stage operations
- `terminal`, `terminal/<id>/events`, `terminal/<id>/input`
- `providers`, `llm/chat`

All mutating routes require the project-scoped session plus its CSRF token.


# Devin Remote Control 5

A phone-first remote IDE and Devin operations console delivered as a VS Code
extension. It keeps **one local gateway and one ngrok tunnel** for all workspace
folders in the VS Code window, while every open project receives an isolated
public route:

```text
https://<assigned>.ngrok.app/p/<project-id>/
```

Click **Devin Remote** in the VS Code status bar to open the project panel. The
panel shows the public endpoint, local QR code, one-time pairing code, gateway
state, and credential controls. The phone experience is agent-first and follows
the compact workflow of modern mobile coding-agent clients, with a full remote
project console one tap away.

## What 5.0 adds

### Devin operations

- Organization session list with project/running/waiting filters and search.
- Full cursor-paginated conversation history and live state refresh.
- Send new instructions; suspended sessions can resume through Devin's API.
- Create sessions with project tag, title, repo and ACU limit.
- Archive and terminate sessions.
- Session attachments and linked resources.
- Session tags with remote editing.
- Session insights, insight generation, classification, status detail,
  action items/issues, session size, mode, parent/child relationships and PR
  context when supplied by Devin.
- ACU/message metrics and mobile notifications for agent state transitions.

### Workspace intelligence and desktop continuity

- Live VS Code diagnostics streamed into the phone with error/warning/info severity, source, code and exact file/line navigation.
- Explorer context switcher for Recent files, Bookmarks and Problems.
- Persistent per-browser file bookmarks for fast return to high-value files.
- Open the current remote file directly in desktop VS Code from the phone.
- Project status rail always showing branch/change count, active agents and diagnostics.
- File commit history with read-only historical revision preview.
- Command palette entries for Problems, Git compare, bookmarks, file history and desktop navigation.
- Recent terminal command history promoted into the Runtime quick-command strip.

### Files and workspace

- Lazy expandable file tree with recent-file history and loaded-tree filtering.
- Whole-workspace filename/content search with line previews.
- Dedicated Explorer workbench with first-class Create, Upload, Search and Refresh controls.
- Dedicated Editor workbench with recent-file tabs, line-numbered syntax preview, edit mode and status bar.
- Syntax-highlighted preview and editable source view.
- File intelligence: lines, words, size, TODO/FIXME count and lightweight symbol outline.
- Git blame directly from the file editor.
- Create files/folders, rename, duplicate, delete and conflict-aware saves.
- Authenticated binary file upload and download.
- Workspace file/directory/byte/language statistics.
- Traversal and symlink escape prevention on every filesystem operation.

### Git

- Branch/upstream/ahead/behind status and changed-file summary.
- Review-first source-control workspace with staged/working-tree grouping and +/- summaries.
- Unified and side-by-side diff modes with explicit per-file stage/unstage/discard controls.
- Git commit detail, blame, stash list/create/apply/pop.
- Compare any safe branch/commit pair with stat summary and mobile diff review.
- Browse per-file commit history and inspect file contents at earlier revisions.
- Stage/unstage one file or all files.
- Working-tree discard with explicit confirmation.
- Commit staged changes.
- Pull using fast-forward-only semantics and push with upstream setup.
- Commit history.
- Local/remote branch list, branch creation and checkout.

### Terminal and tasks

- Multiple concurrent long-lived project terminal sessions.
- Existing terminal sessions reconnect after a web-app reload.
- Streamed terminal output over server-sent events.
- Command input, clear, Ctrl-C and session close controls.
- Console quick-command strip for status, diff statistics, current path and directory listing.
- Recent terminal commands persist locally and are promoted into the quick-command strip.
- Project-root shell on every terminal.
- Automatic task discovery from package scripts, Python project files and
  Makefile targets; tasks can be sent directly to the active terminal.

### Project and model console

- Switch between all open VS Code projects without a second gateway/tunnel.
- Per-project remote activity timeline.
- OpenAI-compatible and Anthropic project chat.
- Provider/model selection and per-request model override.
- Request, token and latency usage history for the current gateway process.
- Repository facts, runtime/architecture, gateway port/uptime and language mix.
- Paired-device inventory with last activity and remote user-agent information.
- Revoke another phone/browser session from the mobile UI.
- Current device is identified and protected from accidental self-revocation.
- Installable project-scoped PWA/service worker.
- Mobile command palette for common project/agent operations.
- Dashboard home surface with active agents, working tree, tasks and activity.
- Reworked primary navigation: Dashboard, Agents, Explorer, Review and Console.
- Responsive Explorer split layout on tablets/desktop while preserving phone-first navigation.
- Deep-linkable PWA shortcuts for Agents, Code, Git and Runtime.
- Hardware-keyboard shortcuts: Cmd/Ctrl+K command palette, Cmd/Ctrl+S save, / search.
- Redesigned VS Code project panel with live agent/ACU/device metrics.

## Install

Use the release VSIX:

```text
code --install-extension devin-remote-control-5.0.0.vsix
```

Or choose **Extensions → … → Install from VSIX…** in VS Code.

The extension activates on startup and adds **Devin Remote** to the bottom status
bar.

## Configure Devin

Run **Devin Remote: Set Devin Credentials**. The organization ID is VS Code
configuration; the token is stored only in VS Code `SecretStorage`. The browser
never receives either secret.

## Configure free ngrok

Install the `ngrok` CLI, create a free ngrok account/authtoken, then run
**Devin Remote: Set ngrok Authtoken**. The extension starts a single process
conceptually equivalent to:

```text
ngrok http <shared-gateway-port>
```

The tunnel hostname is shared by all open projects. Isolation is enforced by
`/p/<project-id>/` routing and project-scoped authentication.

## Pair a phone

1. Click **Devin Remote** in the VS Code status bar.
2. Select the active project.
3. Generate a new pairing code.
4. Scan the QR code.
5. Enter the six-digit one-time code on the phone.

The QR contains only the project URL. It contains no Devin token, provider key,
ngrok token or reusable browser credential.

## Security model

This application intentionally exposes powerful capabilities, including file
writes and a project shell. The public URL is therefore not the authorization
mechanism.

- Gateway binds only to `127.0.0.1`; ngrok is the external ingress.
- Six-digit pairing codes are single-use, short-lived and rate-limited.
- Auth is a random project-scoped `Secure; HttpOnly; SameSite=Strict` cookie.
- Mutating requests also require a rotating session CSRF token.
- Phone sessions expire and can be revoked individually or project-wide.
- Credentials live only in VS Code `SecretStorage`.
- CSP blocks third-party scripts/styles/frames and browser sensitive APIs.
- Filesystem operations perform lexical containment and realpath containment.
- Writes through symlinked directories and symlink mutation are rejected.
- Text saves support modification-time conflict detection.
- Upload/read transfer size uses the configured project file limit.
- Git process calls do not use a shell.
- No iframe, frontend CDN, analytics runtime, or browser-side API secret exists.

Anyone controlling a paired browser session has deliberate project-level power.
Revoke the session immediately if the phone/browser is no longer trusted.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `devinRemote.gateway.autoStart` | `true` | Start one loopback gateway automatically. |
| `devinRemote.gateway.port` | `0` | Shared fixed port, or `0` for an OS-selected port. |
| `devinRemote.ngrok.autoStart` | `true` | Start one ngrok tunnel with the gateway. |
| `devinRemote.ngrok.command` | `ngrok` | ngrok executable or path. |
| `devinRemote.devin.organizationId` | empty | Devin organization ID. |
| `devinRemote.files.maxBytes` | `4194304` | Remote edit and transfer size limit. |
| `devinRemote.files.excludes` | common caches/build dirs | Hidden tree/search basenames. |
| `devinRemote.terminal.shell` | empty | Override default project shell. |
| `devinRemote.security.sessionHours` | `12` | Phone/browser session lifetime. |
| `devinRemote.providers.openAICompatible.baseUrl` | OpenAI v1 | OpenAI-compatible base URL. |
| `devinRemote.providers.openAICompatible.model` | empty | Default compatible model. |
| `devinRemote.providers.anthropic.model` | empty | Default Anthropic model. |

## Architecture

```text
VS Code extension host
  ├─ ProjectRegistry ─ workspace A ─ /p/<A>/
  │                 └ workspace B ─ /p/<B>/
  ├─ SecurityService
  ├─ FileService
  ├─ GitService
  ├─ TerminalManager
  ├─ TaskService
  ├─ ActivityService
  ├─ DevinClient
  ├─ ProviderService
  └─ GatewayServer 127.0.0.1:<port>
             │
             └─ one ngrok HTTPS tunnel
                 ├─ /p/<A>/
                 └─ /p/<B>/
```

See `SOURCE_MAP.md` for the complete code map and API groups.

## Development

The extension and web app have no runtime npm dependencies:

```text
npm run check
npm test
npm run package
```

`tools/check.mjs` syntax-checks owned JavaScript and enforces project structural
web rules. `AGENTS.md` contains the complete canonical engineering standard used
for final review.
