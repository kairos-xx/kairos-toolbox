# conjure

`conjure` is a host/guest-agnostic job execution layer for local hosts,
QEMU virtual machines, and Docker containers.

## Layout

- `tools/conjure/` — implementation and CLI.
- `test/tools/conjure/` — unit/CLI tests.
- `docs/tools/conjure/DOCS.md` — this document.

## CLI

Run with:

```bash
python -m tools.conjure --help
```

Use `--json` before the command group for machine-readable output.

### Local

```bash
python -m tools.conjure local run -- python --version
python -m tools.conjure local run --cwd /project -- python -m pytest
```

### Docker inventory

```bash
python -m tools.conjure docker images
python -m tools.conjure docker containers
python -m tools.conjure docker containers --running
python -m tools.conjure docker inspect CONTAINER_OR_IMAGE
```

### Docker jobs

```bash
python -m tools.conjure docker run --image python:3.13 -- python --version
python -m tools.conjure docker exec CONTAINER -- python --version
python -m tools.conjure docker exec CONTAINER --own -- python --version
```

`docker exec` attaches to an existing container. A stopped container is started.
Without `--own`, closing the runner does not stop/remove that external resource.

Transfers:

```bash
python -m tools.conjure docker copy CONTAINER upload ./project /work/project
python -m tools.conjure docker copy CONTAINER download /work/dist ./dist
```

### QEMU discovery

```bash
python -m tools.conjure qemu images ~/VMs /Volumes/VMs
python -m tools.conjure qemu image ~/VMs/linux.qcow2
python -m tools.conjure qemu processes
```

### Existing QEMU VM

```bash
python -m tools.conjure qemu attach \
  --user builder --identity ~/.ssh/builder --port 2222 \
  -- python3 --version
```

Transfers use the same SSH endpoint:

```bash
python -m tools.conjure qemu copy \
  --user builder --identity ~/.ssh/builder --port 2222 \
  upload ./project /tmp/project
```

### Persistent VM registry

Default registry: `~/.conjure/vms.json`.
Override with `registry --registry PATH`.

Register:

```bash
python -m tools.conjure registry register linux-arm64 \
  --description "ARM64 Linux builder" \
  --qemu qemu-system-aarch64 \
  --qemu-arg=-machine --qemu-arg=virt \
  --qemu-arg=-m --qemu-arg=8G \
  --user builder \
  --identity ~/.ssh/builder \
  --shutdown-arg=sudo \
  --shutdown-arg=shutdown \
  --shutdown-arg=-h \
  --shutdown-arg=now
```

Management:

```bash
python -m tools.conjure registry list
python -m tools.conjure registry details linux-arm64
python -m tools.conjure registry remove linux-arm64
```

Run a job:

```bash
python -m tools.conjure registry run linux-arm64 -- python3 --version
```

Transfer using a registered VM:

```bash
python -m tools.conjure registry copy linux-arm64 upload ./project /tmp/project
python -m tools.conjure registry copy linux-arm64 download /tmp/project/dist ./dist
```

QEMU registered VMs automatically allocate a free loopback host port and add
QEMU `hostfwd` for the configured guest SSH port. QEMU architecture, firmware,
machine type, accelerator, disks, and other VM-specific settings remain explicit
QEMU arguments and are therefore independent of host and guest OS.

### QEMU snapshots

`qemu_snapshot.py` returns a VM's disk to a known state at two layers:

- `QemuSnapshotManager` drives `qemu-img snapshot` on an *offline* qcow2
  image: `create`, `revert`/`restore`, `reset_to` (create-or-revert),
  `delete`, `delete_all`, `prune`, `verify`, `list`, `image_info`,
  `to_json`, `checkpoint`.
- `QemuVmSnapshotManager` drives a *running* VM through the QEMU Machine
  Protocol — `savevm`/`loadvm`/`delvm` via `QmpClient` over a Unix socket
  (`QmpEndpoint(unix_socket=...)`) or TCP (`QmpEndpoint(host=..., port=...)`),
  pausing and resuming the guest around each operation.

`QemuVm` consumes the disk layer so builds never dirty the image: two
`[tool.conjure.qemu]` settings select the disk and its baseline.

```toml
[tool.conjure.qemu]
snapshot_image = "/path/to/disk.qcow2"
baseline_snapshot = "conjure_baseline"
```

With both set, `reset_to` restores `baseline_snapshot` before every boot and
again once the guest stops; the first run captures the disk's current state
as the baseline. `qemu-img` refuses a disk a running VM holds, so shut the
VM down first.

The `QemuSnapshotCli` interface groups the same operations by backend —
`QemuSnapshotCli.run(argv=("disk", "--image", IMG, COMMAND, ...))`:

