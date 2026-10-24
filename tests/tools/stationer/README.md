# `stationer/`

Tests for the cross-platform release builder and publishing control plane.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Tests for stationer, the release builder and publishing control plane.
- [`fake_hosts.py`](fake_hosts.py) — Adapt recording fakes to the ``open_host`` session boundary.
- [`fake_sessions.py`](fake_sessions.py) — Recording stand-ins for the ``conjure`` runners the builders use.
- [`helpers.py`](helpers.py) — Assertion helpers that avoid ``assert`` in test sources.
- [`test_artifacts.py`](test_artifacts.py) — Artifact checksum tests.
- [`test_build_report.py`](test_build_report.py) — Tests for ``build/BUILD.md`` and the merged ``artifacts.json``.
- [`test_builder.py`](test_builder.py) — Tests for stationer configuration and deterministic helpers.
- [`test_cli.py`](test_cli.py) — Command-line tests.
- [`test_config.py`](test_config.py) — Configuration tests.
- [`test_configured.py`](test_configured.py) — Tests for ``\[tool.stationer.builder\]``-driven bare invocations.
- [`test_conjure.py`](test_conjure.py) — Tests for remote job types and result-bundle verification.
- [`test_engine.py`](test_engine.py) — Engine guard tests.
- [`test_helpers.py`](test_helpers.py) — Tests for the assertion helpers' failure paths.
- [`test_installer_support.py`](test_installer_support.py) — Tests for the installer payload helpers shared by every builder.
- [`test_installerless_results.py`](test_installerless_results.py) — Tests for builds that produce a frozen executable but no installer.
- [`test_layout.py`](test_layout.py) — Tests for the ``<os>/<arch>/<abi>/<package>/`` output layout.
- [`test_local_dependencies.py`](test_local_dependencies.py) — Tests for installing local sibling projects before the main project.
- [`test_planner.py`](test_planner.py) — Planner tests.
- [`test_progress.py`](test_progress.py) — Tests for guest-to-host build progress reporting.
- [`test_providers.py`](test_providers.py) — Tests for the release providers' action planning.
- [`test_publish_update.py`](test_publish_update.py) — Tests for publishing to an existing release and for explicit files.
- [`test_publisher.py`](test_publisher.py) — Tests for publishing the built artifacts to a GitHub release.
- [`test_pypi_builder.py`](test_pypi_builder.py) — Tests for the PyPI distribution builder.
- [`test_pyz_builder.py`](test_pyz_builder.py) — Tests for the self-contained ``.pyz`` output format.
- [`test_real_pipelines.py`](test_real_pipelines.py) — Real-environment pipeline tests.
- [`test_remote_builder.py`](test_remote_builder.py) — Tests for the remote pull-worker build backend.
- [`test_remote_cli.py`](test_remote_cli.py) — Tests for ``stationer --remote``.
- [`test_remote_exposure.py`](test_remote_exposure.py) — Tests for the exposure facade over ``tools.charter``.
- [`test_remote_server.py`](test_remote_server.py) — Tests for the authenticated one-job build server.
- [`test_remote_vm.py`](test_remote_vm.py) — Tests for running the remote worker inside a VM session.
- [`test_remote_workers.py`](test_remote_workers.py) — Tests for the rendered remote build workers.
- [`test_sessions.py`](test_sessions.py) — Tests for the ``conjure`` session openers the builders inject.
- [`test_upload.py`](test_upload.py) — Tests for the safe registry upload helper.
- [`test_versioning.py`](test_versioning.py) — Versioning tests.
- [`test_wiki.py`](test_wiki.py) — Tests for the GitHub Wiki synchronizer.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
