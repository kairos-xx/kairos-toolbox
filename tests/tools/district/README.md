# `tests/`

Point pytest at the workspace source root for package imports.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Tests for the District Terminal control plane.
- [`helpers.py`](helpers.py) — Assertion helpers that avoid ``assert`` in test sources.
- [`test_agent_identity.py`](test_agent_identity.py) — Tests for provider-neutral agent identity resolution.
- [`test_attribution.py`](test_attribution.py) — Tests for deterministic attribution evidence resolution.
- [`test_bowman_api.py`](test_bowman_api.py) — District Terminal Bowman adapter tests.
- [`test_cli_coverage.py`](test_cli_coverage.py) — Exercise the public CLI surface for coverage evidence.
- [`test_core.py`](test_core.py) — Tests for the shared-session service and repository.
- [`test_core_conpty.py`](test_core_conpty.py) — Cover ConPTY-backed session paths and daemon-control branches.
- [`test_daemon.py`](test_daemon.py) — Tests for the long-lived local control-plane daemon.
- [`test_district_commands.py`](test_district_commands.py) — Tests for the district command layer behind the web command palette.
- [`test_exposure.py`](test_exposure.py) — Tests for optional public exposure of the web dashboard.
- [`test_foundation.py`](test_foundation.py) — Tests for identity, buffers, privacy, process, and detection helpers.
- [`test_gate_coverage.py`](test_gate_coverage.py) — Close the aggregate Python coverage gate through public behavior.
- [`test_helpers.py`](test_helpers.py) — Tests for the assertion helpers' failure paths.
- [`test_hooks.py`](test_hooks.py) — Tests for provider hooks, lifecycle, startup, hosts, and resources.
- [`test_host_info.py`](test_host_info.py) — Tests for the host facts shown on the dashboard overview.
- [`test_installer.py`](test_installer.py) — Tests for the District Terminal control-plane installer.
- [`test_installer_pyz.py`](test_installer_pyz.py) — Tests for the self-contained ``pyz`` install mode.
- [`test_integration_harness.py`](test_integration_harness.py) — Tests for District Terminal's integration qualification harness.
- [`test_integration_probe.py`](test_integration_probe.py) — Tests for the deterministic integration probe workload.
- [`test_ipc.py`](test_ipc.py) — Tests for the authenticated local IPC layer.
- [`test_keys.py`](test_keys.py) — Tests for the key grammar behind keystroke-level input.
- [`test_mcp_registration.py`](test_mcp_registration.py) — Tests for MCP client registration and the read-only server profile.
- [`test_mcp_server.py`](test_mcp_server.py) — District Terminal MCP stdio server tests.
- [`test_native_build.py`](test_native_build.py) — Tests for building native installers with ``tools.stationer``.
- [`test_native_hooks.py`](test_native_hooks.py) — Tests for hook-backed sessions recorded by Claude Code and Devin hooks.
- [`test_package_main.py`](test_package_main.py) — Cover the package ``__main__`` dispatch entry point.
- [`test_pairing.py`](test_pairing.py) — Tests for device pairing: signed tokens, the key file and LAN peers.
- [`test_platform_identity.py`](test_platform_identity.py) — Tests for editor, surface and agent detection from native variables.
- [`test_process_tree.py`](test_process_tree.py) — Tests for portable process-tree inspection.
- [`test_pyz_build.py`](test_pyz_build.py) — Tests for the single-source version and the ``.pyz`` build.
- [`test_qr.py`](test_qr.py) — Tests for the terminal QR code and the banner that carries the link.
- [`test_release.py`](test_release.py) — Tests for ``district --make-release``, a front end over stationer.
- [`test_selftest.py`](test_selftest.py) — Tests for the structured ``district --test`` self-check.
- [`test_settings_cli.py`](test_settings_cli.py) — Tests for ``--settings``, ``--version`` and update CLI wiring.
- [`test_small_coverage_gates.py`](test_small_coverage_gates.py) — Close small per-file coverage gates with public behavior.
- [`test_tui.py`](test_tui.py) — Tests for the dependency-free terminal dashboard.
- [`test_update.py`](test_update.py) — Tests for version checks, self-update, and update notices.
- [`test_uri_handler_command.py`](test_uri_handler_command.py) — Tests for how the OS URI handler invokes District Terminal.
- [`test_uri_registry.py`](test_uri_registry.py) — Tests for issuing, listing, revoking and redeeming district URIs.
- [`test_url_handler.py`](test_url_handler.py) — District Terminal URI handler tests.
- [`test_web_api.py`](test_web_api.py) — Tests for the command, host and URI endpoints of the web console.
- [`test_web_assets.py`](test_web_assets.py) — Tests of how the dashboard serves its static console assets.
- [`test_web_coverage.py`](test_web_coverage.py) — Exercise the public web-dashboard surface for coverage evidence.
- [`test_web_pairing.py`](test_web_pairing.py) — End-to-end tests of device pairing in the web dashboard.

### Subdirectories

- [`integration/`](integration/README.md) — No direct files besides this README.
- [`js/`](js/README.md) — Tests for the small client that talks to the dashboard's API.

<!-- exactus:inventory:end -->
