# Copyright (c) 2026 Project contributors.
"""Check GitHub Releases for newer District Terminal builds and self-update."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from importlib import import_module
from json import JSONDecodeError, dumps, loads
from os import environ
from os import name as os_name
from pathlib import Path
from platform import machine, system
from re import match
from shutil import rmtree
from subprocess import DEVNULL, Popen
from subprocess import __dict__ as subprocess_symbols
from sys import argv, executable, stderr, stdout
from time import time
from typing import TYPE_CHECKING, Final, TextIO, cast
from urllib.parse import urlparse
from zipfile import is_zipfile

from .product import Product, secure_private_file, state_directory

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from re import Match
    from types import ModuleType
    from urllib.request import BaseHandler, OpenerDirector, Request

    from .installer import Layout
    from .json_config import JsonObject, JsonValue
type Version = tuple[int, ...]


class UpdateConstants:
    """Group update-check constants.

    Attributes:
        default_repository: Monorepo that publishes releases.
        default_tag_prefix: Project-qualified tag prefix.
        default_check: Default check cadence.
        default_timeout: Default network timeout in seconds.
        intervals: Seconds between automatic checks per cadence.
        channels: Accepted release channels.
        notice_interval: Seconds between repeat notices per version.
        api_root: GitHub API origin.
        download_timeout: Minimum timeout for asset downloads.
        checksum_asset: Release checksum manifest name.
        download_directory: Scratch directory for staged downloads.
        helper_name: File name of the detached update helper script.
        helper_source: Source text of the detached update helper script.

    """

    default_repository: Final[str] = "kairos-xx/kairos-toolbox"
    default_tag_prefix: Final[str] = "district/v"
    default_check: Final[str] = "daily"
    default_timeout: Final[int] = 2
    intervals: Final[Mapping[str, int]] = {
        "always": 0,
        "hourly": 3600,
        "daily": 86_400,
        "weekly": 604_800,
        "never": 0,
    }
    channels: Final[tuple[str, ...]] = ("stable", "prerelease")
    notice_interval: Final[int] = 86_400
    api_root: Final[str] = "https://api.github.com"
    download_timeout: Final[int] = 60
    checksum_asset: Final[str] = "SHA256SUMS.txt"
    download_directory: Final[str] = ".district-download"
    helper_name: Final[str] = ".district-update-helper.py"
    helper_source: Final[str] = """\
\"\"\"Finish a District Terminal update after the archive lock clears.\"\"\"
import json
import os
import sys
import time
from pathlib import Path

staged, target, manifest, version = sys.argv[1:5]
for _ in range(240):
    try:
        os.replace(staged, target)
        break
    except OSError:
        time.sleep(0.5)
else:
    sys.exit(1)
