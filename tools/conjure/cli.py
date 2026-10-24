"""Command-line interface for conjure backends."""

from __future__ import annotations

from argparse import (
    REMAINDER,
    SUPPRESS,
    ArgumentParser,
    Namespace,
)
from dataclasses import asdict
from json import dumps
from pathlib import Path
from sys import stderr as sys_stderr
from sys import stdout as sys_stdout
from typing import TYPE_CHECKING

from tools.conjure.attached import AttachedVm
from tools.conjure.config import (
    DockerConfig,
    GuestConfig,
    LocalConfig,
    QemuConfig,
    SshConfig,
)
from tools.conjure.docker import DockerRunner
from tools.conjure.docker_inventory import DockerInventory
from tools.conjure.local import LocalRunner
from tools.conjure.qemu_inventory import QemuInventory
from tools.conjure.registry import VmDefinition, VmRegistry
from tools.conjure.settings import Settings, SettingsLoader

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from tools.conjure.inventory import QemuImageInfo
    from tools.conjure.models import CommandResult
type RowValue = str | int | float | bool | Path | tuple[str, ...] | None
type Row = Mapping[str, RowValue]


def command_vector(*, values: Sequence[str]) -> tuple[str, ...]:
    """Validate and return the command vector after ``--``.

    Args:
        values: Raw remainder captured by the argument parser.

    Returns:
        Command vector without the separator.

    Raises:
        ValueError: If the remainder is empty.

    """
    tail: list[str] = list(values)
    if tail and tail[0] == "--":
        tail = tail[1:]
    if not tail:
        msg: str = "A command after -- is required."
        raise ValueError(msg)
    return tuple(tail)


