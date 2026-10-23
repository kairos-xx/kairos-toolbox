# Copyright (c) 2026 Project contributors.
"""Tests for version checks, self-update, and update notices."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO, StringIO
from json import dumps, loads
from subprocess import CompletedProcess, run
from sys import executable
from time import time
from typing import TYPE_CHECKING, Final, final
from zipfile import ZipFile

import pytest

from tools.lake_terminal.lake_terminal import installer as installer_module
from tools.lake_terminal.lake_terminal import update
from tools.lake_terminal.lake_terminal.installer import Layout, write_manifest
from tools.lake_terminal.lake_terminal.product import Product, state_directory
from tools.lake_terminal.lake_terminal.update import (
    UpdatePolicy,
    UpdateState,
    is_newer,
    latest_release,
    parse_version,
    platform_tag,
    select_asset,
)

from .helpers import check, expect, present

if TYPE_CHECKING:
    from pathlib import Path
    from subprocess import CompletedProcess

    from tools.lake_terminal.lake_terminal.update import Release


@final
class Constants:
    """Group module-level constants.

    Attributes:
        repo: Grouped module constant.

    """

    repo: Final[str] = "kairos-xx/vs_code_compatible_plugins_tools"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Return a scratch home directory.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        The scratch home.

    """
    return tmp_path


