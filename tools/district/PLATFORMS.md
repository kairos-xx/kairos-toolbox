# Supported Platforms

District Terminal is OS- and CPU-architecture independent at the control-plane layer.
Backend capabilities are discovered at runtime.

| Platform | Architectures | Backend | Support |
| --- | --- | --- | --- |
| macOS | arm64, x86_64 | tmux when installed; process fallback | Supported |
| Linux | x86_64, arm64 | tmux when installed; process fallback | Supported |
| Windows | x86_64 | ConPTY when available; process fallback | Supported |
| Windows | arm64 | ConPTY/process | Experimental until native CI is available |

GitHub CI runs Python 3.13 tests on Linux, Windows, Intel macOS and Apple Silicon macOS
where hosted runners are available. Architecture normalization maps `amd64`/`x64` to
`x86_64` and `aarch64` to `arm64`.

A process-only fallback remains observable but is not falsely reported as reconnectable.

## Qualification levels

- **macOS — qualified target:** native District runtime and real-provider
  integration for installed Codex, Claude, Cursor, and Devin surfaces.
- **Linux — headless qualified target:** Docker/native CLI runtime, tmux,
  process observation, SQLite, daemon/IPC, lifecycle, web security, and
  synthetic provider contracts. Docker Sandboxes can add real agent kits.
- **Windows — implemented/contract-tested:** native qualification remains a
  release follow-up until a Windows runner is available.

A platform is not described as fully qualified solely because mocked unit tests
exist for its backend.
