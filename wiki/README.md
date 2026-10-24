# `wiki/`

GitHub-wiki pages for this repository — flat `.md` files in wiki convention:

- `Home.md` is the landing page; `_Sidebar.md` and `_Footer.md` are wiki chrome.
- Pages interlink with extensionless links (`[text](Page-Name)` style), which
  GitHub resolves to the matching `.md` file both in the wiki and in-repo.

## Publishing as the repo wiki

```bash
git clone https://github.com/kairos-xx/kairos-toolbox.wiki.git
cp wiki/*.md kairos-toolbox.wiki/
cd kairos-toolbox.wiki
git add . && git commit -m "Import wiki" && git push
```

Or edit the files directly in this folder — GitHub renders them in-repo too.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`Agent-Attribution.md`](Agent-Attribution.md) — Attribution answers the question every multi-agent workspace asks: **which execution belongs to which agent or human?**
- [`Architecture.md`](Architecture.md) — This page explains how the components fit together and the rules that hold the design together.
- [`Bowman.md`](Bowman.md) — Bowman (`tools/bowman/bowman/`) is a standalone Python package that turns a local URI invocation — e.g. `district://request/v1/...` — into a **security boundary crossing** instead of a bare function call. District Terminal consumes it through `district/bowman_api.py`; Bowman has no District Terminal dependency and can be embedded by any Python application that exposes local URI endpoints.
- [`Devin-Remote-Control.md`](Devin-Remote-Control.md) — > A secure, phone-first remote IDE and Devin operations console — delivered > as a VS Code-compatible extension. Version 5.0.
- [`District-Terminal-CLI.md`](District-Terminal-CLI.md) — The installed command is `district` (a configurable alias — see \[Installing\](District-Terminal-Install)). All monitoring surfaces and editor integrations consume the same SQLite-backed state; the CLI is just one client of the control plane.
- [`District-Terminal-Data-Model.md`](District-Terminal-Data-Model.md) — > **SQLite is the only persistent control-plane state.**
- [`District-Terminal-Install.md`](District-Terminal-Install.md) — The installer is component-based: it owns exactly the artifacts it creates, records them in a manifest, and removes only them on uninstall.
- [`District-Terminal.md`](District-Terminal.md) — > **A cross-platform execution control plane for humans and coding agents.**
- [`Editor-Plugins.md`](Editor-Plugins.md) — Small, focused extensions for VS Code-compatible editors (VS Code, Cursor, Windsurf, VSCodium). Each ships a cross-platform `install.py` that discovers compatible editors and supports interactive install and `--uninstall`.
- [`Engineering-Standards.md`](Engineering-Standards.md) — This repository is held to a strict, machine-enforced bar. Nothing here is aspirational — every rule below is checked by a tool that fails the gate.
- [`FAQ.md`](FAQ.md) — No. It *uses* tmux/ConPTY as backends and adds the control plane they lack: queryable SQLite state, agent attribution, durable command lifecycle, event subscriptions, privacy controls, and machine-readable APIs for editors and automation. See \[District Terminal\](District-Terminal).
- [`Getting-Started.md`](Getting-Started.md) — **Requirements:** Python ≥ 3.13. Plugins target VS Code-compatible editors (VS Code, Cursor, Windsurf, VSCodium) on macOS, Linux, and Windows.
- [`Glossary.md`](Glossary.md) — Next: \[FAQ\](FAQ) · \[Architecture\](Architecture)
- [`Home.md`](Home.md) — Welcome to the documentation wiki for this repository: a toolkit for making agentic development **observable, attributable, and shippable**.
- [`Releasing.md`](Releasing.md) — How a District Terminal release actually flows through this repo's own tooling.
- [`Stationer-Builder.md`](Stationer-Builder.md) — `tools.stationer` turns one TOML manifest into real platform artifacts: a **macOS `.pkg`**, a **Debian `.deb`**, a **Windows ARM64 setup `.exe`**, or — with `--format pyz` — a **self-contained `name-version-platform.pyz`**. Every build also emits SHA-256 digests and a machine-readable `artifacts.json`.
- [`Stationer-Publish.md`](Stationer-Publish.md) — `tools.stationer` is a provider-agnostic release control plane for a single repository or a monorepo of independently versioned projects. Its defining choice: **planning is separate from execution**. A release is compiled into an immutable plan — inspectable, printable, dry-runnable, journaled, resumable — before anything mutates the world.
- [`Testing.md`](Testing.md) — Run them:
- [`_Footer.md`](_Footer.md) — District Terminal & Friends — execution control plane · URI trust · deterministic releases | \[Repository\](https://github.com/kairos-xx/kairos-toolbox)
- [`_Sidebar.md`](_Sidebar.md) — **\[Home\](Home)**

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