try:
    path = Path(manifest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\\n", encoding="utf-8")
except (OSError, ValueError):
    pass
try:
    Path(staged).parent.rmdir()
    Path(__file__).unlink()
except OSError:
    pass
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdatePolicy:
    """Hand-edited update-check policy from ``update.json``.

    Attributes:
        check: Cadence name.
        repository: ``owner/repo`` publishing releases.
        tag_prefix: Tag prefix identifying this project's releases.
        channel: ``stable`` or ``prerelease``.
        timeout_seconds: Network timeout for checks.
        env_disabled: Checks disabled via ``DISTRICT_DISABLE_UPDATE_CHECK``.
        repository_from_env: Repository came from ``DISTRICT_UPDATE_REPO``.

    """

    check: str = UpdateConstants.default_check
    repository: str = UpdateConstants.default_repository
    tag_prefix: str = UpdateConstants.default_tag_prefix
    channel: str = "stable"
    timeout_seconds: int = UpdateConstants.default_timeout
    env_disabled: bool = False
    repository_from_env: bool = False

    @classmethod
    def load(cls, *, home: Path) -> UpdatePolicy:
        """Load ``~/.district/update.json`` plus env overrides.

        Args:
            home: User home directory.

        Returns:
            Effective policy; invalid values fall back to defaults.

        """
        raw: JsonObject = _read_object(
            path=state_directory(home=home) / "update.json",
        )
        check: JsonValue = raw.get("check")
        channel: JsonValue = raw.get("channel")
        timeout: JsonValue = raw.get("timeout_seconds")
        env_repository: str = environ.get("DISTRICT_UPDATE_REPO", "").strip()
        repository: JsonValue = raw.get("repository")
        prefix: JsonValue = raw.get("tag_prefix")
        return cls(
            check=(
                check
                if isinstance(check, str)
                and check in UpdateConstants.intervals
                else UpdateConstants.default_check
            ),
            repository=(
                env_repository
                or (
                    repository
                    if isinstance(repository, str) and repository
                    else UpdateConstants.default_repository
                )
            ),
            tag_prefix=(
                prefix
                if isinstance(prefix, str) and prefix
                else UpdateConstants.default_tag_prefix
            ),
            channel=(
                channel
                if isinstance(channel, str)
                and channel in UpdateConstants.channels
                else "stable"
            ),
            timeout_seconds=(
                timeout
                if isinstance(timeout, int)
                and not isinstance(timeout, bool)
                and timeout > 0
                else UpdateConstants.default_timeout
            ),
            env_disabled=bool(environ.get("DISTRICT_DISABLE_UPDATE_CHECK")),
            repository_from_env=bool(env_repository),
        )

    @property
    def interval_seconds(self) -> int:
        """Return seconds that must pass between automatic checks.

        Returns:
            Interval in seconds.

        """
        return UpdateConstants.intervals[self.check]

    @property
    def enabled(self) -> bool:
        """Return whether automatic and forced checks are permitted.

        Returns:
            True unless disabled by file or environment.

        """
        return self.check != "never" and not self.env_disabled


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateState:
    """Machine-written record of the last check and notice.

    Attributes:
        checked_at: Epoch seconds of the last check.
        latest: Newest known version, empty when unknown.
        release_url: Web URL of that release.
        notified_at: Epoch seconds of the last notice.
        notified_version: Version announced by the last notice.

    """

    checked_at: float = 0.0
    latest: str = ""
    release_url: str = ""
    notified_at: float = 0.0
    notified_version: str = ""

    @classmethod
    def load(cls, *, home: Path) -> UpdateState:
        """Load ``update-state.json``, tolerating absence or damage.

        Args:
            home: User home directory.

        Returns:
            Stored state or an empty state.

        """
        raw: JsonObject = _read_object(
            path=state_directory(home=home) / "update-state.json",
        )
        checked: JsonValue = raw.get("checked_at")
        notified: JsonValue = raw.get("notified_at")
        return cls(
            checked_at=(
                float(checked)
                if isinstance(checked, int | float)
                and not isinstance(checked, bool)
                else 0.0
            ),
            latest=_text(value=raw.get("latest")),
            release_url=_text(value=raw.get("release_url")),
            notified_at=(
                float(notified)
                if isinstance(notified, int | float)
                and not isinstance(notified, bool)
                else 0.0
            ),
            notified_version=_text(value=raw.get("notified_version")),
        )

    def save(self, *, home: Path) -> None:
        """Persist the state privately.

        Args:
            home: User home directory.

        """
        path: Path = state_directory(home=home) / "update-state.json"
        _ = path.write_text(
            data=dumps(obj=asdict(obj=self), indent=2) + "\n",
            encoding="utf-8",
        )
        secure_private_file(path=path)


@dataclass(frozen=True, slots=True, kw_only=True)
class Asset:
    """One downloadable release file.

    Attributes:
        name: File name.
        url: Direct download URL.

    """

    name: str
    url: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Release:
    """A GitHub release matching this project's tag prefix.

    Attributes:
        tag: Full tag name.
        version: Version text after the prefix.
        url: Release web page.
        prerelease: Whether GitHub marks it a prerelease.
        assets: Attached files.

    """

    tag: str
    version: str
    url: str
    prerelease: bool
    assets: tuple[Asset, ...]


def _text(*, value: JsonValue) -> str:
    """Return a string value or an empty string.

    Args:
        value: Untrusted JSON value.

    Returns:
        The string, otherwise empty.

    """
    return value if isinstance(value, str) else ""


def _read_object(*, path: Path) -> JsonObject:
    """Read a JSON object, returning ``{}`` on any problem.

    Args:
        path: JSON file.

    Returns:
        Parsed object or an empty mapping.

    """
    if not path.is_file():
        return {}
    try:
        raw: JsonValue = cast(
            typ="JsonValue",
            val=loads(s=path.read_text(encoding="utf-8")),
        )
    except (JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return cast(typ="JsonObject", val=raw)


def parse_version(*, text: str) -> Version | None:
    """Parse the leading ``X.Y.Z`` of a version string.

    Args:
        text: Version text, optionally ``v``-prefixed or suffixed.

    Returns:
        Integer components, or None when unparseable.

    """
    found: Match[str] | None = match(
        pattern=r"v?(\d+(?:\.\d+)*)",
        string=text.strip(),
    )
    if found is None:
        return None
    return tuple(int(part) for part in found.group(1).split(sep="."))


def is_newer(*, candidate: str, current: str) -> bool:
    """Return whether ``candidate`` is strictly newer than ``current``.

    Args:
        candidate: Candidate version.
        current: Installed version.

    Returns:
        False when either side is unparseable.

    """
    newer: Version | None = parse_version(text=candidate)
    older: Version | None = parse_version(text=current)
    if newer is None or older is None:
        return False
    return newer > older


def platform_tag() -> str:
    """Return the release-asset platform tag for this machine.

    Returns:
        Tag such as ``macos-arm64`` or ``linux-x86_64``.

    """
    os_name: str = {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }.get(system(), system().lower())
    arch: str = machine().lower()
    if arch in {"arm64", "aarch64"}:
        arch = "arm64" if os_name in {"macos", "windows"} else "aarch64"
    elif arch in {"x86_64", "amd64"}:
        arch = "amd64" if os_name == "windows" else "x86_64"
    return f"{os_name}-{arch}"


def _request(*, url: str, accept: str) -> Request:
    """Build an authenticated GitHub request.

    ``urllib.request`` is imported on demand: it is the costliest import in
    this module and most invocations never touch the network.

    Args:
        url: HTTPS URL.
        accept: Accept header value.

    Returns:
        Prepared request.

    Raises:
        ValueError: When the URL is not HTTPS.

    """
    if urlparse(url=url).scheme != "https":
        msg: str = f"Refusing non-HTTPS update URL: {url}"
        raise ValueError(msg)
    headers: dict[str, str] = {
        "Accept": accept,
        "User-Agent": f"district/{Product.version}",
    }
    token: str = environ.get("GH_TOKEN") or environ.get("GITHUB_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    module: ModuleType = import_module(name="urllib.request")
    return cast(
        typ="Request",
        val=module.Request(url=url, headers=headers),
    )


def _read(*, request: Request, timeout: int) -> bytes:
    """Perform a request through an HTTPS-only opener.

    Args:
        request: Prepared request.
        timeout: Timeout in seconds.

    Returns:
        Response body.

    """
    module: ModuleType = import_module(name="urllib.request")
    build_opener: Callable[..., OpenerDirector] = cast(
        typ="Callable[..., OpenerDirector]",
        val=module.build_opener,
    )
    https_handler: type[BaseHandler] = cast(
        typ="type[BaseHandler]",
        val=module.HTTPSHandler,
    )
    opener: OpenerDirector = build_opener(https_handler())
    with opener.open(fullurl=request, timeout=timeout) as response:
        return cast(typ="bytes", val=response.read())


def fetch_json(*, url: str, policy: UpdatePolicy) -> JsonValue:
    """GET and decode a JSON document.

    Args:
        url: HTTPS URL.
        policy: Effective policy.

    Returns:
        Decoded JSON.

    """
    body: bytes = _read(
        request=_request(url=url, accept="application/vnd.github+json"),
        timeout=policy.timeout_seconds,
    )
    return cast(typ="JsonValue", val=loads(s=body.decode(encoding="utf-8")))


def fetch_bytes(*, url: str, policy: UpdatePolicy) -> bytes:
    """GET raw bytes, following redirects to release storage.

    Args:
        url: HTTPS URL.
        policy: Effective policy.

    Returns:
        Response body.

    """
    return _read(
        request=_request(url=url, accept="application/octet-stream"),
        timeout=max(policy.timeout_seconds, UpdateConstants.download_timeout),
    )


def _release_from(
    *,
    item: JsonValue,
    policy: UpdatePolicy,
) -> Release | None:
    """Convert one GitHub payload into a matching release.

    Args:
        item: Untrusted release object.
        policy: Effective policy.

    Returns:
        A release, or None when it does not qualify.

    """
    if not isinstance(item, dict):
        return None
    data: JsonObject = cast(typ="JsonObject", val=item)
    tag: JsonValue = data.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith(policy.tag_prefix):
        return None
    if data.get("draft") is True:
        return None
    prerelease: bool = data.get("prerelease") is True
    if prerelease and policy.channel == "stable":
        return None
    version: str = tag.removeprefix(policy.tag_prefix)
    if parse_version(text=version) is None:
        return None
    assets: list[Asset] = []
    raw_assets: JsonValue = data.get("assets")
    if isinstance(raw_assets, list):
        for entry in raw_assets:
            if not isinstance(entry, dict):
                continue
            info: JsonObject = cast(typ="JsonObject", val=entry)
            name: JsonValue = info.get("name")
            link: JsonValue = info.get("browser_download_url")
            if isinstance(name, str) and isinstance(link, str):
                assets.append(Asset(name=name, url=link))
    return Release(
        tag=tag,
        version=version,
        url=_text(value=data.get("html_url")),
        prerelease=prerelease,
        assets=tuple(assets),
    )


def latest_release(*, policy: UpdatePolicy) -> Release | None:
    """Return the highest-version matching release.

    Args:
        policy: Effective policy.

    Returns:
        The release, or None when none match or the lookup fails.

    """
    url: str = (
        f"{UpdateConstants.api_root}/repos/{policy.repository}"
        "/releases?per_page=20"
    )
    try:
        payload: JsonValue = fetch_json(url=url, policy=policy)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, list):
        return None
    releases: list[Release] = [
        found
        for item in payload
        if (found := _release_from(item=item, policy=policy)) is not None
    ]
    if not releases:
        return None
    return max(
        releases,
        key=lambda candidate: parse_version(text=candidate.version) or (),
    )


def select_asset(*, release: Release) -> Asset | None:
    """Pick the ``.pyz`` asset built for this platform.

    Args:
        release: Release to inspect.

    Returns:
        The matching asset, or None.

    """
    wanted: str = f"district-{release.version}-{platform_tag()}.pyz"
    return next(
        (asset for asset in release.assets if asset.name == wanted),
        None,
    )


def expected_digest(*, sums: str, name: str) -> str | None:
    """Find a file's SHA-256 in ``SHA256SUMS.txt`` text.

    Args:
        sums: Checksum manifest contents.
        name: Asset file name.

    Returns:
        Lowercase hex digest, or None when absent.

    """
    for line in sums.splitlines():
        digest, _, label = line.strip().partition(" ")
        if label.strip().lstrip("*") == name:
            return digest.lower()
    return None


def _is_stale(*, state: UpdateState, policy: UpdatePolicy) -> bool:
    """Return whether the cached check is older than the policy allows.

    Args:
        state: Cached state.
        policy: Effective policy.

    Returns:
        True when a new check is due.

    """
    return time() - state.checked_at >= policy.interval_seconds


def _record(
    *,
    home: Path,
    state: UpdateState,
    release: Release | None,
) -> UpdateState:
    """Persist the outcome of a check.

    Args:
        home: User home directory.
        state: Previous state.
        release: Newly found release, if any.

    Returns:
        The saved state.

    """
    fresh: UpdateState = replace(
        state,
        checked_at=time(),
        latest=release.version if release is not None else state.latest,
        release_url=(
            release.url if release is not None else state.release_url
        ),
    )
    fresh.save(home=home)
    return fresh


def check(*, home: Path) -> UpdateState | None:
    """Refresh cached release info when the policy says it is due.

    Args:
        home: User home directory.

    Returns:
        Current state; None when disabled or the lookup failed.

    """
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    if not policy.enabled:
        return None
    state: UpdateState = UpdateState.load(home=home)
    if not _is_stale(state=state, policy=policy):
        return state
    release: Release | None = latest_release(policy=policy)
    fresh: UpdateState = _record(home=home, state=state, release=release)
    return fresh if release is not None else None


def spawn_detached(*, args: tuple[str, ...]) -> None:
    """Start a fully detached child process.

    Args:
        args: Argument vector.

    """
    try:
        if os_name == "nt":
            detached: int = int(
                subprocess_symbols.get("DETACHED_PROCESS", 0),
            )
            process_group: int = int(
                subprocess_symbols.get("CREATE_NEW_PROCESS_GROUP", 0),
            )
            _ = Popen(
                args=args,
                stdin=DEVNULL,
                stdout=DEVNULL,
                stderr=DEVNULL,
                creationflags=detached | process_group,
            )
        else:
            _ = Popen(
                args=args,
                stdin=DEVNULL,
                stdout=DEVNULL,
                stderr=DEVNULL,
                start_new_session=True,
            )
    except OSError:
        return


def refresh_command() -> tuple[str, ...]:
    """Return the argument vector that refreshes the cache.

    Returns:
        Interpreter plus either the running archive or this module.

    """
    archive: Path = Path(argv[0]) if argv else Path()
    if archive.suffix == ".pyz" and archive.is_file():
        return (executable, str(object=archive), "--update-refresh")
    return (executable, "-m", __name__, "--update-refresh")


def maybe_refresh(*, home: Path) -> None:
    """Spawn a background refresh when the cache is stale.

    Args:
        home: User home directory.

    """
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    if not policy.enabled:
        return
    if _is_stale(state=UpdateState.load(home=home), policy=policy):
        spawn_detached(args=refresh_command())


def notify(*, home: Path, stream: TextIO | None = None) -> None:
    """Print a one-line update notice, at most daily per version.

    Args:
        home: User home directory.
        stream: Destination; stderr by default.

    """
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    state: UpdateState = UpdateState.load(home=home)
    if not policy.enabled or not is_newer(
        candidate=state.latest,
        current=Product.version,
    ):
        return
    now: float = time()
    if (
        state.notified_version == state.latest
        and now - state.notified_at < UpdateConstants.notice_interval
    ):
        return
    target: TextIO = stream if stream is not None else stderr
    _ = target.write(
        f"update available: district {Product.version} → "
        f'{state.latest} (run "{Product.command} --update")\n',
    )
    replace(
        state,
        notified_at=now,
        notified_version=state.latest,
    ).save(home=home)


def check_updates(*, home: Path, stream: TextIO | None = None) -> int:
    """Force a check and report the result for ``--check-updates``.

    Args:
        home: User home directory.
        stream: Destination; stdout-like stream required by callers.

    Returns:
        0 on success or when disabled, 1 when the lookup fails.

    """
    target: TextIO = stream if stream is not None else stdout
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    if not policy.enabled:
        _ = target.write("update checks are disabled\n")
        return 0
    release: Release | None = latest_release(policy=policy)
    if release is None:
        _ = target.write(
            "could not check for updates "
            f"(no matching release in {policy.repository}, or offline)\n",
        )
        return 1
    _ = _record(home=home, state=UpdateState.load(home=home), release=release)
    if is_newer(candidate=release.version, current=Product.version):
        _ = target.write(
            f"update available: {release.version} — "
            f'run "{Product.command} --update"\n',
        )
    else:
        _ = target.write(f"up to date ({Product.version})\n")
    return 0


def release_for_version(
    *,
    policy: UpdatePolicy,
    version: str,
) -> Release | None:
    """Look up one specific release by version.

    Args:
        policy: Effective policy.
        version: Version without the tag prefix.

    Returns:
        The release, or None when missing or the lookup fails.

    """
    url: str = (
        f"{UpdateConstants.api_root}/repos/{policy.repository}"
        f"/releases/tags/{policy.tag_prefix}{version}"
    )
    try:
        payload: JsonValue = fetch_json(url=url, policy=policy)
    except (OSError, ValueError):
        return None
    return _release_from(
        item=payload,
        policy=replace(policy, channel="prerelease"),
    )


def replace_file(*, source: Path, target: Path) -> None:
    """Atomically move ``source`` over ``target``.

    Args:
        source: Staged file.
        target: Installed file.

    """
    _ = source.replace(target=target)


def _hand_off(
    *,
    staged: Path,
    target: Path,
    manifest: Path | None,
    version: str,
    state_dir: Path,
) -> None:
    """Delegate the final swap to a detached helper process.

    Args:
        staged: Verified download.
        target: Archive to replace.
        manifest: Install manifest to stamp, if any.
        version: New version.
        state_dir: Directory that receives the helper script.

    """
    helper: Path = state_dir / UpdateConstants.helper_name
    _ = helper.write_text(
        data=UpdateConstants.helper_source,
        encoding="utf-8",
    )
    spawn_detached(
        args=(
            executable,
            str(object=helper),
            str(object=staged),
            str(object=target),
            str(object=manifest) if manifest is not None else "",
            version,
        ),
    )


def _verified_download(
    *,
    release: Release,
    policy: UpdatePolicy,
    directory: Path,
    out: TextIO,
) -> Path | None:
    """Download this platform's archive and verify its checksum.

    Args:
        release: Release to install.
        policy: Effective policy.
        directory: Staging directory.
        out: Message stream.

    Returns:
        The staged archive, or None after reporting a failure.

    """
    asset: Asset | None = select_asset(release=release)
    if asset is None:
        _ = out.write(
            f"no {platform_tag()} build in release {release.version}\n",
        )
        return None
    sums: Asset | None = next(
        (
            item
            for item in release.assets
            if item.name == UpdateConstants.checksum_asset
        ),
        None,
    )
    if sums is None:
        _ = out.write(
            f"release {release.version} has no "
            f"{UpdateConstants.checksum_asset}; "
            "refusing to install an unverified archive\n",
        )
        return None
    try:
        manifest_text: str = fetch_bytes(url=sums.url, policy=policy).decode(
            encoding="utf-8",
            errors="replace",
        )
        payload: bytes = fetch_bytes(url=asset.url, policy=policy)
    except (OSError, ValueError) as error:
        _ = out.write(f"download failed: {error}\n")
        return None
    expected: str | None = expected_digest(sums=manifest_text, name=asset.name)
    if expected is None or sha256(data=payload).hexdigest() != expected:
        _ = out.write(f"checksum mismatch for {asset.name}; not installing\n")
        return None
    directory.mkdir(parents=True, exist_ok=True)
    staged: Path = directory / f"{asset.name}.part"
    _ = staged.write_bytes(data=payload)
    if not is_zipfile(filename=staged):
        _ = out.write(f"{asset.name} is not a valid archive\n")
        staged.unlink()
        return None
    return staged


def is_frozen() -> bool:
    """Return whether this is a PyInstaller (``stationer``) build.

    Returns:
        Whether the interpreter carries the frozen attribute.

    """
    return bool(getattr(import_module(name="sys"), "frozen", False))


def _locate_target(
    *,
    layout: Layout | None,
    archive: Path | None,
    out: TextIO,
) -> Path | int:
    """Decide which archive an update would replace.

    Args:
        layout: Recorded installation, if any.
        archive: Archive currently running, if any.
        out: Message stream.

    Returns:
        The archive path, or ``2`` after explaining why none is updatable.

    """
    if layout is not None and layout.mode == "live":
        _ = out.write(
            f"Live install points at {layout.source_root}; update with "
            '"git pull" or reinstall with "run install --mode copy".\n',
        )
        return 2
    if layout is not None:
        return layout.pyz_path
    if archive is None:
        _ = out.write(
            "This is a native installer build; install the newer package "
            "from the release page.\n"
            if is_frozen()
            else 'Running from a source checkout; update with "git pull".\n',
        )
        return 2
    return archive


def _choose_release(
    *,
    policy: UpdatePolicy,
    requested: str | None,
    out: TextIO,
) -> Release | int:
    """Pick the release to install.

    Args:
        policy: Effective policy.
        requested: Specific version, or None for the latest.
        out: Message stream.

    Returns:
        The release, ``0`` when already current, or ``1`` when none exists.

    """
    release: Release | None = (
        release_for_version(policy=policy, version=requested)
        if requested is not None
        else latest_release(policy=policy)
    )
    if release is None:
        _ = out.write(
            f"could not find a release in {policy.repository} (or offline)\n",
        )
        return 1
    if requested is None and not is_newer(
        candidate=release.version,
        current=Product.version,
    ):
        _ = out.write(f"up to date ({Product.version})\n")
        return 0
    return release


def _record_install(*, layout: Layout, version: str) -> None:
    """Switch a recorded install to ``pyz`` mode at ``version``.

    Args:
        layout: Previously recorded layout.
        version: Newly installed version.

    """
    installer: ModuleType = import_module(
        name=".installer",
        package=__package__,
    )
    converted: Layout = replace(
        layout,
        mode="pyz",
        source_root=layout.pyz_path,
    )
    installer.write_launcher(layout=converted)
    installer.write_manifest(
        layout=converted,
        keys=frozenset(
            installer.read_manifest(layout=layout).get("components", []),
        ),
        version=version,
    )


def run_update(
    *,
    home: Path,
    requested: str | None = None,
    stream: TextIO | None = None,
) -> int:
    """Replace the installed archive with a released build.

    Args:
        home: User home directory.
        requested: Specific version to install instead of the latest.
        stream: Message destination; stdout by default.

    Returns:
        0 on success or when current, 1 on failure, 2 for live installs.

    """
    installer: ModuleType = import_module(
        name=".installer",
        package=__package__,
    )
    out: TextIO = stream if stream is not None else stdout
    layout: Layout | None = installer.manifest_layout(home=home)
    located: Path | int = _locate_target(
        layout=layout,
        archive=installer.running_archive(),
        out=out,
    )
    if isinstance(located, int):
        return located
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    release: Release | int = _choose_release(
        policy=policy,
        requested=requested,
        out=out,
    )
    if isinstance(release, int):
        return release
    directory: Path = located.parent / UpdateConstants.download_directory
    staged: Path | None = _verified_download(
        release=release,
        policy=policy,
        directory=directory,
        out=out,
    )
    if staged is None:
        rmtree(path=directory, ignore_errors=True)
        return 1
    try:
        replace_file(source=staged, target=located)
    except OSError:
        _hand_off(
            staged=staged,
            target=located,
            manifest=layout.manifest_path if layout is not None else None,
            version=release.version,
            state_dir=state_directory(home=home),
        )
        _ = out.write(
            f"completing the update to {release.version} in the "
            "background; run the command again in a moment\n",
        )
        return 0
    rmtree(path=directory, ignore_errors=True)
    if layout is not None:
        _record_install(layout=layout, version=release.version)
    _ = out.write(
        f"updated district {Product.version} → {release.version}\n",
    )
    return 0


def main(
    *,
    arguments: tuple[str, ...] | None = None,
    home: Path | None = None,
) -> int:
    """Handle the internal ``--update-refresh`` entry point.

    Args:
        arguments: Arguments; defaults to ``sys.argv``.
        home: User home directory override.

    Returns:
        Exit status.

    """
    args: tuple[str, ...] = tuple(argv[1:]) if arguments is None else arguments
    base: Path = home or Path(environ.get("HOME", str(object=Path.home())))
    if args == ("--update-refresh",):
        _ = check(home=base)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
