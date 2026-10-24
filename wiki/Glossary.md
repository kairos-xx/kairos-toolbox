# Glossary

| Term | Meaning |
| --- | --- |
| **Control plane** | The single SQLite-backed state + API that every District Terminal surface (CLI, TUI, web, daemon, editor) reads and writes. |
| **Managed session** | A persistent terminal owned by District Terminal: stable UUID, short hex public ID, backend attribution, lifecycle events. |
| **Backend** | What actually holds the PTY: tmux (Unix), ConPTY (Windows), or process-only observation when no persistent PTY exists. |
| **Process-only mode** | Honest degraded mode — a process is observable (attribution, lifecycle) but not *reconnectable*. |
| **Attribution** | Which agent/human owns an execution: provider hook > editor/environment evidence > process signature. Recorded with the session. |
| **Bowman envelope** | A signed, expiring, provenance-carrying local URI invocation package — Bowman's unit of trust. |
| **Elevation** | Running an allowlisted command through the OS's *native* consent (UAC/sudo/macOS auth) — never silent privilege. |
| **`district://` URI** | The OS-registered scheme handled by `district --handle-uri` → Bowman `request/v1`. |
| **`.pyz`** | A `zipapp` archive — self-contained Python app zip, platform-tagged (`macos-arm64`, `linux-x86_64`, ...). The self-update format. |
| **`bootstrap`** | The `[pyz]` manifest key naming the file shipped as the archive's `__main__.py`. |
| **`local_dependencies`** | Manifest key: sibling project directories installed before the main project (e.g. `../bowman`). |
| **Action** | `stationer`'s unit of work: `COMMAND` or `WRITE` + description/command/path/content/cwd. Immutable. |
| **Plan** | The compiled, immutable list of `Action`s a release becomes — printable, dry-runnable, resumable. |
| **Journal** | The record of executed actions enabling `--resume`. |
| **`version_files`** | The explicit files a project treats as its version source (e.g. `product.py`). |
| **Tag template** | `"{project}/v{version}"` → `district/v1.2.3`. |
| **`Runner` protocol** | The typed subprocess boundary every external command flows through — `CommandRunner` in production, fakes in tests. |
| **exactus** | The repo's unified quality gate: lints, coverage_tests, docs, kwargs_typehints, readme, performance. |
| **`expect`/`check`/`present`** | The test assertion DSL replacing banned `assert` statements. |
| **PWA** | Progressive Web App — the installable phone interface of Devin Remote Control. |
| **ACU** | Devin's compute unit; sessions can carry an ACU limit. |

Next: [FAQ](FAQ) · [Architecture](Architecture)
