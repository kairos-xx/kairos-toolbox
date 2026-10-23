# Lake Terminal &amp; Friends

> **An execution control plane for the agentic era — plus the trust layer, release pipeline, and editor extensions built around it.**

Modern development isn't one human typing into one terminal anymore. It's a developer, an IDE-hosted agent, a CLI agent, background automation, and remote hosts — all mutating the same workspace at once. This repository is a complete toolkit for making that chaos *observable, attributable, and shippable*:

| Component | What it is | One-liner |
| --- | --- | --- |
| 🌊 [`lake_terminal/`](tools/lake_terminal/) | **Execution control plane** | Persistent terminals, agent attribution, durable command history — one SQLite-backed state model for humans *and* coding agents. |
| 🏹 [`arrow/`](tools/arrow/) | **Local URI trust framework** | Signed, expiring, replay-proof envelopes for `scheme://` invocations — with provenance, delegation, audit chains, and native OS elevation. |
| 📦 [`release_builder/`](tools/release_builder/) | **Cross-platform release builder** | One command → macOS `.pkg`, Debian `.deb`, Windows `.exe`, or a self-contained `.pyz` — with SHA-256 digests and a machine-readable manifest. |
| 🚀 [`release_publish/`](tools/release_publish/) | **Deterministic release publisher** | Compile a release into an *immutable plan*, inspect it, dry-run it, journal it, resume it. Monorepo-native. |
| 📱 [`devin_remote_control/`](plugins/devin_remote_control/) | **Phone-first remote IDE** | A VS Code-compatible extension that puts your projects, agents, git, and terminals in your pocket — via one local gateway and a QR code. |
| 🧩 [`plugins/`](plugins/) | **Editor micro-extensions** | Three more focused quality-of-life plugins for VS Code-compatible editors. |

```text
            ┌──────────────────── AGENTS & HUMANS ────────────────────┐
            │  Claude Code   Codex   Cursor   Devin Local Agent   you │
            └──────────┬───────────────┬───────────────┬──────────────┘
                       │               │               │
                       ▼               ▼               ▼
              ┌────────────────────────────────────────────────┐
              │              LAKE TERMINAL                     │
              │  canonical sessions · commands · events ·      │
              │  processes · hosts  →  all in one SQLite DB    │
              └───────┬───────────────────────┬────────────────┘
                      │                       │
        tmux / ConPTY │                       │ daemon IPC · TUI · web
        persistent PTY│                       │ CLI · editor bridge
                      ▼                       ▼
              ┌──────────────┐        ┌─────────────────────────┐
              │   ARROW      │        │  devin_remote_control   │
              │ signed local │        │  gateway → QR → phone   │
              │ URI trust    │        └─────────────────────────┘
              └──────────────┘

        ┌─────────────── SHIPPING ───────────────┐
        │  release_builder → installers / .pyz   │
        │  release_publish → plans → GH releases │
        └────────────────────────────────────────┘
```

---

## 🌊 Lake Terminal — the flagship

**A cross-platform execution control plane for humans and coding agents.** `lake` gives every execution — yours, Claude's, Codex's, Cursor's, Devin's — a stable identity, a durable command record, a normalized event stream, and honest answers to "what's actually still running?"

```bash
lake                    # attach to a managed persistent shell
lake pytest -q          # run in a managed shell that survives you
lake --status           # everything the control plane knows
lake --watch            # interactive multi-session TUI
lake --web              # live browser dashboard
lake --status --json    # machine-readable, for editors & automation
lake --subscribe        # normalized event stream
lake --doctor --full    # deep diagnostics
```

### Why not tmux / zellij / PM2 / screen?

Lake Terminal is not a terminal multiplexer — it *uses* them as backends and adds the layer they don't have: **a queryable control plane**.

