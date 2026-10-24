# Copyright (c) 2026 Project contributors.
"""Charter backends: ngrok, pinggy, tunnl, runlocal and direct."""

from __future__ import annotations

from json import loads
from os import environ
from pathlib import Path
from platform import machine, system
from re import findall
from shutil import which
from subprocess import DEVNULL, Popen, run
from tarfile import open as open_tar
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import monotonic, sleep
from typing import TYPE_CHECKING

from tools.charter.errors import CharterError
from tools.charter.http import http_request
from tools.charter.lifecycle import manage, require_detach_support
from tools.charter.models import Charter, CharterConstants, JsonValue
from tools.charter.settings import Settings, SettingsLoader

if TYPE_CHECKING:
    from collections.abc import Callable
    from tarfile import TarFile, TarInfo
    from typing import IO


def _package_command(*, os_name: str) -> tuple[str, ...] | None:
    """Return the install command of the platform's package manager.

    Args:
        os_name: Lower-case operating system name.

    Returns:
        The command, or ``None`` when no supported manager exists.

    """
    if os_name == "darwin" and which(cmd="brew"):
        return ("brew", "install", "ngrok")
    if os_name == "windows" and which(cmd="winget"):
        return (
            "winget",
            "install",
            "ngrok",
            "-s",
            "msstore",
            "--accept-source-agreements",
            "--accept-package-agreements",
        )
    return None


def _download_ngrok_linux() -> Path:
    """Download ngrok from its official tarball into the user cache.

    Returns:
        The installed executable.

    Raises:
        CharterError: If the CPU is unsupported or the download fails.

    """
    architecture: str = machine().lower()
    suffix: str | None = CharterConstants.ngrok_architectures.get(
        architecture,
    )
    if suffix is None:
        msg: str = (
            f"Automatic ngrok install does not support Linux {architecture}."
        )
        raise CharterError(msg)
    destination: Path = Path.home() / ".cache" / "charter" / "ngrok"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="charter-ngrok-") as temporary:
        archive: Path = Path(temporary) / "ngrok.tgz"
        try:
            _ = archive.write_bytes(
                data=http_request(
                    url=CharterConstants.ngrok_download.format(arch=suffix),
                    timeout=30,
                ),
            )
            bundle: TarFile
            with open_tar(name=archive, mode="r:gz") as bundle:
                info: TarInfo = bundle.getmember(name="ngrok")
                member: IO[bytes] | None = bundle.extractfile(member=info)
                if member is None:
                    msg = "ngrok archive did not contain ngrok."
                    raise CharterError(msg)
                _ = destination.write_bytes(data=member.read())
        except (OSError, KeyError) as error:
            msg = f"Automatic ngrok download failed: {error}"
            raise CharterError(msg) from error
    destination.chmod(mode=0o755)
    return destination


def install_ngrok(*, settings: Settings | None = None) -> Path:
    """Find ngrok, installing it with an official method when absent.

    Args:
        settings: Resolved settings; loaded from ``pyproject.toml`` when
            omitted.

    Returns:
        The ngrok executable.

    Raises:
        CharterError: If ngrok cannot be found or installed.

    """
    resolved: Settings = settings or SettingsLoader.load()
    found: str | None = which(cmd=resolved.ngrok_executable or "ngrok")
    if found is not None:
        return Path(found)
    os_name: str = system().lower()
    if os_name == "linux":
        return _download_ngrok_linux()
    command: tuple[str, ...] | None = _package_command(os_name=os_name)
    if command is None:
        msg: str = (
            "ngrok is not installed and no supported automatic installer "
            "was found for this platform."
        )
        raise CharterError(msg)
    if run(args=command, check=False).returncode != 0:
        msg = f"Automatic ngrok installation failed: {' '.join(command)}"
        raise CharterError(msg)
    installed: str | None = which(cmd=resolved.ngrok_executable or "ngrok")
    if installed is None:
        msg = "ngrok installation completed but ngrok is not on PATH."
        raise CharterError(msg)
    return Path(installed)