def release(
    *,
    version: str,
    prerelease: bool = False,
    draft: bool = False,
    prefix: str = "lake-terminal/v",
    assets: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build one GitHub release payload.

    Args:
        version: Semantic version after the tag prefix.
        prerelease: Prerelease flag.
        draft: Draft flag.
        prefix: Tag prefix.
        assets: Asset file names.

    Returns:
        Release JSON object.

    """
    return {
        "tag_name": f"{prefix}{version}",
        "html_url": f"https://example.test/releases/{version}",
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://example.test/{name}",
            }
            for name in assets
        ],
    }


def write_policy(*, home: Path, **values: object) -> None:
    """Write ``update.json`` under the state directory.

    Args:
        home: Home directory.
        **values: Policy fields.
        values: Test parameter.

    """
    path: Path = state_directory(home=home) / "update.json"
    _ = path.write_text(data=dumps(obj=values), encoding="utf-8")


def test_parse_version_orders_numerically() -> None:
    """Compare components as integers, not strings."""
    expect(
        actual=(parse_version(text="1.10.0") or ())
        > (parse_version(text="1.9.9") or ()),
        expected=True,
    )
    expect(actual=parse_version(text="v2.0.1"), expected=(2, 0, 1))
    expect(actual=parse_version(text="1.2.3rc1"), expected=(1, 2, 3))
    expect(actual=parse_version(text="garbage"), expected=None)


def test_is_newer_handles_unparseable_values() -> None:
    """Never report an update for unparseable versions."""
    expect(actual=is_newer(candidate="1.0.1", current="1.0.0"), expected=True)
    expect(actual=is_newer(candidate="1.0.0", current="1.0.0"), expected=False)
    expect(actual=is_newer(candidate="nope", current="1.0.0"), expected=False)


def test_policy_defaults(home: Path) -> None:
    """Defaults apply when no update.json exists.

    Args:
        home: Scratch Lake home directory.

    """
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    expect(actual=policy.check, expected="daily")
    expect(actual=policy.repository, expected=Constants.repo)
    expect(actual=policy.tag_prefix, expected="lake-terminal/v")
    expect(actual=policy.channel, expected="stable")
    expect(actual=policy.timeout_seconds, expected=2)
    expect(actual=policy.interval_seconds, expected=86400)
    expect(actual=policy.enabled, expected=True)


def test_policy_file_overrides(home: Path) -> None:
    """Valid file values replace defaults; invalid ones fall back.

    Args:
        home: Scratch Lake home directory.

    """
    write_policy(
        home=home,
        check="hourly",
        repository="a/b",
        tag_prefix="x/v",
        channel="prerelease",
        timeout_seconds=5,
    )
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    expect(actual=policy.interval_seconds, expected=3600)
    expect(actual=policy.repository, expected="a/b")
    expect(actual=policy.tag_prefix, expected="x/v")
    expect(actual=policy.channel, expected="prerelease")
    expect(actual=policy.timeout_seconds, expected=5)
    write_policy(home=home, check="sometimes", channel="nightly")
    fallback: UpdatePolicy = UpdatePolicy.load(home=home)
    expect(actual=fallback.check, expected="daily")
    expect(actual=fallback.channel, expected="stable")


def test_policy_ignores_corrupt_file(home: Path) -> None:
    """A hand-edited broken file never raises.

    Args:
        home: Scratch Lake home directory.

    """
    path: Path = state_directory(home=home) / "update.json"
    _ = path.write_text(data="{not json", encoding="utf-8")
    expect(actual=UpdatePolicy.load(home=home).check, expected="daily")


def test_policy_environment_overrides(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment variables win over the file.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    write_policy(home=home, repository="file/repo")
    monkeypatch.setenv(name="LAKE_UPDATE_REPO", value="env/repo")
    monkeypatch.setenv(name="LAKE_DISABLE_UPDATE_CHECK", value="1")
    policy: UpdatePolicy = UpdatePolicy.load(home=home)
    expect(actual=policy.repository, expected="env/repo")
    expect(actual=policy.enabled, expected=False)


def test_policy_never_disables(home: Path) -> None:
    """The ``never`` setting disables checks.

    Args:
        home: Scratch Lake home directory.

    """
    write_policy(home=home, check="never")
    expect(actual=UpdatePolicy.load(home=home).enabled, expected=False)


def test_state_roundtrip_is_private(home: Path) -> None:
    """State persists and tolerates a missing or corrupt file.

    Args:
        home: Scratch Lake home directory.

    """
    expect(actual=UpdateState.load(home=home).latest, expected="")
    state: UpdateState = UpdateState(
        checked_at=12.5,
        latest="1.2.0",
        release_url="u",
        notified_at=3.0,
        notified_version="1.2.0",
    )
    state.save(home=home)
    expect(actual=UpdateState.load(home=home), expected=state)
    path: Path = state_directory(home=home) / "update-state.json"
    _ = path.write_text(data="[]", encoding="utf-8")
    expect(actual=UpdateState.load(home=home).latest, expected="")


def test_platform_tag_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Map system and machine names onto asset tags.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    """
    cases: dict[tuple[str, str], str] = {
        ("Darwin", "arm64"): "macos-arm64",
        ("Darwin", "x86_64"): "macos-x86_64",
        ("Linux", "x86_64"): "linux-x86_64",
        ("Linux", "aarch64"): "linux-aarch64",
        ("Windows", "AMD64"): "windows-amd64",
    }
    for (system, machine), expected in cases.items():
        monkeypatch.setattr(
            target=update,
            name="system",
            value=lambda s=system: s,
        )
        monkeypatch.setattr(
            target=update,
            name="machine",
            value=lambda m=machine: m,
        )
        expect(actual=platform_tag(), expected=expected)


def test_latest_release_filters_and_picks_max(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filter by prefix/draft/prerelease and take the highest semver.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    seen: list[str] = []

    def fake(*, url: str, policy: UpdatePolicy) -> object:
        """Return fake.

        Args:
            url: Url.
            policy: Policy.

        Returns:
            The resulting value.

        """
        seen.append(url)
        _ = policy
        return [
            release(version="1.1.0"),
            release(version="1.10.0"),
            release(version="1.9.0"),
            release(version="9.0.0", draft=True),
            release(version="8.0.0", prerelease=True),
            release(version="7.0.0", prefix="other/v"),
        ]

    monkeypatch.setattr(target=update, name="fetch_json", value=fake)
    found: Release | None = latest_release(policy=UpdatePolicy.load(home=home))
    check(condition=found is not None, reason="release expected")
    found = present(value=found)
    expect(actual=found.version, expected="1.10.0")
    check(
        condition=f"/repos/{Constants.repo}/releases" in seen[0],
        reason=seen[0],
    )
    write_policy(home=home, channel="prerelease")
    pre: Release | None = latest_release(policy=UpdatePolicy.load(home=home))
    pre = present(value=pre)
    expect(actual=pre.version, expected="8.0.0")


def test_latest_release_returns_none_on_garbage(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-list payloads and network errors yield no release.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def garbage(**_: object) -> object:
        """Return garbage.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return {"message": "x"}

    monkeypatch.setattr(target=update, name="fetch_json", value=garbage)
    expect(
        actual=latest_release(policy=UpdatePolicy.load(home=home)),
        expected=None,
    )

    def boom(**_: object) -> object:
        """Return boom.

        Args:
            _: _.

        Returns:
            The resulting value.

        Raises:
            OSError: When the operation cannot complete.

        """
        msg: str = "offline"
        raise OSError(msg)

    monkeypatch.setattr(target=update, name="fetch_json", value=boom)
    expect(
        actual=latest_release(policy=UpdatePolicy.load(home=home)),
        expected=None,
    )


def test_select_asset_matches_platform(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pick the asset named for this platform.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    monkeypatch.setattr(
        target=update,
        name="platform_tag",
        value=lambda: "linux-x86_64",
    )
    names: tuple[str, ...] = (
        "lake-terminal-1.2.0-macos-arm64.pyz",
        "lake-terminal-1.2.0-linux-x86_64.pyz",
        "SHA256SUMS.txt",
    )

    def fake(**_: object) -> object:
        """Return fake.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return [release(version="1.2.0", assets=names)]

    monkeypatch.setattr(target=update, name="fetch_json", value=fake)
    found: update.Release | None = latest_release(
        policy=UpdatePolicy.load(home=home),
    )
    found = present(value=found)
    asset: update.Asset | None = select_asset(release=found)
    asset = present(value=asset)
    expect(actual=asset.name, expected=names[1])
    expect(actual=asset.url, expected="https://example.test/" + names[1])
    monkeypatch.setattr(
        target=update,
        name="platform_tag",
        value=lambda: "windows-amd64",
    )
    expect(actual=select_asset(release=found), expected=None)


def test_check_writes_state_and_respects_throttle(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch when stale, skip when fresh, skip when disabled.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    calls: list[str] = []

    def fake(*, url: str, policy: UpdatePolicy) -> object:
        """Return fake.

        Args:
            url: Url.
            policy: Policy.

        Returns:
            The resulting value.

        """
        calls.append(url)
        _ = policy
        return [release(version="2.0.0")]

    monkeypatch.setattr(target=update, name="fetch_json", value=fake)
    state: UpdateState | None = update.check(home=home)
    state = present(value=state)
    expect(actual=state.latest, expected="2.0.0")
    expect(actual=len(calls), expected=1)
    expect(actual=UpdateState.load(home=home).latest, expected="2.0.0")
    _ = update.check(home=home)
    expect(actual=len(calls), expected=1)
    write_policy(home=home, check="always")
    _ = update.check(home=home)
    expect(actual=len(calls), expected=2)
    write_policy(home=home, check="never")
    expect(actual=update.check(home=home), expected=None)
    expect(actual=len(calls), expected=2)


def test_check_is_silent_when_offline(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network failures never raise from the throttled check.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def boom(**_: object) -> object:
        """Return boom.

        Args:
            _: _.

        Returns:
            The resulting value.

        Raises:
            OSError: When the operation cannot complete.

        """
        msg: str = "offline"
        raise OSError(msg)

    monkeypatch.setattr(target=update, name="fetch_json", value=boom)
    expect(actual=update.check(home=home), expected=None)


def test_notify_once_per_day_per_version(home: Path) -> None:
    """Announce a newer cached version once, then stay quiet.

    Args:
        home: Scratch Lake home directory.

    """
    UpdateState(checked_at=time(), latest="9.9.9").save(home=home)
    first: StringIO = StringIO()
    update.notify(home=home, stream=first)
    check(
        condition=f"update available: lake-terminal {Product.version} → 9.9.9"
        in first.getvalue(),
        reason=first.getvalue(),
    )
    second: StringIO = StringIO()
    update.notify(home=home, stream=second)
    expect(actual=second.getvalue(), expected="")
    UpdateState(
        checked_at=time(),
        latest="9.9.9",
        notified_at=time() - 90000,
        notified_version="9.9.9",
    ).save(home=home)
    third: StringIO = StringIO()
    update.notify(home=home, stream=third)
    check(condition="9.9.9" in third.getvalue(), reason="daily repeat")


def test_notify_silent_when_current_or_disabled(home: Path) -> None:
    """No notice for equal versions or a disabled policy.

    Args:
        home: Scratch Lake home directory.

    """
    UpdateState(checked_at=time(), latest=Product.version).save(home=home)
    out: StringIO = StringIO()
    update.notify(home=home, stream=out)
    expect(actual=out.getvalue(), expected="")
    UpdateState(checked_at=time(), latest="9.9.9").save(home=home)
    write_policy(home=home, check="never")
    update.notify(home=home, stream=out)
    expect(actual=out.getvalue(), expected="")


def test_maybe_refresh_spawns_only_when_stale(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spawn a detached refresh for stale state only.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    spawned: list[tuple[str, ...]] = []

    def fake_spawn(*, args: tuple[str, ...]) -> None:
        """Handle fake spawn.

        Args:
            args: Args.

        """
        spawned.append(args)

    monkeypatch.setattr(target=update, name="spawn_detached", value=fake_spawn)
    update.maybe_refresh(home=home)
    expect(actual=len(spawned), expected=1)
    check(
        condition=spawned[0][-1] == "--update-refresh",
        reason=str(object=spawned),
    )
    UpdateState(checked_at=time(), latest="1.0.0").save(home=home)
    update.maybe_refresh(home=home)
    expect(actual=len(spawned), expected=1)


def test_refresh_entry_writes_state(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The internal refresh entry point refreshes cached state.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def fake(**_: object) -> object:
        """Return fake.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return [release(version="3.0.0")]

    monkeypatch.setattr(target=update, name="fetch_json", value=fake)
    expect(
        actual=update.main(arguments=("--update-refresh",), home=home),
        expected=0,
    )
    expect(actual=UpdateState.load(home=home).latest, expected="3.0.0")


def test_check_updates_output_matrix(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up to date, available, offline, and disabled outcomes.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    out: StringIO = StringIO()

    def fake(**_: object) -> object:
        """Return fake.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return [release(version=Product.version)]

    monkeypatch.setattr(target=update, name="fetch_json", value=fake)
    expect(actual=update.check_updates(home=home, stream=out), expected=0)
    check(
        condition=f"up to date ({Product.version})" in out.getvalue(),
        reason=out.getvalue(),
    )
    out = StringIO()

    def newer(**_: object) -> object:
        """Return newer.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return [release(version="9.0.0")]

    monkeypatch.setattr(target=update, name="fetch_json", value=newer)
    expect(actual=update.check_updates(home=home, stream=out), expected=0)
    check(
        condition='update available: 9.0.0 — run "lake --update"'
        in out.getvalue(),
        reason=out.getvalue(),
    )

    def boom(**_: object) -> object:
        """Return boom.

        Args:
            _: _.

        Returns:
            The resulting value.

        Raises:
            OSError: When the operation cannot complete.

        """
        msg: str = "offline"
        raise OSError(msg)

    monkeypatch.setattr(target=update, name="fetch_json", value=boom)
    out = StringIO()
    expect(actual=update.check_updates(home=home, stream=out), expected=1)
    check(condition="could not check" in out.getvalue(), reason=out.getvalue())
    write_policy(home=home, check="never")
    out = StringIO()
    expect(actual=update.check_updates(home=home, stream=out), expected=0)
    check(condition="disabled" in out.getvalue(), reason=out.getvalue())


def test_sha256_helper_matches_hashlib() -> None:
    """Checksum parsing finds the line for one file name."""
    digest: str = sha256(data=b"payload").hexdigest()
    sums: str = f"{digest}  lake-terminal-1.0.0-linux-x86_64.pyz\nabc  other\n"
    expect(
        actual=update.expected_digest(
            sums=sums,
            name="lake-terminal-1.0.0-linux-x86_64.pyz",
        ),
        expected=digest,
    )
    expect(
        actual=update.expected_digest(sums=sums, name="missing"),
        expected=None,
    )


def zip_bytes(*, marker: str) -> bytes:
    """Return the bytes of a tiny valid zip archive.

    Args:
        marker: Text stored in the archive so contents differ.

    Returns:
        Zip file bytes.

    """
    buffer: BytesIO = BytesIO()
    with ZipFile(file=buffer, mode="w") as bundle:
        bundle.writestr(zinfo_or_arcname="__main__.py", data=marker)
    return buffer.getvalue()


class FakeGitHub:
    """Serve one release and its assets through the fetch seams.

    Attributes:
        payload: Archive bytes served for the platform asset.
        sums_digest: Digest advertised in SHA256SUMS.txt.
        urls: Every URL requested, in order.

    """

    def __init__(
        self,
        *,
        version: str = "2.0.0",
        payload: bytes | None = None,
        sums_digest: str | None = None,
        with_sums: bool = True,
        with_asset: bool = True,
    ) -> None:
        """Create the fake server.

        Args:
            version: Release version.
            payload: Archive bytes; generated when omitted.
            sums_digest: Digest override for tamper tests.
            with_sums: Whether to publish SHA256SUMS.txt.
            with_asset: Whether to publish the platform asset.

        """
        self.version: str = version
        self.payload: bytes = payload or zip_bytes(marker=version)
        self.name: str = f"lake-terminal-{version}-{platform_tag()}.pyz"
        self.sums_digest: str = (
            sums_digest or sha256(data=self.payload).hexdigest()
        )
        self.urls: list[str] = []
        names: list[str] = []
        if with_asset:
            names.append(self.name)
        if with_sums:
            names.append("SHA256SUMS.txt")
        self.release: dict[str, object] = release(
            version=version,
            assets=tuple(names),
        )

    def json(self, *, url: str, policy: UpdatePolicy) -> object:
        """Answer release listing and tag lookups.

        Args:
            url: Requested URL.
            policy: Ignored policy.

        Returns:
            Release JSON.

        """
        _ = policy
        self.urls.append(url)
        return self.release if "/tags/" in url else [self.release]

    def blob(self, *, url: str, policy: UpdatePolicy) -> bytes:
        """Answer asset downloads.

        Args:
            url: Requested URL.
            policy: Ignored policy.

        Returns:
            File bytes.

        Raises:
            OSError: For unknown URLs.

        """
        _ = policy
        self.urls.append(url)
        if url.endswith("SHA256SUMS.txt"):
            return f"{self.sums_digest}  {self.name}\n".encode()
        if url.endswith(self.name):
            return self.payload
        msg: str = f"404 {url}"
        raise OSError(msg)

    def install(self, *, monkeypatch: pytest.MonkeyPatch) -> None:
        """Route the update module's network seams here.

        Args:
            monkeypatch: Pytest monkeypatch.

        """
        monkeypatch.setattr(target=update, name="fetch_json", value=self.json)
        monkeypatch.setattr(target=update, name="fetch_bytes", value=self.blob)


def install_manifest(*, home: Path, mode: str, tmp: Path) -> Layout:
    """Write an install manifest for a scratch layout.

    Args:
        home: Home directory.
        mode: Install mode.
        tmp: Scratch root.

    Returns:
        The layout recorded.

    """
    layout: Layout = Layout(
        home=home,
        bin_dir=tmp / "bin",
        lib_dir=tmp / "lib",
        alias="laketest",
        mode=mode,
        source_root=tmp / "src",
        python=executable,
    )
    layout.lib_dir.mkdir(parents=True)
    write_manifest(layout=layout, keys=frozenset({"launcher"}))
    return layout


def test_update_refuses_live_install(home: Path, tmp_path: Path) -> None:
    """Live installs point at a checkout and are never rewritten.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.

    """
    _: Layout = install_manifest(home=home, mode="live", tmp=tmp_path)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=2)
    check(condition="git pull" in out.getvalue(), reason=out.getvalue())
    check(condition="--mode copy" in out.getvalue(), reason=out.getvalue())
    check(
        condition=str(object=tmp_path / "src") in out.getvalue(),
        reason="path",
    )


def test_update_refuses_bare_checkout(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a manifest or archive there is nothing to replace.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def no_archive(**_: object) -> Path | None:
        """Return no archive.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return None

    monkeypatch.setattr(
        target=installer_module,
        name="running_archive",
        value=no_archive,
    )
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=2)
    check(condition="git pull" in out.getvalue(), reason=out.getvalue())


def test_update_copy_install_becomes_pyz(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download, verify, install the archive, and switch the launcher.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = install_manifest(home=home, mode="copy", tmp=tmp_path)
    github: FakeGitHub = FakeGitHub()
    github.install(monkeypatch=monkeypatch)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=0)
    expect(
        actual=(layout.lib_dir / "lake-terminal.pyz").read_bytes(),
        expected=github.payload,
    )
    manifest: dict[str, object] = loads(
        s=layout.manifest_path.read_text(encoding="utf-8"),
    )
    expect(actual=manifest["mode"], expected="pyz")
    expect(actual=manifest["version"], expected="2.0.0")
    expect(actual=manifest["payload"], expected="lake-terminal.pyz")
    expect(actual=manifest["components"], expected=["launcher"])
    launcher: str = layout.launcher.read_text(encoding="utf-8")
    check(condition="lake-terminal.pyz" in launcher, reason=launcher)
    check(
        condition=not (layout.lib_dir / ".lake-download").exists(),
        reason="download directory cleaned",
    )
    check(condition="2.0.0" in out.getvalue(), reason=out.getvalue())


def test_update_checksum_mismatch_leaves_payload(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad digest aborts before touching the installed archive.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = install_manifest(home=home, mode="pyz", tmp=tmp_path)
    layout.pyz_path.write_bytes(data=b"old")
    FakeGitHub(sums_digest="0" * 64).install(monkeypatch=monkeypatch)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=1)
    expect(actual=layout.pyz_path.read_bytes(), expected=b"old")
    check(condition="checksum" in out.getvalue(), reason=out.getvalue())
    check(
        condition=not (layout.lib_dir / ".lake-download").exists(),
        reason="download directory cleaned",
    )


def test_update_requires_checksum_manifest(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse to install an archive that has no published checksum.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = install_manifest(home=home, mode="pyz", tmp=tmp_path)
    layout.pyz_path.write_bytes(data=b"old")
    FakeGitHub(with_sums=False).install(monkeypatch=monkeypatch)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=1)
    expect(actual=layout.pyz_path.read_bytes(), expected=b"old")


def test_update_reports_missing_platform_asset(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail clearly when no asset matches this platform.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _: Layout = install_manifest(home=home, mode="pyz", tmp=tmp_path)
    FakeGitHub(with_asset=False).install(monkeypatch=monkeypatch)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=1)
    check(condition=platform_tag() in out.getvalue(), reason=out.getvalue())


def test_update_noop_when_current(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is downloaded when already on the latest version.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    _ = install_manifest(home=home, mode="pyz", tmp=tmp_path)
    github: FakeGitHub = FakeGitHub(version=Product.version)
    github.install(monkeypatch=monkeypatch)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=0)
    check(condition="up to date" in out.getvalue(), reason=out.getvalue())
    check(
        condition=not any(url.endswith(".pyz") for url in github.urls),
        reason=str(object=github.urls),
    )


def test_update_requested_version_uses_tag_lookup(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit version resolves its own tag and may downgrade.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = install_manifest(home=home, mode="pyz", tmp=tmp_path)
    github: FakeGitHub = FakeGitHub(version="0.9.0")
    github.install(monkeypatch=monkeypatch)
    expect(
        actual=update.run_update(
            home=home,
            requested="0.9.0",
            stream=StringIO(),
        ),
        expected=0,
    )
    check(
        condition=any(
            "/tags/lake-terminal%2Fv0.9.0" in u
            or "/tags/lake-terminal/v0.9.0" in u
            for u in github.urls
        ),
        reason=str(object=github.urls),
    )
    expect(actual=layout.pyz_path.read_bytes(), expected=github.payload)


def test_update_self_replaces_bare_archive(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no install manifest, a running archive replaces itself.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    running: Path = tmp_path / "lake.pyz"
    running.write_bytes(data=zip_bytes(marker="old"))

    def fake_archive(**_: object) -> Path:
        """Return fake archive.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return running

    monkeypatch.setattr(
        target=installer_module,
        name="running_archive",
        value=fake_archive,
    )
    github: FakeGitHub = FakeGitHub()
    github.install(monkeypatch=monkeypatch)
    expect(actual=update.run_update(home=home, stream=StringIO()), expected=0)
    expect(actual=running.read_bytes(), expected=github.payload)


def test_update_locked_archive_falls_back_to_helper(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed in-place replace hands off to a detached helper.

    Args:
        home: Scratch Lake home directory.
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    """
    layout: Layout = install_manifest(home=home, mode="pyz", tmp=tmp_path)
    layout.pyz_path.write_bytes(data=b"old")
    github: FakeGitHub = FakeGitHub()
    github.install(monkeypatch=monkeypatch)

    def locked(*, source: Path, target: Path) -> None:
        """Handle locked.

        Args:
            source: Source.
            target: Target.

        Raises:
            PermissionError: When the operation cannot complete.

        """
        _: tuple[Path, Path] = (source, target)
        msg: str = "file is locked"
        raise PermissionError(msg)

    spawned: list[tuple[str, ...]] = []
    monkeypatch.setattr(target=update, name="replace_file", value=locked)

    def fake_spawn(*, args: tuple[str, ...]) -> None:
        """Handle fake spawn.

        Args:
            args: Args.

        """
        spawned.append(args)

    monkeypatch.setattr(target=update, name="spawn_detached", value=fake_spawn)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=0)
    check(condition="background" in out.getvalue(), reason=out.getvalue())
    expect(actual=len(spawned), expected=1)
    helper: Path = layout.state_dir / ".lake-update-helper.py"
    check(condition=helper.is_file(), reason="helper written")
    expect(actual=layout.pyz_path.read_bytes(), expected=b"old")
    done: CompletedProcess[str] = run(
        args=[executable, "-I", *spawned[0][1:]],
        check=False,
        capture_output=True,
        text=True,
    )
    expect(actual=done.returncode, expected=0)
    expect(actual=layout.pyz_path.read_bytes(), expected=github.payload)
    manifest: dict[str, object] = loads(
        s=layout.manifest_path.read_text(encoding="utf-8"),
    )
    expect(actual=manifest["version"], expected="2.0.0")
    check(condition=not helper.exists(), reason="helper removes itself")


def test_update_explains_native_installer_builds(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen release_builder install is updated by its package.

    Args:
        home: Scratch Lake home directory.
        monkeypatch: Pytest monkeypatch fixture.

    """

    def _no_archive(**_: object) -> Path | None:
        """Return no archive.

        Args:
            _: _.

        Returns:
            The resulting value.

        """
        return None

    def _frozen() -> bool:
        """Return frozen.

        Returns:
            The resulting value.

        """
        return True

    monkeypatch.setattr(
        target=installer_module,
        name="running_archive",
        value=_no_archive,
    )
    monkeypatch.setattr(target=update, name="is_frozen", value=_frozen)
    out: StringIO = StringIO()
    expect(actual=update.run_update(home=home, stream=out), expected=2)
    check(
        condition="native installer" in out.getvalue(),
        reason=out.getvalue(),
    )
    check(condition="git pull" not in out.getvalue(), reason=out.getvalue())