| | tmux / zellij | PM2 / systemd | IDE integrated terminal | **Lake Terminal** |
| --- | --- | --- | --- | --- |
| Survives disconnects | ✅ | ✅ | ❌ | ✅ (tmux on Unix, ConPTY on Windows) |
| Queryable state model | ❌ (socket protocol) | ⚠️ (its own daemon db) | ❌ (UI state only) | ✅ normalized SQLite: `sessions`, `commands`, `events`, `processes`, `hosts` |
| Agent attribution | ❌ | ❌ | ❌ | ✅ native hooks for Claude Code, Codex, Cursor, Devin |
| Durable command history | ❌ scrollback only | ⚠️ logs | ❌ | ✅ transactional lifecycle records |
| Honest degraded mode | n/a | n/a | ❌ | ✅ process-only observability instead of false "reconnectable" claims |
| Event subscriptions | ❌ | ⚠️ | ❌ | ✅ authenticated local daemon + `lake --subscribe` |
| Editor/machine API | ❌ | ❌ | ⚠️ | ✅ `--json`, IPC, extension bridge |
| Privacy controls | ❌ | ❌ | ❌ | ✅ redaction before persistence, retention, purge |
| Remote host aggregation | ❌ | ❌ | ❌ | ✅ named SSH execution hosts |

**Design rule that makes it trustworthy:** SQLite is the *only* persistent control-plane state. No JSON session registry, no shadow files, no second source of truth.

📖 [Full architecture &amp; API docs](docs/tools/lake_terminal/DOCS.md)

---

## 🏹 Arrow — trust for local URIs

Registering a `scheme://` handler is easy. Making it **safe** is not — any process can fire a URI at your handler. Arrow treats every local URI invocation as a security-boundary crossing:

1. **Provenance** — which executable called, at which trust level.
2. **Authentication** — principals with declared strength.
3. **Authorization** — scopes, capabilities, and policy checks.
4. **Time constraints** — issue/expiry windows, issuer/audience/machine binding.
5. **Replay protection** — consumed nonces and idempotency keys.
6. **Audit** — hash-chained audit records and traces.
7. **Explicit elevation** — narrowly allowlisted commands through the OS's *native* consent surface (UAC, `sudo`, macOS authorization) — never silent privilege.

**vs. JWT/OAuth libraries:** those solve *web* token validation. Arrow solves *local IPC* trust — provenance of the calling executable, revocation, delegation chains, and elevation through OS consent — things a JWT library doesn't even model.

📖 [Arrow docs](docs/tools/arrow/DOCS.md)

---

## 📦 release_builder — one manifest, four artifact formats

```bash
python -m tools.release_builder \
  --project . --manifest release-builder.toml --output dist/
```

Out of a single TOML manifest you get: macOS `.pkg`, Debian `.deb`, Windows ARM64 setup `.exe`, or — with `--format pyz` — a self-contained `name-version-platform.pyz`.

- **Every external command flows through a `Runner` protocol** → builds are deterministic, captured, and fail fast on any non-zero exit.
- **`pip` never dirties your source tree** — the `.pyz` build installs into a throwaway copy (no stray `build/` or `*.egg-info`), strips console scripts and `__pycache__`, then smoke-tests the archive with an isolated home before shipping it.
- **vs. PyInstaller/Nuitka:** those freeze an interpreter *into* the artifact. `release_builder` produces real OS installers and portable `.pyz` archives — smaller, standard, and updatable in place.

📖 [Builder docs](docs/tools/release_builder/DOCS.md)

---

## 🚀 release_publish — releases as immutable plans

Most release tools *do things*. `release_publish` **plans** things — into immutable `Action` objects you can inspect, render, dry-run, journal, and *resume after failure*.

```bash
python -m tools.release_publish --config release.toml plan      # compile & inspect
python -m tools.release_publish --config release.toml release   # execute
```

- **Monorepo-native:** per-component tags (`demo/v1.2.3`), independent versions, `version_files` instead of version guessing.
- **Deterministic ordering:** topological sort over declared dependencies.
- **Provider boundary:** GitHub adapter bundled; the engine only sees `Action`s.
- **Two-phase CI flow:** `release --no-publish` pushes tags, CI builds artifacts, then `publish --project X --artifacts dist/` creates the hosted release — with `--generate-notes` anchored to the *previous release tag of that project*.

**vs. semantic-release / changesets / lerna:** those are Node-centric and do their work inline. This is language-agnostic, audit-first, and resumable by construction.

📖 [Publisher docs](docs/tools/release_publish/DOCS.md)

---

## 📱 devin_remote_control — your IDE, in your pocket

A VS Code-compatible extension that turns your desktop workspace into a phone-first remote console: agents, explorer, editor, diffs, git, terminals — all through **one loopback gateway + one ngrok tunnel**, with per-project isolated routes `https://<assigned>.ngrok.app/p/<project-id>/`.