def _wait_for_url(
    *,
    process: Popen[bytes],
    log_path: Path,
    suffix: str,
    timeout_seconds: float,
) -> str:
    """Wait until a backend log holds one HTTPS URL ending in ``suffix``.

    Args:
        process: The backend process, watched for early exit.
        log_path: Log file the backend writes to.
        suffix: Host suffix identifying the charter's public URL.
        timeout_seconds: Seconds to wait for the URL to appear.

    Returns:
        The public URL without a trailing slash.

    Raises:
        CharterError: If no matching URL appears in time.

    """
    deadline: float = monotonic() + timeout_seconds
    while monotonic() < deadline:
        content: str = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.is_file()
            else ""
        ).replace("\\/", "/")
        for candidate in findall(
            pattern=r"https://[A-Za-z0-9.-]+",
            string=content,
        ):
            if candidate.split(sep="/", maxsplit=3)[2].endswith(suffix):
                return candidate.rstrip("/")
        if process.poll() is not None:
            break
        sleep(0.1)
    msg: str = f"Charter process did not emit an HTTPS URL ending in {suffix}."
    raise CharterError(msg)


def _spawn_logged(
    *,
    command: list[str],
    suffix: str,
    settings: Settings,
) -> Charter:
    """Spawn a charter backend and read its public URL from its output.

    Args:
        command: Backend command line.
        suffix: Host suffix identifying the charter's public URL.
        settings: Resolved timeouts applied to the charter.

    Returns:
        The running charter.

    Raises:
        CharterError: If the backend never prints a matching URL.

    """
    with NamedTemporaryFile(
        prefix="charter-",
        suffix=".log",
        delete=False,
    ) as stream:
        log_path: Path = Path(stream.name)
    with log_path.open(mode="wb") as output:
        process: Popen[bytes] = Popen(
            args=command,
            stdin=DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=settings.keep_alive,
        )
    try:
        url: str = _wait_for_url(
            process=process,
            log_path=log_path,
            suffix=suffix,
            timeout_seconds=settings.url_timeout_seconds,
        )
    except CharterError:
        Charter(
            process=process,
            public_url="",
            log_path=log_path,
            stop_timeout_seconds=settings.stop_timeout_seconds,
        ).stop()
        raise
    return Charter(
        process=process,
        public_url=url,
        log_path=log_path,
        stop_timeout_seconds=settings.stop_timeout_seconds,
        keep_alive=settings.keep_alive,
    )


def _require(*, backend: str, tools: tuple[str, ...]) -> list[str]:
    """Resolve the executables a backend needs.

    Args:
        backend: Backend name, for the error message.
        tools: Executable names that must all be on ``PATH``.

    Returns:
        Their resolved paths, in order.

    Raises:
        CharterError: If any executable is missing.

    """
    found: list[str | None] = [which(cmd=tool) for tool in tools]
    if any(path is None for path in found):
        names: str = " and ".join(f"`{tool}`" for tool in tools)
        msg: str = f"{backend} requires {names} on PATH."
        raise CharterError(msg)
    return [path for path in found if path is not None]


def _start_pinggy(*, port: int, settings: Settings) -> Charter:
    """Start the free Pinggy ``expect``/blank-password flow.

    Args:
        port: Local port to expose.
        settings: Resolved settings supplying the executables.

    Returns:
        The running charter.

    """
    expect_path, ssh = _require(
        backend="pinggy",
        tools=(settings.expect_executable, settings.ssh_executable),
    )
    script: str = (
        "log_user 0; "
        f"spawn {ssh} -o StrictHostKeyChecking=accept-new -p 443 "
        f"-R0:localhost:{port} free.pinggy.io; "
        'expect "password:"; send "\\r"; '
        r"expect -re {https://[A-Za-z0-9.-]+\.free\.pinggy\.net}; "
        r"puts $expect_out(0,string); expect eof"
    )
    return _spawn_logged(
        command=[expect_path, "-c", script],
        suffix=".free.pinggy.net",
        settings=settings,
    )


def _start_tunnl(*, port: int, settings: Settings) -> Charter:
    """Start the free tunnl.gg reverse-SSH backend.

    Args:
        port: Local port to expose.
        settings: Resolved settings supplying the executables.

    Returns:
        The running charter.

    """
    (ssh,) = _require(backend="tunnl", tools=(settings.ssh_executable,))
    return _spawn_logged(
        command=[
            ssh,
            "-n",
            "-T",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-R",
            f"80:localhost:{port}",
            "proxy.tunnl.gg",
            "output=json",
        ],
        suffix=".tunnl.gg",
        settings=settings,
    )


