# `tests/`

Point pytest at the workspace source root for package imports.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Tests for the Lake Terminal control plane.
- [`helpers.py`](helpers.py) — Assertion helpers that avoid ``assert`` in test sources.
- [`test_agent_identity.py`](test_agent_identity.py) — Tests for provider-neutral agent identity resolution.
- [`test_arrow_api.py`](test_arrow_api.py) — Lake Terminal Arrow adapter tests.
- [`test_attribution.py`](test_attribution.py) — Tests for deterministic attribution evidence resolution.
- [`test_cli_coverage.py`](test_cli_coverage.py) — Exercise the public CLI surface for coverage evidence.
- [`test_core.py`](test_core.py) — Tests for the shared-session service and repository.
- [`test_core_conpty.py`](test_core_conpty.py) — Cover ConPTY-backed session paths and daemon-control branches.
- [`test_daemon.py`](test_daemon.py) — Tests for the long-lived local control-plane daemon.
- [`test_foundation.py`](test_foundation.py) — Tests for identity, buffers, privacy, process, and detection helpers.
- [`test_gate_coverage.py`](test_gate_coverage.py) — Close the aggregate Python coverage gate through public behavior.
- [`test_helpers.py`](test_helpers.py) — Tests for the assertion helpers' failure paths.
- [`test_hooks.py`](test_hooks.py) — Tests for provider hooks, lifecycle, startup, hosts, and resources.
- [`test_installer.py`](test_installer.py) — Tests for the Lake Terminal control-plane installer.
- [`test_installer_pyz.py`](test_installer_pyz.py) — Tests for the self-contained ``pyz`` install mode.
- [`test_integration_harness.py`](test_integration_harness.py) — Tests for Lake Terminal's integration qualification harness.
- [`test_integration_probe.py`](test_integration_probe.py) — Tests for the deterministic integration probe workload.
- [`test_ipc.py`](test_ipc.py) — Tests for the authenticated local IPC layer.
- [`test_mcp_registration.py`](test_mcp_registration.py) — Tests for MCP client registration and the read-only server profile.
- [`test_mcp_server.py`](test_mcp_server.py) — Lake Terminal MCP stdio server tests.
- [`test_native_build.py`](test_native_build.py) — Tests for building native installers with ``tools.release_builder``.
- [`test_native_hooks.py`](test_native_hooks.py) — Tests for hook-backed sessions recorded by Claude Code and Devin hooks.
- [`test_package_main.py`](test_package_main.py) — Cover the package ``__main__`` dispatch entry point.
- [`test_platform_identity.py`](test_platform_identity.py) — Tests for editor, surface and agent detection from native variables.
- [`test_process_tree.py`](test_process_tree.py) — Tests for portable process-tree inspection.
- [`test_pyz_build.py`](test_pyz_build.py) — Tests for the single-source version and the ``.pyz`` build.
- [`test_release.py`](test_release.py) — Tests for ``lake --make-release``, a front end over release_publish.
- [`test_settings_cli.py`](test_settings_cli.py) — Tests for ``--settings``, ``--version`` and update CLI wiring.
- [`test_small_coverage_gates.py`](test_small_coverage_gates.py) — Close small per-file coverage gates with public behavior.
- [`test_tui.py`](test_tui.py) — Tests for the dependency-free terminal dashboard.
- [`test_update.py`](test_update.py) — Tests for version checks, self-update, and update notices.
- [`test_uri_handler_command.py`](test_uri_handler_command.py) — Tests for how the OS URI handler invokes Lake Terminal.
- [`test_url_handler.py`](test_url_handler.py) — Lake Terminal URI handler tests.
- [`test_web_coverage.py`](test_web_coverage.py) — Exercise the public web-dashboard surface for coverage evidence.

### Subdirectories

- [`integration/`](integration/README.md) — No direct files besides this README.

<!-- exactus:inventory:end -->