- **Zero runtime npm dependencies** — plain Node/CommonJS + browser JS.
- **Real security model:** one-time pairing, scoped HttpOnly sessions, rotating CSRF, rate limiting, device revocation, CSP headers — not "obscure URL" security.
- **QR pairing stays local** — an embedded QR encoder; pairing material never touches a SaaS.
- **Deterministic packaging** — `tools/package.mjs` emits byte-identical VSIX/ZIP archives on every run.

**vs. code-server / VS Code Remote Tunnels:** those move the *whole IDE* to the browser. This keeps your desktop IDE as-is and gives your phone an agent-first console for it — dashboard, Devin session ops, diff review, and a console, designed for a 390px screen.

📖 [Extension docs](docs/plugins/devin_remote_control/DOCS.md)

### The other plugins

| Plugin | Superpower | Docs |
| --- | --- | --- |
| [`close_deleted_editors/`](plugins/close_deleted_editors/) | Closes tabs whose files no longer exist on disk. | 📖 [DOCS.md](docs/plugins/close_deleted_editors/DOCS.md) |
| [`open_selected_editor_panel/`](plugins/open_selected_editor_panel/) | Opens an Explorer selection into the editor group you chose, recursively, pinned. | 📖 [DOCS.md](docs/plugins/open_selected_editor_panel/DOCS.md) |
| [`simple_web_server/`](plugins/simple_web_server/) | One-command localhost static server for the workspace. | 📖 [DOCS.md](docs/plugins/simple_web_server/DOCS.md) |

---

## 🛡️ Engineering discipline

This repo is held to an unusually strict bar — and it's machine-enforced, not aspirational:

| Gate | Tool | State |
| --- | --- | --- |
| Lint | `ruff check --select ALL` (every rule) | ✅ clean — **zero `noqa` suppressions** |
| Types | `basedpyright` strict + `pyrefly` | ✅ 0 errors / 0 warnings / 0 notes |
| Call shape | exactus `kwargs_typehints` (kwarg-only calls, full hints) | ✅ 0 findings |
| Tests | `pytest` + `node --test` | ✅ 580+ Python tests, JS suites per plugin |
| Coverage | exactus `coverage_tests` | ✅ ≥90% overall, ≥80% per file |
| Docs | exactus `docs` (every public symbol documented) | ✅ 0 missing |
| Determinism | `package.mjs` / builder hashing | ✅ reproducible artifacts |

The unusual rules that make it interesting: **no `assert` in tests** (a `expect`/`check`/`present` helper DSL), **no bare imports** (all imports are `from x import y`), **keyword-only call sites** enforced mechanically, and **zero lint/type-check suppressions anywhere**.

## 📂 Repository layout

```text
tools/            Lake Terminal, Arrow, release_builder, release_publish
plugins/          VS Code-compatible extensions (incl. devin_remote_control)
tests/            Python + JS test suites mirroring the tree
docs/             Full technical docs mirroring the tree
release.toml      This repo's own release-publish configuration
```

Requires **Python ≥ 3.13**; plugins target VS Code-compatible editors on macOS, Linux, and Windows.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`eslint.config.mjs`](eslint.config.mjs) — ", "*
- [`jsconfig.json`](jsconfig.json) — Provide jsconfig json content.
- [`package-lock.json`](package-lock.json) — Provide package lock json content.
- [`package.json`](package.json) — Provide package json content.
- [`pyproject.toml`](pyproject.toml) — Provide pyproject toml content.
- [`release.toml`](release.toml) — Provide release toml content.

### Subdirectories

- [`docs/`](docs/README.md) — Collects the technical documentation of every project folder, mirroring the workspace tree.
- [`plugins/`](plugins/README.md) — > Keep your tab strip clean by closing editors whose files no longer exist on disk.
- [`tests/`](tests/README.md) — Provide core.test js content.
- [`tools/`](tools/README.md) — Developer tools: the Lake Terminal control plane, the Arrow URI trust framework, and the release builder and publisher.
- [`wiki/`](wiki/README.md) — GitHub-wiki pages for this repository — flat `.md` files in wiki convention:

<!-- exactus:inventory:end -->
