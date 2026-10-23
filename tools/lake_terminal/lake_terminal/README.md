# `lake_terminal/`

Cross-platform coding-agent execution observability and control plane.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Cross-platform coding-agent execution observability and control plane.
- [`__main__.py`](__main__.py) — Execute the Lake Terminal command as a Python module.
- [`agent.py`](agent.py) — Detect interactive shells launched under configured local coding agents.
- [`arrow_api.py`](arrow_api.py) — Arrow trust boundary for Lake Terminal URI control.
- [`attribution.py`](attribution.py) — Resolve agent attribution from explicit evidence without guessing.
- [`buffer.py`](buffer.py) — Bounded in-memory terminal output buffers.
- [`claude.py`](claude.py) — Integrate Claude Code lifecycle hooks with lake-terminal attribution.
- [`cli.py`](cli.py) — Command-line interface for Lake Terminal agent execution observability.
- [`codex.py`](codex.py) — Integrate Codex lifecycle hooks with lake-terminal attribution.
- [`conpty.py`](conpty.py) — Native Windows ConPTY sessions owned by the Lake Terminal daemon.
- [`copy_url.py`](copy_url.py) — Handle safe lake-terminal clipboard URLs on macOS.
- [`core.py`](core.py) — Lake Terminal session lifecycle and canonical SQLite session state.
- [`cursor.py`](cursor.py) — Integrate Cursor embedded-agent hooks with Lake Terminal observability.
- [`daemon.py`](daemon.py) — Long-lived local control-plane daemon for subscriptions and buffers.
- [`daemon_control.py`](daemon_control.py) — Control-plane lifecycle for the detached local Lake Terminal daemon.
- [`devin.py`](devin.py) — Integrate Devin hook events with lake-terminal agent attribution.
- [`events.py`](events.py) — Normalized observability event definitions.
- [`history.py`](history.py) — Queryable command activity history backed exclusively by SQLite.
- [`hosts.py`](hosts.py) — Configure and query local/remote Lake Terminal execution hosts.
- [`identity.py`](identity.py) — Stable host, session, and process identity helpers.
- [`installer.py`](installer.py) — Install, inspect, and remove the Lake Terminal control-plane integration.
- [`integration.py`](integration.py) — Integration qualification harness for real and synthetic coding agents.
- [`integration_probe.py`](integration_probe.py) — Deterministic workload used to qualify Lake Terminal integrations.
- [`ipc.py`](ipc.py) — Authenticated local IPC for event publication and subscription.
- [`lifecycle.py`](lifecycle.py) — Authoritative shell command lifecycle instrumentation.
- [`mcp_server.py`](mcp_server.py) — Minimal-latency MCP stdio server for Lake Terminal.
- [`models.py`](models.py) — Canonical Lake Terminal data models.
- [`monitoring.py`](monitoring.py) — Non-invasive session monitoring and process inspection.
- [`notifications.py`](notifications.py) — Rule-driven native notifications for significant agent events.
- [`platform.py`](platform.py) — Cross-platform runtime and capability detection.
- [`privacy.py`](privacy.py) — Redact sensitive values and load retention/privacy policy.
- [`process.py`](process.py) — Run child processes without shell interpretation.
- [`process_tree.py`](process_tree.py) — Process-tree inspection and command correlation.
- [`product.py`](product.py) — Central Lake Terminal product identity and state directory.
- [`provider_state.py`](provider_state.py) — Correlate provider lifecycle hooks with canonical Lake session state.
- [`providers.py`](providers.py) — Agent-provider contracts and built-in provider registry.
- [`recorder.py`](recorder.py) — Persist and publish normalized observability events.
- [`resources.py`](resources.py) — Portable per-session process and resource telemetry.
- [`startup.py`](startup.py) — Cross-platform per-user web-dashboard startup management.
- [`storage.py`](storage.py) — Transactional SQLite state, command, process, and event storage.
- [`tui.py`](tui.py) — Dependency-free terminal dashboard for live agent activity.
- [`url_handler.py`](url_handler.py) — Handle Lake Terminal custom URIs through Arrow or the legacy copy route.
- [`web.py`](web.py) — Demand-driven FastAPI dashboard for Lake session observability.

### Subdirectories

- [`web_assets/`](web_assets/README.md) — Browser assets for the local FastAPI/WebSocket Lake Terminal control-plane dashboard.

<!-- exactus:inventory:end -->
