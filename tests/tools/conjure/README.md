# `conjure/`

Additional AttachedVm coverage.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Tests for the conjure execution backends.
- [`helpers.py`](helpers.py) — Assertion helpers that avoid ``assert`` in test sources.
- [`test_attached_extra.py`](test_attached_extra.py) — Additional AttachedVm coverage.
- [`test_cli.py`](test_cli.py) — CLI coverage for conjure.
- [`test_config.py`](test_config.py) — Tests for immutable configuration validation.
- [`test_coverage_gates.py`](test_coverage_gates.py) — Target remaining backend lifecycle branches for coverage gates.
- [`test_docker.py`](test_docker.py) — Tests for the Docker execution backend.
- [`test_helpers.py`](test_helpers.py) — Coverage for the shared expectation helpers.
- [`test_inventory.py`](test_inventory.py) — Tests for resource discovery and attachment.
- [`test_lifecycle.py`](test_lifecycle.py) — Lifecycle tests for the high-level QEMU controller.
- [`test_local.py`](test_local.py) — Tests for direct host execution.
- [`test_main.py`](test_main.py) — Module entry-point coverage.
- [`test_more.py`](test_more.py) — Additional branch tests for process and transport behavior.
- [`test_network.py`](test_network.py) — Tests for automatic QEMU networking.
- [`test_process.py`](test_process.py) — Tests for QEMU subprocess ownership.
- [`test_qemu_snapshot.py`](test_qemu_snapshot.py) — Tests for qcow2 disk and QMP snapshot management.
- [`test_registry.py`](test_registry.py) — Tests for persistent named VM definitions.
- [`test_settings.py`](test_settings.py) — Tests for layered conjure settings.
- [`test_transport.py`](test_transport.py) — Tests for SSH command construction and result handling.
- [`test_utm.py`](test_utm.py) — Tests for UTM QEMU VM discovery and standalone command derivation.
- [`test_utm_runner.py`](test_utm_runner.py) — Tests for the UTM guest-agent runner.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