def _emit_result(*, result: CommandResult, settings: Settings) -> None:
    """Print one command result in text or JSON mode.

    Args:
        result: Captured command result to render.
        settings: Resolved ``conjure`` settings.

    """
    if settings.json_output:
        sys_stdout.write(
            dumps(
                obj={
                    "command": list(result.command),
                    "return_code": result.return_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
            + "\n",
        )
        return
    sys_stdout.write(result.stdout)
    if result.stderr:
        sys_stderr.write(result.stderr)


def _emit_rows(
    *,
    rows: Sequence[Row],
    settings: Settings,
) -> None:
    """Print inventory rows in text or JSON mode.

    Args:
        rows: Inventory records to render.
        settings: Resolved ``conjure`` settings.

    """
    if settings.json_output:
        sys_stdout.write(dumps(obj=list(rows), default=str) + "\n")
        return
    for row in rows:
        sys_stdout.write(
            "\t".join(str(object=value) for value in row.values()),
        )
        sys_stdout.write("\n")


def _require_str(*, value: str | None, option: str) -> str:
    """Return a required string option or raise.

    Args:
        value: Resolved value.
        option: User-facing option name.

    Returns:
        The non-empty value.

    Raises:
        ValueError: If the value is missing.

    """
    if not value:
        msg: str = f"{option} is required."
        raise ValueError(msg)
    return value


def _ssh_config(
    *,
    args: Namespace,
    settings: Settings,
) -> SshConfig:
    """Build SSH configuration from arguments and settings.

    Args:
        args: Parsed namespace with ``user``, ``identity``, ``host`` and
            ``port`` attributes.
        settings: Resolved settings.

    Returns:
        Immutable SSH configuration.

    Raises:
        ValueError: If username or identity is unresolved.

    """
    return SshConfig(
        username=_require_str(
            value=args.user or settings.ssh_username,
            option="--user",
        ),
        identity_file=Path(
            _require_str(
                value=args.identity
                or (
                    None
                    if settings.ssh_identity_file is None
                    else str(object=settings.ssh_identity_file)
                ),
                option="--identity",
            ),
        ),
        host=args.host or settings.ssh_host,
        port=args.port if args.port is not None else settings.ssh_port,
        ssh_executable=settings.ssh_executable,
        scp_executable=settings.scp_executable,
        known_hosts_file=settings.ssh_known_hosts_file,
        connect_timeout_seconds=settings.ssh_connect_timeout_seconds,
        poll_interval_seconds=settings.ssh_poll_interval_seconds,
    )


def _local_run(*, args: Namespace, settings: Settings) -> None:
    """Execute a command on the local host.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    with LocalRunner(
        config=LocalConfig(
            working_directory=(args.cwd or settings.local_working_directory),
        ),
    ) as runner:
        result: CommandResult = runner.run(
            command=command_vector(values=args.command),
        )
    _emit_result(result=result, settings=settings)


def _docker_images(*, args: Namespace, settings: Settings) -> None:
    """List Docker images.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    del args
    inventory: DockerInventory = DockerInventory(
        docker_executable=settings.docker_executable,
    )
    _emit_rows(
        rows=tuple(asdict(obj=image) for image in inventory.images()),
        settings=settings,
    )


def _docker_containers(*, args: Namespace, settings: Settings) -> None:
    """List Docker containers.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    inventory: DockerInventory = DockerInventory(
        docker_executable=settings.docker_executable,
    )
    _emit_rows(
        rows=tuple(
            asdict(obj=container)
            for container in inventory.containers(running=args.running)
        ),
        settings=settings,
    )


def _docker_inspect(*, args: Namespace, settings: Settings) -> None:
    """Inspect one Docker resource.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    inventory: DockerInventory = DockerInventory(
        docker_executable=settings.docker_executable,
    )
    sys_stdout.write(
        dumps(obj=inventory.inspect(resource=args.resource), indent=2) + "\n",
    )


def _docker_run(*, args: Namespace, settings: Settings) -> None:
    """Run a command inside a fresh disposable container.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    remove: bool = (
        settings.docker_remove_on_close if args.keep is None else not args.keep
    )
    runner: DockerRunner = DockerRunner(
        config=DockerConfig(
            image=_require_str(
                value=args.image or settings.docker_image,
                option="--image",
            ),
            docker_executable=settings.docker_executable,
            create_arguments=settings.docker_create_arguments,
            command=settings.docker_command,
            working_directory=settings.docker_working_directory,
            remove_on_close=remove,
            stop_timeout_seconds=settings.docker_stop_timeout_seconds,
        ),
    )
    runner.start()
    try:
        result: CommandResult = runner.run(
            command=command_vector(values=args.command),
        )
    finally:
        runner.close()
    _emit_result(result=result, settings=settings)


def _docker_exec(*, args: Namespace, settings: Settings) -> None:
    """Execute a command inside an existing container.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    runner: DockerRunner = DockerRunner(
        config=DockerConfig(
            image="unused",
            docker_executable=settings.docker_executable,
            working_directory=settings.docker_working_directory,
            stop_timeout_seconds=settings.docker_stop_timeout_seconds,
        ),
        container_id=args.container,
        owns_container=(
            settings.docker_owns_container if args.own is None else args.own
        ),
    )
    runner.start()
    try:
        result: CommandResult = runner.run(
            command=command_vector(values=args.command),
        )
    finally:
        runner.close()
    _emit_result(result=result, settings=settings)


def _docker_copy(*, args: Namespace, settings: Settings) -> None:
    """Copy between the host and an existing container.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    runner: DockerRunner = DockerRunner(
        config=DockerConfig(
            image="unused",
            docker_executable=settings.docker_executable,
        ),
        container_id=args.container,
        owns_container=False,
    )
    if args.direction == "upload":
        runner.upload(
            source=Path(args.source),
            destination=args.destination,
        )
    else:
        runner.download(
            source=args.source,
            destination=Path(args.destination),
        )


def _qemu_inventory(*, settings: Settings) -> QemuInventory:
    """Return a QEMU inventory using configured executables.

    Args:
        settings: Resolved ``conjure`` settings.

    Returns:
        QEMU inventory helper bound to the configured executables.

    """
    return QemuInventory(
        img_executable=settings.qemu_img_executable,
        process_executable=settings.qemu_process_executable,
    )


def _qemu_images(*, args: Namespace, settings: Settings) -> None:
    """Discover QEMU disk images below roots.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    roots: tuple[Path, ...] = (
        tuple(Path(root) for root in args.roots)
        if args.roots
        else settings.qemu_image_roots
    )
    _emit_rows(
        rows=tuple(
            asdict(obj=image)
            for image in _qemu_inventory(settings=settings).images(
                roots=roots,
            )
        ),
        settings=settings,
    )


def _qemu_image(*, args: Namespace, settings: Settings) -> None:
    """Read qemu-img metadata for one image.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    image: QemuImageInfo = _qemu_inventory(settings=settings).image_details(
        path=Path(args.path),
    )
    _emit_rows(rows=(asdict(obj=image),), settings=settings)


def _qemu_processes(*, args: Namespace, settings: Settings) -> None:
    """List running QEMU processes.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    del args
    _emit_rows(
        rows=tuple(
            asdict(obj=process)
            for process in _qemu_inventory(settings=settings).processes()
        ),
        settings=settings,
    )


def _qemu_attach(*, args: Namespace, settings: Settings) -> None:
    """Run a command on an existing SSH-reachable VM.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    with AttachedVm(ssh=_ssh_config(args=args, settings=settings)) as vm:
        result: CommandResult = vm.run(
            command=command_vector(values=args.command),
        )
    _emit_result(result=result, settings=settings)


def _qemu_copy(*, args: Namespace, settings: Settings) -> None:
    """Copy between the host and an existing VM.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    with AttachedVm(ssh=_ssh_config(args=args, settings=settings)) as vm:
        if args.direction == "upload":
            vm.upload(
                source=Path(args.source),
                destination=args.destination,
            )
        else:
            vm.download(
                source=args.source,
                destination=Path(args.destination),
            )


def _registry(*, args: Namespace, settings: Settings) -> VmRegistry:
    """Return the configured persistent registry.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    Returns:
        Registry bound to the configured storage path.

    """
    return VmRegistry(
        path=(
            settings.registry_path
            if args.registry is None
            else Path(args.registry)
        ),
    )


def _registry_register(*, args: Namespace, settings: Settings) -> None:
    """Persist a new VM definition.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    registry: VmRegistry = _registry(args=args, settings=settings)
    qemu: QemuConfig = QemuConfig(
        executable=Path(
            _require_str(
                value=args.qemu
                or (
                    None
                    if settings.qemu_executable is None
                    else str(object=settings.qemu_executable)
                ),
                option="--qemu",
            ),
        ),
        arguments=(
            tuple(args.qemu_arg) if args.qemu_arg else settings.qemu_arguments
        ),
        network_device=settings.qemu_network_device,
        network_id=settings.qemu_network_id,
        automatic_host_forward=settings.qemu_automatic_host_forward,
        startup_timeout_seconds=settings.qemu_startup_timeout_seconds,
        shutdown_timeout_seconds=settings.qemu_shutdown_timeout_seconds,
        terminate_timeout_seconds=settings.qemu_terminate_timeout_seconds,
        snapshot_image=settings.qemu_snapshot_image,
        baseline_snapshot=settings.qemu_baseline_snapshot,
        qemu_img=settings.qemu_img_executable,
    )
    registry.register(
        definition=VmDefinition(
            name=args.name,
            description=args.description or "",
            qemu=qemu,
            ssh=_ssh_config(args=args, settings=settings),
            guest=GuestConfig(
                shutdown_command=(
                    tuple(args.shutdown_arg)
                    if args.shutdown_arg
                    else settings.guest_shutdown_command
                ),
                ssh_port=settings.guest_ssh_port,
            ),
        ),
        replace=args.replace,
    )
    sys_stdout.write(f"registered {args.name}\n")


def _registry_list(*, args: Namespace, settings: Settings) -> None:
    """List registered VM definitions.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    registry: VmRegistry = _registry(args=args, settings=settings)
    _emit_rows(
        rows=tuple(
            {"name": item.name, "description": item.description}
            for item in registry.list()
        ),
        settings=settings,
    )


def _registry_details(*, args: Namespace, settings: Settings) -> None:
    """Print one registered VM definition.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    registry: VmRegistry = _registry(args=args, settings=settings)
    definition: VmDefinition = registry.details(name=args.name)
    if settings.json_output:
        sys_stdout.write(dumps(obj=definition.name) + "\n")
        return
    sys_stdout.write(f"{definition.name}\t{definition.description}\n")


def _registry_remove(*, args: Namespace, settings: Settings) -> None:
    """Remove a registered VM definition.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    registry: VmRegistry = _registry(args=args, settings=settings)
    removed: VmDefinition = registry.remove(name=args.name)
    sys_stdout.write(f"removed {removed.name}\n")


def _registry_run(*, args: Namespace, settings: Settings) -> None:
    """Run a command inside a registered QEMU VM.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    registry: VmRegistry = _registry(args=args, settings=settings)
    with registry.use(name=args.name) as vm:
        result: CommandResult = vm.run(
            command=command_vector(values=args.command),
        )
    _emit_result(result=result, settings=settings)


def _registry_copy(*, args: Namespace, settings: Settings) -> None:
    """Copy between the host and a registered QEMU VM.

    Args:
        args: Parsed command-line arguments.
        settings: Resolved ``conjure`` settings.

    """
    registry: VmRegistry = _registry(args=args, settings=settings)
    with registry.use(name=args.name) as vm:
        if args.direction == "upload":
            vm.upload(
                source=Path(args.source),
                destination=args.destination,
            )
        else:
            vm.download(
                source=args.source,
                destination=Path(args.destination),
            )


def _add_ssh_options(*, parser: ArgumentParser) -> None:
    """Attach common SSH endpoint options to a parser.

    Args:
        parser: Argument parser to extend.

    """
    parser.add_argument("--user")
    parser.add_argument("--identity")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)


def _add_command(*, parser: ArgumentParser) -> None:
    """Attach the trailing command remainder to a parser.

    Args:
        parser: Argument parser to extend.

    """
    parser.add_argument("command", nargs=REMAINDER)


def _add_copy_options(*, parser: ArgumentParser) -> None:
    """Attach direction/source/destination copy operands.

    Args:
        parser: Argument parser to extend.

    """
    parser.add_argument("direction", choices=("upload", "download"))
    parser.add_argument("source")
    parser.add_argument("destination")


def _local_group(*, local: ArgumentParser) -> None:
    """Register the ``local`` command group.

    Args:
        local: ``local`` subcommand parser.

    """
    add_subparser: Callable[..., ArgumentParser] = local.add_subparsers(
        dest="command_name",
        required=True,
    ).add_parser
    local_run: ArgumentParser = add_subparser("run")
    local_run.add_argument("--cwd", type=Path)
    _add_command(parser=local_run)
    local_run.set_defaults(handler=_local_run)


def _docker_group(*, docker: ArgumentParser) -> None:
    """Register the ``docker`` command group.

    Args:
        docker: ``docker`` subcommand parser.

    """
    add_subparser: Callable[..., ArgumentParser] = docker.add_subparsers(
        dest="command_name",
        required=True,
    ).add_parser
    add_subparser("images").set_defaults(
        handler=_docker_images,
    )
    docker_ps: ArgumentParser = add_subparser("containers")
    docker_ps.add_argument("--running", action="store_true")
    docker_ps.set_defaults(handler=_docker_containers)
    docker_inspect: ArgumentParser = add_subparser("inspect")
    docker_inspect.add_argument("resource")
    docker_inspect.set_defaults(handler=_docker_inspect)
    docker_run: ArgumentParser = add_subparser("run")
    docker_run.add_argument("--image")
    docker_run.add_argument(
        "--keep",
        dest="keep",
        action="store_true",
        default=None,
    )
    docker_run.add_argument(
        "--no-keep",
        dest="keep",
        action="store_false",
        default=SUPPRESS,
    )
    _add_command(parser=docker_run)
    docker_run.set_defaults(handler=_docker_run)
    docker_exec: ArgumentParser = add_subparser("exec")
    docker_exec.add_argument("container")
    docker_exec.add_argument(
        "--own",
        dest="own",
        action="store_true",
        default=None,
    )
    docker_exec.add_argument(
        "--no-own",
        dest="own",
        action="store_false",
        default=SUPPRESS,
    )
    _add_command(parser=docker_exec)
    docker_exec.set_defaults(handler=_docker_exec)
    docker_copy: ArgumentParser = add_subparser("copy")
    docker_copy.add_argument("container")
    _add_copy_options(parser=docker_copy)
    docker_copy.set_defaults(handler=_docker_copy)


def _qemu_group(*, qemu: ArgumentParser) -> None:
    """Register the ``qemu`` command group.

    Args:
        qemu: ``qemu`` subcommand parser.

    """
    add_subparser: Callable[..., ArgumentParser] = qemu.add_subparsers(
        dest="command_name",
        required=True,
    ).add_parser
    qemu_images: ArgumentParser = add_subparser("images")
    qemu_images.add_argument("roots", nargs="*")
    qemu_images.set_defaults(handler=_qemu_images)
    qemu_image: ArgumentParser = add_subparser("image")
    qemu_image.add_argument("path")
    qemu_image.set_defaults(handler=_qemu_image)
    add_subparser("processes").set_defaults(
        handler=_qemu_processes,
    )
    qemu_attach: ArgumentParser = add_subparser("attach")
    _add_ssh_options(parser=qemu_attach)
    _add_command(parser=qemu_attach)
    qemu_attach.set_defaults(handler=_qemu_attach)
    qemu_copy: ArgumentParser = add_subparser("copy")
    _add_ssh_options(parser=qemu_copy)
    _add_copy_options(parser=qemu_copy)
    qemu_copy.set_defaults(handler=_qemu_copy)


def _registry_group(*, registry: ArgumentParser) -> None:
    """Register the ``registry`` command group.

    Args:
        registry: ``registry`` subcommand parser.

    """
    registry.add_argument(
        "--registry",
        type=Path,
        help="Override the VM registry file.",
    )
    add_subparser: Callable[..., ArgumentParser] = registry.add_subparsers(
        dest="command_name",
        required=True,
    ).add_parser
    register: ArgumentParser = add_subparser("register")
    register.add_argument("name")
    register.add_argument("--description")
    register.add_argument("--qemu")
    register.add_argument("--qemu-arg", action="append", default=[])
    register.add_argument("--shutdown-arg", action="append", default=[])
    register.add_argument("--replace", action="store_true")
    _add_ssh_options(parser=register)
    register.set_defaults(handler=_registry_register)
    add_subparser("list").set_defaults(
        handler=_registry_list,
    )
    details: ArgumentParser = add_subparser("details")
    details.add_argument("name")
    details.set_defaults(handler=_registry_details)
    remove: ArgumentParser = add_subparser("remove")
    remove.add_argument("name")
    remove.set_defaults(handler=_registry_remove)
    registry_run: ArgumentParser = add_subparser("run")
    registry_run.add_argument("name")
    _add_command(parser=registry_run)
    registry_run.set_defaults(handler=_registry_run)
    registry_copy: ArgumentParser = add_subparser("copy")
    registry_copy.add_argument("name")
    _add_copy_options(parser=registry_copy)
    registry_copy.set_defaults(handler=_registry_copy)


def _parser() -> ArgumentParser:
    """Build the full conjure argument parser.

    Returns:
        Top-level parser with all command groups.

    """
    parser: ArgumentParser = ArgumentParser(prog="conjure")
    parser.add_argument(
        "--pyproject",
        type=Path,
        help="Explicit pyproject.toml to load settings from.",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=None,
        help="Emit machine-readable JSON output.",
    )
    parser.add_argument(
        "--no-json",
        dest="json_output",
        action="store_false",
        default=SUPPRESS,
        help="Emit human-readable output.",
    )
    add_subparser: Callable[..., ArgumentParser] = parser.add_subparsers(
        dest="group",
        required=True,
    ).add_parser
    _local_group(local=add_subparser("local"))
    _docker_group(docker=add_subparser("docker"))
    _qemu_group(qemu=add_subparser("qemu"))
    _registry_group(registry=add_subparser("registry"))
    return parser


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Parse arguments and dispatch one conjure command.

    Args:
        arguments: Argument vector without the program name; ``sys.argv``
            is used when omitted.

    Returns:
        ``0`` on success, ``1`` on a handled failure.

    """
    parser: ArgumentParser = _parser()
    args: Namespace = parser.parse_args(
        args=None if arguments is None else list(arguments),
    )
    try:
        settings: Settings = SettingsLoader.load(
            pyproject=args.pyproject,
            overrides={
                "json_output": args.json_output,
            },
        )
        args.handler(args=args, settings=settings)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        sys_stderr.write(f"conjure: {error}\n")
        return 1
    return 0
