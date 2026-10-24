# `conjure/`

Public API for host- and guest-agnostic execution backends.

<!-- exactus:inventory:start -->
## Directory contents

### Files

- [`__init__.py`](__init__.py) — Public API for host- and guest-agnostic execution backends.
- [`__main__.py`](__main__.py) — Module entry point for ``python -m tools.conjure``.
- [`attached.py`](attached.py) — Attach to an already-running SSH-capable VM.
- [`cli.py`](cli.py) — Command-line interface for conjure backends.
- [`config.py`](config.py) — Immutable execution-backend configuration.
- [`docker.py`](docker.py) — Disposable and attachable Docker execution backend.
- [`docker_inventory.py`](docker_inventory.py) — Docker image and container discovery.
- [`errors.py`](errors.py) — Typed errors shared by every execution backend.
- [`inventory.py`](inventory.py) — Typed inventory records for Docker and QEMU resources.
- [`local.py`](local.py) — Execution directly on the current host operating system.
- [`models.py`](models.py) — Typed values returned by qemu-runner.
- [`network.py`](network.py) — Loopback networking helpers for automatic QEMU forwarding.
- [`process.py`](process.py) — Ownership of a detached QEMU system process.
- [`qemu_inventory.py`](qemu_inventory.py) — QEMU disk-image and running-process discovery.
- [`qemu_snapshot.py`](qemu_snapshot.py) — QEMU qcow2 and QMP snapshot management.
- [`registry.py`](registry.py) — Persistent named QEMU VM definitions.
- [`settings.py`](settings.py) — Layered settings: defaults < user file < ``pyproject.toml`` < CLI.
- [`transport.py`](transport.py) — OpenSSH transport for Windows guests.
- [`utm.py`](utm.py) — Discover UTM-managed QEMU VMs and derive standalone QEMU commands.
- [`utm_runner.py`](utm_runner.py) — Run commands in a UTM guest through ``utmctl`` and the guest agent.
- [`vm.py`](vm.py) — Host- and guest-agnostic QEMU VM orchestration.

### Subdirectories

No subdirectories.

<!-- exactus:inventory:end -->