```text
disk --image PATH [--qemu-img PATH] [--dry-run] COMMAND
  list [--contains PAT] [--reverse] [--json]
  info [--json]
  create NAME [--replace]
  revert|restore NAME [--checkpoint NAME]
  reset NAME                 create the baseline if missing, then revert
  delete NAME [--missing-ok]
  delete-all
  prune --keep N             drop oldest snapshots, keep the newest N
  verify [--name NAME]       image readable (and snapshot present)
  version                    qemu-img version

qmp (--qmp-socket PATH | --qmp-host HOST --qmp-port PORT) [--timeout S] COMMAND
  status | blocks | pause | resume | list | commands
  create|revert|restore NAME [--no-pause] [--no-resume]
  delete NAME
  hmp MONITOR_COMMAND        arbitrary human-monitor line
```

Snapshot names may contain only letters, digits, `.`, `_` and `-`. Failures
raise `QemuSnapshotError` subclasses (`SnapshotNotFoundError`,
`SnapshotExistsError`, `UnsupportedImageError`, `QmpError`).

## Python API

The principal runner types are `LocalRunner`, `DockerRunner`, `QemuVm`, and
`AttachedVm`. All expose the same conceptual lifecycle: `start`, `run`,
`upload`, `download`, and `close` (where meaningful), and support context
management.

Discovery is provided by `DockerInventory` and `QemuInventory`. Persistent QEMU
VM definitions are managed by `VmRegistry` and `VmDefinition`. Offline and
running-VM snapshot management is provided by `QemuSnapshotManager`,
`QemuVmSnapshotManager` and `QmpClient` (see
[QEMU snapshots](#qemu-snapshots)).

## Coverage

The test suite is designed for a minimum 80% statement coverage for every
production file and 90% overall coverage.

## Configuration from `pyproject.toml`

`conjure` resolves configuration in this exact order:

```text
built-in defaults < ~/.conjure/settings.toml < [tool.conjure] in pyproject.toml < explicit CLI options
```

The user-level file `~/.conjure/settings.toml` uses the same
`[tool.conjure.*]` tables documented below. It is the right place for
machine-local values such as user names, home-directory paths and VM disk
images (for example `qemu.snapshot_image`) that must not be committed to a
shared `pyproject.toml`.

An omitted CLI option never overwrites a project value. Boolean CLI settings use
paired forms such as `--json` / `--no-json`, `--keep` / `--no-keep`, and
`--own` / `--no-own` where applicable, so omission remains distinguishable from
an explicit boolean choice.

By default, `conjure` searches the current directory and each parent for the
nearest `pyproject.toml`. Use global `--pyproject PATH` to select another file.
Unknown conjure configuration keys are rejected rather than silently ignored.

The distributed `pyproject.toml` contains every supported persistent setting as
a commented example under these tables:

- `[tool.conjure.general]`: JSON output and VM registry path.
- `[tool.conjure.local]`: local working directory.
- `[tool.conjure.docker]`: Docker executable, default image, working directory,
  create arguments, keepalive command, cleanup policy, stop timeout, and attached
  container ownership.
- `[tool.conjure.qemu]`: QEMU executable/arguments, NIC/network ID, automatic
  host forwarding, lifecycle timeouts, `qemu-img`, process discovery executable,
  image discovery roots, and the `snapshot_image`/`baseline_snapshot` pair that
  returns a disk to its baseline around every run (see
  [QEMU snapshots](#qemu-snapshots)).
- `[tool.conjure.ssh]`: username, identity, endpoint, OpenSSH/SCP executables,
  known-hosts file, connect timeout, and readiness polling interval.
- `[tool.conjure.guest]`: graceful shutdown command and guest SSH port.

For example:

```toml
[tool.conjure.docker]
image = "python:3.13"
working_directory = "/work"
stop_timeout_seconds = 30

[tool.conjure.ssh]
username = "builder"
identity_file = "~/.ssh/conjure"
host = "127.0.0.1"

[tool.conjure.guest]
ssh_port = 22
```

Then an explicit CLI option wins over it:

```bash
python -m tools.conjure docker run --image python:3.14 -- python --version
```

Here `python:3.14` overrides `python:3.13`, while the configured Docker working
directory and stop timeout remain inherited from `pyproject.toml`.

Use `--pyproject` before the command group:

```bash
python -m tools.conjure --pyproject /project/pyproject.toml docker images
```

Operational operands such as a VM name, container ID, source/destination path,
image path to inspect, and the command after `--` are intentionally not project
settings; they identify the individual operation being requested.