def _start_runlocal(*, port: int, settings: Settings) -> Charter:
    """Start the free runlocal backend through ``npx``.

    Args:
        port: Local port to expose.
        settings: Resolved settings supplying the executables.

    Returns:
        The running charter.

    """
    (npx,) = _require(backend="runlocal", tools=(settings.npx_executable,))
    return _spawn_logged(
        command=[npx, "--yes", "runlocal", str(object=port)],
        suffix=".runlocal.eu",
        settings=settings,
    )


def _ngrok_url(*, document: JsonValue) -> str | None:
    """Pick the first HTTPS endpoint from ngrok's charter listing.

    Args:
        document: Decoded JSON from the local ngrok API.

    Returns:
        The public HTTPS URL, or ``None`` when none is listed yet.

    """
    if not isinstance(document, dict):
        return None
    tunnels: JsonValue = document.get("tunnels")
    if not isinstance(tunnels, list):
        return None
    for item in tunnels:
        if isinstance(item, dict):
            url: JsonValue = item.get("public_url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
    return None


def start_ngrok(*, port: int, settings: Settings | None = None) -> Charter:
    """Install and start ngrok, then wait for its public HTTPS URL.

    Args:
        port: Local port to expose.
        settings: Resolved settings; loaded from ``pyproject.toml`` when
            omitted. ``ngrok_authtoken`` overrides ``NGROK_AUTHTOKEN``.

    Returns:
        The running charter.

    Raises:
        CharterError: If no charter is established in time.

    """
    resolved: Settings = settings or SettingsLoader.load()
    executable: Path = install_ngrok(settings=resolved)
    environment: dict[str, str] = dict(environ)
    token: str | None = resolved.ngrok_authtoken or environ.get(
        "NGROK_AUTHTOKEN",
    )
    if token:
        environment["NGROK_AUTHTOKEN"] = token
    process: Popen[bytes] = Popen(
        args=[
            str(object=executable),
            "http",
            str(object=port),
            "--log",
            "stdout",
        ],
        stdout=DEVNULL,
        stderr=DEVNULL,
        env=environment,
        start_new_session=resolved.keep_alive,
    )
    deadline: float = monotonic() + resolved.ngrok_timeout_seconds
    last_error: str = ""
    while monotonic() < deadline and process.poll() is None:
        try:
            url: str | None = _ngrok_url(
                document=loads(
                    s=http_request(url=CharterConstants.ngrok_api, timeout=1),
                ),
            )
        except (OSError, ValueError) as error:
            last_error = str(object=error)
        else:
            if url is not None:
                return Charter(
                    process=process,
                    public_url=url,
                    stop_timeout_seconds=resolved.stop_timeout_seconds,
                    keep_alive=resolved.keep_alive,
                )
        sleep(0.2)
    Charter(
        process=process,
        public_url="",
        stop_timeout_seconds=resolved.stop_timeout_seconds,
    ).stop()
    msg: str = (
        "ngrok did not establish a charter. ngrok's free plan still requires "
        "a free account/authtoken: run `ngrok config add-authtoken <TOKEN>` "
        "or set NGROK_AUTHTOKEN."
        + (f" Last probe: {last_error}" if last_error else "")
    )
    raise CharterError(msg)


def open_charter(
    *,
    port: int,
    backend: str | None = None,
    settings: Settings | None = None,
) -> Charter | None:
    """Start the selected charter backend for one local port.

    Args:
        port: Local port to expose.
        backend: ``direct``, ``ngrok``, ``pinggy``, ``tunnl`` or
            ``runlocal``; the configured ``backend`` is used when omitted.
        settings: Resolved settings; loaded from ``pyproject.toml`` when
            omitted.

    Returns:
        The charter, or ``None`` for ``direct`` (nothing to expose).

    Raises:
        CharterError: If the backend is unknown or fails to start.

    """
    resolved: Settings = settings or SettingsLoader.load()
    chosen: str = backend or resolved.backend
    if resolved.keep_alive:
        require_detach_support()
    if chosen not in CharterConstants.backends:
        msg: str = f"Unknown charter backend: {chosen}"
        raise CharterError(msg)
    if chosen == "direct":
        return None
    starters: dict[str, Callable[..., Charter]] = {
        "ngrok": start_ngrok,
        "pinggy": _start_pinggy,
        "tunnl": _start_tunnl,
        "runlocal": _start_runlocal,
    }
    charter: Charter = starters[chosen](port=port, settings=resolved)
    manage(charter=charter, backend=chosen, port=port, settings=resolved)
    return charter
